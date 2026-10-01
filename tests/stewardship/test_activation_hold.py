"""A configuration change in its activation window is a brief hold (#429)."""

import logging
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import HttpRequest, HttpResponse

from parishkit.config import ConfigError
from parishkit.logging import STRUCTURED_EXTRA_FIELD
from parishkit.stewardship import activation_hold, request_scope
from parishkit.stewardship import runtime_background as background
from parishkit.stewardship.accounts.authority import (
    AuthorityChanging,
    authority_mismatch,
)
from parishkit.stewardship.jobs import dispatch, lifetime
from parishkit.stewardship.jobs.dispatch import Execution, Handler, WorkQueue
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.observability import Event as LogEvent
from parishkit.stewardship.source.errors import (
    SourceAuthorityChanging,
    local_read_admission,
)
from parishkit.stewardship.source.failures import classify_read_failure

BASE, NEXT, OTHER = "a" * 64, "b" * 64, "c" * 64


def version(digest, predecessor):
    """A selected YAML version with just the fields authority checks read."""
    return SimpleNamespace(
        version_id=uuid4(), digest=digest, predecessor_digest=predecessor
    )


@pytest.mark.parametrize(
    "selected,active,schema,changing",
    [
        # Exactly one activation ahead: the installer is mid-apply.
        (version(NEXT, BASE), BASE, "campaign-content-v5", True),
        # Anything else still needs recovery.
        (version(NEXT, OTHER), BASE, "campaign-content-v5", False),
        (version(NEXT, None), BASE, "campaign-content-v5", False),
        (version(BASE, None), NEXT, "campaign-content-v5", False),
        (version(NEXT, BASE), None, None, False),
        (None, BASE, "campaign-content-v5", False),
        # Initial setup keeps its candidate selected over the bootstrap on
        # purpose; that is the setup hold, never a brief activation.
        (version(NEXT, BASE), BASE, "bootstrap-policy-v1", False),
    ],
)
def test_only_a_selection_one_activation_ahead_is_changing(
    selected, active, schema, changing
):
    """The predecessor digest, not any mismatch, identifies an activation."""
    error = authority_mismatch(selected, active, "synthetic", active_schema=schema)
    assert isinstance(error, ConfigError)
    assert isinstance(error, AuthorityChanging) is changing


@pytest.mark.parametrize("ahead", [True, False])
def test_matching_authority_names_the_activation_window(monkeypatch, ahead):
    """Background admission raises the hold only for the one-step selection."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    active_id = uuid4()
    selected = version(NEXT, BASE if ahead else OTHER)
    store = SimpleNamespace(
        active=lambda: selected,
        manifest_reference=lambda: (selected.version_id, selected.digest),
    )
    monkeypatch.setattr(
        SystemConfiguration.objects,
        "values_list",
        lambda *fields: SimpleNamespace(
            first=lambda: (active_id, BASE, "campaign-content-v5")
        ),
    )
    with pytest.raises(ConfigError, match="requires recovery") as caught:
        background.matching_authority(store)
    assert isinstance(caught.value, AuthorityChanging) is ahead


def test_wait_retries_a_step_until_the_activation_commits(monkeypatch):
    """A short window is invisible to the caller apart from the delay."""
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    attempts = []

    def step():
        """Meet the window twice, then see the committed activation."""
        attempts.append(1)
        if len(attempts) < 3:
            raise AuthorityChanging("synthetic")
        return "done"

    assert activation_hold.wait_out_activation(step) == "done"
    assert len(attempts) == 3


def test_wait_gives_up_at_its_limit_and_logs_the_timeout(monkeypatch, caplog):
    """An activation that never commits is handed back, with limit and elapsed."""
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0.01)
    task = uuid4()

    def step():
        """The change never finishes activating."""
        raise AuthorityChanging("synthetic")

    with (
        caplog.at_level(logging.INFO, logger="parishkit.stewardship"),
        pytest.raises(AuthorityChanging),
    ):
        activation_hold.wait_out_activation(step, limit=0.05, task_id=task)
    (record,) = [r for r in caplog.records if r.msg == LogEvent.TASK_TIMED_OUT]
    assert record.levelno == logging.WARNING
    context = getattr(record, STRUCTURED_EXTRA_FIELD)
    assert context["timeout"] == "configuration_activation"
    assert context["limit_seconds"] == 0 and context["elapsed_seconds"] == 0
    assert context["task_id"] == task


def test_wait_never_holds_an_open_transaction(monkeypatch):
    """Activation needs the work-order lock, so a transaction must not wait."""
    monkeypatch.setattr(
        activation_hold, "connection", SimpleNamespace(in_atomic_block=True)
    )
    attempts = []

    def step():
        """Count attempts; the first must be the only one."""
        attempts.append(1)
        raise AuthorityChanging("synthetic")

    with pytest.raises(AuthorityChanging):
        activation_hold.wait_out_activation(step)
    assert attempts == [1]


def synthetic_execution(admit):
    """An execution whose SQL steps are replaced; only admission is real."""
    return Execution(
        TaskClaim(uuid4(), 1, uuid4()),
        Handler(WorkQueue.GENERAL, admit, lambda *args: None),
        uuid4(),
    )


def test_effect_admission_waits_out_activation_and_runs_once(monkeypatch):
    """A refused admission is rolled back and repeated; the effect runs once."""
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    monkeypatch.setattr(dispatch, "lock_task_claim", lambda claim: None)
    monkeypatch.setattr(dispatch, "_status", lambda row: None)
    rolled_back = []

    class Atomic:
        """Record each transaction's outcome instead of opening one."""

        def __enter__(self):
            return self

        def __exit__(self, kind, value, traceback):
            rolled_back.append(kind is not None)

    monkeypatch.setattr(dispatch, "transaction", SimpleNamespace(atomic=Atomic))
    admissions = []

    def admit(action, status):
        """Refuse the first admission inside the activation window."""
        admissions.append(action)
        if len(admissions) == 1:
            raise AuthorityChanging("synthetic")
        return True

    context, ran = synthetic_execution(admit), []
    with context.effect():
        ran.append(1)
    assert admissions == ["effect", "effect"] and ran == [1]
    assert rolled_back == [True, False]


def test_transition_waits_out_activation(monkeypatch):
    """A transition is one transaction, so it is simply applied again."""
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    monkeypatch.setattr(
        dispatch, "lock_task_claim", lambda claim: SimpleNamespace(pk=1, version=1)
    )
    monkeypatch.setattr(dispatch, "transaction", SimpleNamespace(atomic=nullcontext))
    calls = []

    def change_run(**kwargs):
        """Refuse once, as change_run's admission would in the window."""
        calls.append(kwargs["action"])
        if len(calls) == 1:
            raise AuthorityChanging("synthetic")
        return SimpleNamespace(state="running")

    monkeypatch.setattr(dispatch, "change_run", change_run)
    context = synthetic_execution(lambda *args: True)
    assert context.transition("progress", progress=(1, 2)).state == "running"
    assert calls == ["progress", "progress"]
    assert not context.control.finished.is_set()


def test_renewal_retries_soon_instead_of_losing_the_lease(monkeypatch):
    """A heartbeat that meets the window must not stop the execution."""
    monkeypatch.setattr(lifetime, "PULSE_SECONDS", 0)
    monkeypatch.setattr(lifetime, "ACTIVATION_RETRY_SECONDS", 0)
    context = synthetic_execution(lambda *args: True)
    renewals = []

    def renew(execution):
        """Meet the window once, then renew and finish."""
        renewals.append(1)
        if len(renewals) == 1:
            raise AuthorityChanging("synthetic")
        execution.control.finished.set()

    monkeypatch.setattr(lifetime, "renew_once", renew)
    lifetime._renewal_loop(context, Event())
    assert len(renewals) == 2 and not context.control.failed.is_set()


def test_consumer_logs_an_unfinished_activation_as_a_warning(monkeypatch, caplog):
    """The broker task never reports the activation window as task_failed ERROR."""
    from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
    from parishkit.stewardship.jobs import broker

    def consume(*args, **kwargs):
        """The dispatcher's wait ran out (its own timeout line is separate)."""
        raise AuthorityChanging("synthetic")

    monkeypatch.setattr(broker, "consume_hint", consume)
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.WORKER,
        handlers={},
    )
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        runtime.app.tasks[broker.HINT_TASK].run(str(uuid4()))
    levels = [r.levelno for r in caplog.records if r.msg == LogEvent.TASK_FAILED]
    assert levels == [logging.WARNING]


def test_source_read_holds_an_unfinished_activation_without_spending_attempts():
    """Classified like a scope change, but as contention (no allowance used)."""
    for error in (
        AuthorityChanging("synthetic"),
        SourceAuthorityChanging("synthetic"),
    ):
        decision = classify_read_failure(error, has_source_claim=True)
        assert decision.retry and decision.contention
        assert decision.event is LogEvent.SOURCE_HELD


def test_local_read_admission_carries_the_hold_out_of_shared_loaders():
    """A loader treats ValueError as bad provider data; the hold is not one."""

    @local_read_admission
    def before_request():
        """The transport preflight met the activation window."""
        raise AuthorityChanging("synthetic")

    with pytest.raises(SourceAuthorityChanging):
        before_request()


@pytest.mark.parametrize("noted", [False, True])
def test_activation_503_is_not_logged_as_a_server_error(caplog, noted):
    """Django logs a 503 at ERROR unless it met an activation in progress."""
    from django.utils.log import log_response

    with request_scope.request_scope():
        if noted:
            authority_mismatch(version(NEXT, BASE), BASE, "synthetic")
        response = request_scope.mark_activation_response(HttpResponse(status=503))
        with caplog.at_level(logging.WARNING, logger="django.request"):
            log_response(
                "Service Unavailable", response=response, request=HttpRequest()
            )
    errors = [r for r in caplog.records if r.name == "django.request"]
    assert bool(errors) is not noted
    assert response.has_header("Retry-After") is noted


def test_activation_note_is_scoped_to_one_request():
    """Outside a request nothing is noted; a new request starts clean."""
    authority_mismatch(version(NEXT, BASE), BASE, "synthetic")
    assert not request_scope.authority_changing_noted()
    with request_scope.request_scope():
        assert not request_scope.authority_changing_noted()
        response = request_scope.mark_activation_response(HttpResponse(status=200))
        assert not response.has_header("Retry-After")


def test_admin_poll_waits_out_activation_then_answers_retry(monkeypatch):
    """The poll views retry briefly, then give the pollers' ordinary 503."""
    from parishkit.stewardship.jobs import views

    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    attempts = []

    def brief():
        """Meet the window once, then read normally."""
        attempts.append(1)
        if len(attempts) == 1:
            raise AuthorityChanging("synthetic")
        return HttpResponse("ok")

    assert views._held(brief).content == b"ok"
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(views, "WEB_HOLD_SECONDS", 0.03)

    def stuck():
        """The change never finishes activating."""
        raise AuthorityChanging("synthetic")

    response = views._held(stuck)
    assert response.status_code == 503 and response["Retry-After"]
