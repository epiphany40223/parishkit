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


@pytest.fixture(autouse=True)
def installer_running(monkeypatch):
    """By default an installer holds its lock, as during a real activation."""
    state = {"running": True}
    monkeypatch.setattr(
        activation_hold, "installation_running", lambda: state["running"]
    )
    return state


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


def own_entries(monkeypatch):
    """Capture ``record_timeout`` calls; return the activation wait's own.

    The patch is process-wide and production callers import the recorder
    lazily, so a daemon thread an earlier test left behind (a lease renewal,
    a liveness thread) could record its own timeout into it during the wait
    (#542, #549). Only ``configuration_activation`` records are this test's.
    """
    from parishkit.stewardship.audit import timeouts

    entries = []
    monkeypatch.setattr(
        timeouts,
        "record_timeout",
        lambda event, **facts: entries.append((event, facts)),
    )
    return lambda: [
        (event, facts)
        for event, facts in entries
        if facts.get("what") == "configuration_activation"
    ]


def test_worker_wait_gives_up_with_a_durable_timeout_entry(monkeypatch):
    """Background work records the abandoned wait: what, limit and elapsed."""
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0.01)
    entries, task = own_entries(monkeypatch), uuid4()

    def step():
        """The change never finishes activating."""
        raise AuthorityChanging("synthetic")

    with pytest.raises(AuthorityChanging):
        activation_hold.wait_out_activation(step, limit=0.05, task_id=task)
    ((event, facts),) = entries()
    assert event is LogEvent.TASK_TIMED_OUT
    assert facts["what"] == "configuration_activation" and facts["level"] == "WARNING"
    assert facts["task_id"] == task and facts["limit_seconds"] == 0.05
    assert facts["elapsed_seconds"] >= 0.05


def test_web_wait_gives_up_in_the_process_log_only(monkeypatch, caplog):
    """An Admin poll's give-up is a WARNING line, never a durable entry."""
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0.01)
    # Recorded, not failed inside the recorder: a stray thread's record
    # would otherwise trip it, and a failure raised on another thread does
    # not fail this test anyway.
    entries = own_entries(monkeypatch)

    def step():
        """The change never finishes activating."""
        raise AuthorityChanging("synthetic")

    with (
        caplog.at_level(logging.INFO, logger="parishkit.stewardship"),
        pytest.raises(AuthorityChanging),
    ):
        activation_hold.wait_out_activation(step, limit=0.05, durable=False)
    assert entries() == [], "no durable entry for a web wait"
    (record,) = [r for r in caplog.records if r.msg == LogEvent.TASK_TIMED_OUT]
    assert record.levelno == logging.WARNING
    context = getattr(record, STRUCTURED_EXTRA_FIELD)
    assert context["timeout"] == "configuration_activation"
    assert context["limit_seconds"] == 0 and context["elapsed_seconds"] == 0


@pytest.mark.parametrize("comes_back", [False, True])
def test_a_mismatch_with_no_installer_running_is_not_waited_for(
    monkeypatch, installer_running, comes_back
):
    """A YAML selected by a failed activation needs recovery, not a wait.

    One idle sighting may be the instant after the commit released the lock,
    so the step is tried once more; a second idle sighting raises the
    ordinary ConfigError at once.
    """
    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    installer_running["running"] = False
    attempts = []

    def step():
        """Mismatched until (optionally) the second attempt."""
        attempts.append(1)
        if comes_back and len(attempts) == 2:
            return "done"
        raise AuthorityChanging("synthetic")

    if comes_back:
        assert activation_hold.wait_out_activation(step) == "done"
    else:
        with pytest.raises(ConfigError) as caught:
            activation_hold.wait_out_activation(step)
        assert not isinstance(caught.value, AuthorityChanging)
    assert len(attempts) == 2


def test_activating_names_only_a_running_activation(installer_running):
    """The scheduler's check: an AuthorityChanging while the installer runs."""
    assert activation_hold.activating(AuthorityChanging("synthetic"))
    assert not activation_hold.activating(ConfigError("synthetic"))
    installer_running["running"] = False
    assert not activation_hold.activating(AuthorityChanging("synthetic"))


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


@pytest.mark.parametrize("held", [False, True])
def test_only_the_holds_own_503_skips_the_server_error_log(caplog, held):
    """Any other 503, even in a request that met an activation, stays ERROR."""
    from django.utils.log import log_response

    response = HttpResponse(status=503)
    if held:
        setattr(response, request_scope.ACTIVATION_HOLD, True)
    response = request_scope.mark_activation_response(response)
    with caplog.at_level(logging.WARNING, logger="django.request"):
        log_response("Service Unavailable", response=response, request=HttpRequest())
    errors = [r for r in caplog.records if r.name == "django.request"]
    assert bool(errors) is not held
    assert response.has_header("Retry-After") is held


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
    assert response.status_code == 503
    assert getattr(response, request_scope.ACTIVATION_HOLD)


def test_admin_poll_answers_a_stuck_activation_as_an_ordinary_503(
    monkeypatch, installer_running
):
    """No installer running: the ordinary 503, logged at ERROR as before."""
    from parishkit.stewardship.jobs import views

    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    installer_running["running"] = False

    def stuck():
        """A failed activation left the YAML selected."""
        raise AuthorityChanging("synthetic")

    response = views._held(stuck)
    assert response.status_code == 503
    assert not getattr(response, request_scope.ACTIVATION_HOLD, False)


class FakeCursor:
    """Accept check_inflight's transaction-local limits without SQL."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args):
        """Nothing to set without a database."""


def inflight_execution(monkeypatch, admit):
    """An execution whose in-flight check runs real logic but no SQL."""
    monkeypatch.setattr(
        dispatch,
        "connection",
        SimpleNamespace(in_atomic_block=False, cursor=FakeCursor),
    )
    monkeypatch.setattr(dispatch, "transaction", SimpleNamespace(atomic=nullcontext))
    monkeypatch.setattr(dispatch, "lock_task_claim", lambda claim: None)
    monkeypatch.setattr(dispatch, "_status", lambda row: None)
    return synthetic_execution(admit)


def test_inflight_check_in_the_window_is_unverified_not_fatal(monkeypatch):
    """A send already under way is not killed by a settings edit (#429)."""
    calls = []

    def admit(action, status):
        """mail_authority in the window, then agreement again."""
        calls.append(action)
        if len(calls) == 1:
            raise AuthorityChanging("synthetic")
        return True

    context = inflight_execution(monkeypatch, admit)
    assert context.check_inflight() is False
    assert context.check_inflight() is True


def _changing(*args):
    """An admission that calls mail_authority in the activation window."""
    raise AuthorityChanging("synthetic")


@pytest.mark.parametrize("owner", ["campaign", "operational", "setup"])
def test_mail_helper_pulses_survive_the_activation_window(monkeypatch, tmp_path, owner):
    """Campaign, operational and setup mail admit with mail_authority unwrapped.

    Before #429 an AuthorityChanging there escaped the pulse and killed the
    helper mid-send; now the pulse is an unverified tick and the send drains.
    """
    context = inflight_execution(monkeypatch, _changing)
    if owner == "campaign":
        from parishkit.stewardship.accounts import campaign_mail_tasks as tasks
        from parishkit.stewardship.accounts.key_files import (
            file_fingerprint,
        )

        path = tmp_path / "key"
        path.write_bytes(b"SYNTHETIC")
        path.chmod(0o600)
        tasks._check(context, path, file_fingerprint(b"SYNTHETIC"))
    elif owner == "operational":
        from parishkit.stewardship.jobs import operational_mail_tasks as tasks

        tasks._check(context)
    else:
        from parishkit.stewardship.accounts import setup_mail_tasks as tasks

        tasks._check(context)
    assert not context.control.failed.is_set()


@pytest.mark.parametrize("running", [True, False])
def test_scheduler_producer_logs_activation_as_warning_and_stuck_as_error(
    monkeypatch, caplog, installer_running, running
):
    """A producer that met the window retries next pass at WARNING."""
    from parishkit.stewardship import runtime_process

    installer_running["running"] = running
    guard = SimpleNamespace(check=lambda: None)
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        assert runtime_process.independent_producer(guard, _changing) == ()
    (record,) = [r for r in caplog.records if r.msg == LogEvent.TASK_FAILED]
    assert record.levelno == (logging.WARNING if running else logging.ERROR)
