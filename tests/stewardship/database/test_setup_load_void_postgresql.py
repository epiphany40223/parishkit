"""A finished setup load can be discarded and loaded again (#1353, 0102).

Migration 0102 (``schema/migrations/0102_setup_load_void.sql``) lets a load
that fails for good return setup to collecting, lets the owning live Admin
discard a finished load (audited as ``setup_source_voided``) and lets the
setup source cleanup dispose of every load of the attempt. These tests run
the real web and worker paths under their own logins, each SQL refusal, the
cleanup of a discarded load and the file's own DO block against the earlier
definitions.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import re
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.accounts.setup_credentials import stage_credential
from parishkit.stewardship.accounts.setup_drafts import view_draft
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_source import (
    VOID_SETTING,
    start_source_load,
)
from parishkit.stewardship.accounts.setup_staging import begin_setup, cancel_setup
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.setup_admission import bound_attempt
from parishkit.stewardship.source.setup_cleanup import _unscheduled
from parishkit.stewardship.source.setup_disposal import dispose_batch
from parishkit.stewardship.source.setup_execution import setup_source_handler
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .setup_request_budgets import short_request_budgets
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE
from .test_setup_disposal_postgresql import cleanup_claim
from .test_setup_loading_postgresql import (
    installer_reply,
    pages,
    prepared,
    queued,
    run,
)
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider
from .test_taskrun_postgresql import act, expire, new

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = SCHEMA / "migrations/0102_setup_load_void.sql"
# Provider data the load rejects for good: a load that never retries.
INVALID = {"birthdate": "invalid-private-value"}


def dispatch(monkeypatch, private, task_id, *, invalid=False):
    """Run one queued load on the real worker path, to success or failure."""
    installer_reply(monkeypatch, private)
    fake_provider(monkeypatch, pages(member_change=INVALID if invalid else None))
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            task_id,
            queue=WorkQueue.SOURCE,
            worker_id=uuid4(),
            handlers={"setup_source_load": setup_source_handler()},
        )
    return TaskRun.objects.get(pk=task_id).state


def drained():
    """Wait out the earlier load's real provider drain before the next claim.

    The source lease keeps a read's external deadline after release, so a new
    load (or the cleanup) cannot claim the source until it passes; the test
    waits for it rather than rewriting lease clocks.
    """
    from parishkit.stewardship.source.models import SourceMutationLease

    with transaction.atomic():
        deadline = SourceMutationLease.objects.get().external_deadline
        remaining = (deadline - database_now()).total_seconds() if deadline else 0
    Event().wait(max(remaining, 0) + 0.05)


def stage(request, service, *, candidate=CANDIDATE, organization=1):
    """Stage a ParishSoft key and organization on the web login."""
    attempt = SetupAttempt.objects.get()
    with web_login():
        return stage_credential(
            request,
            service,
            attempt.pk,
            target="parishsoft",
            candidate=candidate,
            expected_version=attempt.version,
            organization_id=organization,
        )


def context(event_type):
    """The one audit context recorded for ``event_type``, newest first."""
    event = AuditEvent.objects.filter(event_type=event_type).latest("created_at")
    return AuditContext.objects.get(event=event).context


def test_a_failed_load_returns_to_collecting_and_loads_again(
    setup_service, monkeypatch
):
    """A permanent failure keeps its binding; Load again discards it."""
    short_request_budgets(monkeypatch)
    request, attempt, task, private = queued(setup_service)
    assert dispatch(monkeypatch, private, task.run_id, invalid=True) == "failed"
    row = SetupAttempt.objects.get()
    assert (row.state, row.source_task_id) == ("collecting", task.run_id)
    assert context("setup_source_completed")["outcome"] == "failed"
    with web_login():
        draft = view_draft(request, setup_service, row.pk)
    assert draft.source_task_state == "failed"

    with web_login():
        again = start_source_load(
            request, setup_service, row.pk, expected_version=row.version
        )
    assert again.run_id != task.run_id
    row = SetupAttempt.objects.get()
    assert (row.state, row.source_task_id) == ("loading", again.run_id)
    # The discard's audit names why and how the load had ended, never a key.
    assert context("setup_source_voided") == {
        "version": row.version - 1,
        "outcome": "cancelled",
        "void_reason": "reload",
        "task_state": "failed",
    }
    keys = set(
        TaskRun.objects.filter(task_type="setup_source_load").values_list(
            "idempotency_key", flat=True
        )
    )
    assert len(keys) == 2
    # The discarded Task no longer resolves the attempt, and the worker
    # cannot return setup to collecting for the new, unfinished load.
    with work_transaction(), pytest.raises(PermissionError):
        bound_attempt(_status(TaskRun.objects.get(pk=task.run_id)))
    with (
        pytest.raises(IntegrityError, match="validated setup staging"),
        task_login(ServiceRole.WORKER, exact=True),
        work_transaction(),
    ):
        SetupAttempt.objects.filter(pk=row.pk).update(
            state="collecting", actor_id=row.owner_id, version=F("version") + 1
        )

    drained()
    assert dispatch(monkeypatch, private, again.run_id) == "succeeded"
    row = SetupAttempt.objects.get()
    assert (row.state, row.source_task_id) == ("collecting", again.run_id)
    # A successful load is kept: loading again returns it unchanged.
    with web_login():
        assert (
            start_source_load(
                request, setup_service, row.pk, expected_version=row.version
            ).run_id
            == again.run_id
        )


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"organization": 2}, "organization_changed"),
        ({"candidate": b"another-synthetic-wizard-key"}, "key_changed"),
    ],
)
def test_the_same_connection_keeps_a_load_and_a_change_discards_it(
    setup_service, monkeypatch, change, reason
):
    """Only a different key or organization discards a successful load."""
    request, attempt, task, private = queued(setup_service)
    assert dispatch(monkeypatch, private, task.run_id) == "succeeded"
    before = SetupAttempt.objects.get()
    status, receipt = stage(request, setup_service)
    after = SetupAttempt.objects.get()
    assert (after.version, after.source_task_id) == (before.version, task.run_id)
    assert status.version == before.version
    assert not AuditEvent.objects.filter(event_type="setup_source_voided").exists()

    status, changed = stage(request, setup_service, **change)
    row = SetupAttempt.objects.get()
    assert (row.state, row.source_task_id, row.renewed_at) == ("collecting", None, None)
    assert changed.version == receipt.version + 1
    assert context("setup_source_voided") == {
        "version": row.version - 1,
        "outcome": "cancelled",
        "void_reason": reason,
        "task_state": "succeeded",
    }
    # Nothing is bound now, so a further change discards nothing, and the
    # next load binds a new Task.
    stage(request, setup_service, organization=3)
    assert AuditEvent.objects.filter(event_type="setup_source_voided").count() == 1
    row = SetupAttempt.objects.get()
    with web_login():
        load = start_source_load(
            request, setup_service, row.pk, expected_version=row.version
        )
    assert load.run_id != task.run_id
    assert SetupAttempt.objects.get().source_task_id == load.run_id


def finished(service, outcome):
    """An attempt bound to a synthetic load that ended with ``outcome``.

    A succeeded load is returned to collecting by this schema-owner test
    login, as the guard admits; a failed one by the worker login, the only
    one that may (#1353).
    """
    request = login(service)
    with web_login():
        attempt = begin_setup(request, service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    act(act(task, "claim"), outcome)
    back = dict(state="collecting", actor_id=row.owner_id, version=F("version") + 1)
    if outcome == "complete":
        with work_transaction():
            SetupAttempt.objects.filter(pk=row.pk).update(**back)
    else:
        with pytest.raises(IntegrityError, match="owning work"), work_transaction():
            SetupAttempt.objects.filter(pk=row.pk).update(**back)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            SetupAttempt.objects.filter(pk=row.pk).update(**back)
    return SetupAttempt.objects.get(pk=row.pk), task


def void(row, reason, *, actor=None, **values):
    """Discard the binding directly in SQL, naming ``reason`` as the app does."""
    with work_transaction():
        if reason is not None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config(%s, %s, true)", [VOID_SETTING, reason]
                )
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=None,
            actor_id=actor or row.owner_id,
            version=F("version") + 1,
            **values,
        )


def test_sql_discards_only_a_finished_load_by_its_owner_with_a_reason(setup_service):
    """Every other way to clear a load binding is refused."""
    row, task = finished(setup_service, "complete")
    for reason, actor, message in (
        (None, None, "closed reason"),
        ("unknown", None, "closed reason"),
        ("reload", None, "closed reason"),  # only a failed load reloads
        ("key_changed", uuid4(), "original authorized base"),
    ):
        with pytest.raises(IntegrityError, match=message):
            void(row, reason, actor=actor)
    with pytest.raises(IntegrityError, match="finished setup load"):
        void(row, "key_changed", renewed_at=timezone.now())
    assert SetupAttempt.objects.get().source_task_id == task.run_id
    void(row, "organization_changed")
    assert SetupAttempt.objects.get().source_task_id is None
    # The setting is consumed: the next discard must name its own reason.
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [VOID_SETTING])
        assert cursor.fetchone()[0] in (None, "")


def test_sql_refuses_to_discard_a_running_load(setup_service):
    """A running load stays bound, by any route."""
    request = login(setup_service)
    with web_login():
        attempt = begin_setup(request, setup_service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    with pytest.raises(IntegrityError):
        void(SetupAttempt.objects.get(), "key_changed")
    with pytest.raises(IntegrityError), work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            state="collecting", source_task_id=None, version=F("version") + 1
        )


@pytest.mark.parametrize("ending", ["retry_wait", "abandoned"])
def test_a_load_waiting_to_retry_or_abandoned_cannot_be_discarded(
    setup_service, ending
):
    """A load that may still run again keeps setup loading and stays bound."""
    request = login(setup_service)
    with web_login():
        attempt = begin_setup(request, setup_service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    if ending == "retry_wait":
        act(act(task, "claim"), "retryable_failure")
    else:
        # A real one-second lease runs out: the worker is gone.
        expire(act(task, "claim", lease_seconds=1))
    assert TaskRun.objects.get(pk=task.run_id).state == ending
    row = SetupAttempt.objects.get()
    for state in ("collecting", "loading"):
        with pytest.raises(IntegrityError), work_transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config(%s, %s, true)", [VOID_SETTING, "key_changed"]
                )
            SetupAttempt.objects.filter(pk=row.pk).update(
                state=state, source_task_id=None, version=F("version") + 1
            )
    with pytest.raises(PermissionError):
        stage(request, setup_service, organization=2)
    assert SetupAttempt.objects.get().source_task_id == task.run_id


def test_a_crashed_load_that_recovery_fails_returns_to_collecting(
    setup_service, monkeypatch
):
    """recovery_fail on an abandoned last attempt returns setup like _failed."""
    from parishkit.stewardship.source import setup_admission

    request, attempt, task, _ = queued(setup_service)
    expire(act(task, "claim", lease_seconds=1))
    # The worker died on its last allowed attempt, so recovery fails the load.
    monkeypatch.setattr(setup_admission, "MAX_AUTOMATIC_ATTEMPTS", 1)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert recover_hint(
            task.run_id,
            queue=WorkQueue.SOURCE,
            worker_id=uuid4(),
            handlers={"setup_source_load": setup_source_handler()},
        )
    assert TaskRun.objects.get(pk=task.run_id).state == "failed"
    row = SetupAttempt.objects.get()
    assert (row.state, row.source_task_id) == ("collecting", task.run_id)
    assert context("setup_source_completed")["outcome"] == "failed"
    with web_login():
        again = start_source_load(
            request, setup_service, row.pk, expected_version=row.version
        )
    assert again.run_id != task.run_id
    assert context("setup_source_voided")["void_reason"] == "reload"


def test_a_failed_load_is_discarded_never_rebound_in_place(setup_service):
    """A new load binds only after the failed one's binding is cleared."""
    failed, _ = finished(setup_service, "permanent_failure")
    other = new(
        task_type="setup_source_load",
        actor_id=failed.owner_id,
        domain_request_id=failed.pk,
    )
    with pytest.raises(IntegrityError, match="binding"), work_transaction():
        SetupAttempt.objects.filter(pk=failed.pk).update(
            source_task_id=other.run_id, version=F("version") + 1
        )
    void(failed, "reload")
    with work_transaction():
        SetupAttempt.objects.filter(pk=failed.pk).update(
            source_task_id=other.run_id, state="loading", version=F("version") + 1
        )
    assert SetupAttempt.objects.get().source_task_id == other.run_id


def test_cleanup_disposes_of_a_discarded_load(setup_service, monkeypatch):
    """A discarded load's staging is removed when the setup ends."""
    short_request_budgets(monkeypatch)
    values, request = prepared(setup_service, with_request=True)
    fake_provider(monkeypatch, pages())
    snapshot = run(*values, complete=True)
    stage(request, setup_service, organization=2)
    attempt = SetupAttempt.objects.get()
    assert attempt.source_task_id is None
    with web_login():
        cancel_setup(request, setup_service, attempt.pk)
    assert list(_unscheduled().values_list("pk", flat=True)) == [attempt.pk]
    drained()
    execution, source = cleanup_claim(attempt.pk)
    removed = 0
    with task_login(ServiceRole.WORKER, exact=True):
        while True:
            with execution.effect():
                count = dispose_batch(execution, source)
            if count is None:
                break
            removed += count
    assert removed == sum(snapshot.counts.values())
    assert SourceSnapshot.objects.get().state == "rejected"
    for payload, membership in ENTITY_MODELS.values():
        assert not payload.objects.exists()
        assert not membership.objects.exists()
    release_source(source)


# Where each function 0102 replaces had its earlier body: a frozen file, or
# the fixture copied from the baseline for those no frozen file holds.
EARLIER = {
    "stewardship_setup_attempt_guard_v1": "migrations/0055_setup_users.sql",
    "stewardship_setup_mail_live_v1": "migrations/0055_setup_users.sql",
    "stewardship_setup_slack_live_v1": "migrations/0055_setup_users.sql",
    "stewardship_safe_context_v1": "migrations/0089_retry_refusal_log.sql",
    "stewardship_setup_attempt_audit_v1": None,
    "stewardship_setup_attempt_mutable_v1": None,
    "stewardship_setup_disposable_snapshot_v1": None,
}
FIXTURE = Path(__file__).with_name("pre_setup_load_void_functions.sql")


def earlier_definition(name):
    """The function's body before 0102, as one CREATE OR REPLACE statement."""
    source = EARLIER[name]
    text = (SCHEMA / source if source else FIXTURE).read_text(encoding="utf-8")
    start = re.search(
        rf"^CREATE OR REPLACE FUNCTION public\.{name}\(", text, re.M
    ).start()
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, start)
    return text[start : text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1]


def test_do_block_refuses_each_earlier_definition():
    """The file's closing check passes as installed and raises once any of
    the seven replaced functions is put back to its earlier body; the whole
    file then runs again on an upgraded database (users-swap reruns)."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    assert set(EARLIER) == set(
        re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", text, re.M)
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    for name in EARLIER:
        with (
            pytest.raises(DatabaseError, match="0102"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(earlier_definition(name))
            cursor.execute(check)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(text)
        cursor.execute(check)
