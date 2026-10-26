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

from parishkit.stewardship.accounts.setup_credentials import (
    credential_status,
    stage_credential,
)
from parishkit.stewardship.accounts.setup_drafts import view_draft
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_staging import begin_setup
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue

from .legacy_setup_execution import setup_source_handler
from .legacy_setup_source import (
    VOID_SETTING,
    start_source_load,
)
from .setup_lookup_builders import answered_and_chosen
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE
from .test_setup_loading_postgresql import (
    installer_reply,
    pages,
    queued,
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
    """Stage a ParishSoft key on the web login and choose its organization.

    The key's organization lookup is answered with ``organization`` and that
    organization chosen (#1554). Returns the attempt's status and the key's
    receipt once chosen.
    """
    attempt = SetupAttempt.objects.get()
    with web_login():
        stage_credential(
            request,
            service,
            attempt.pk,
            target="parishsoft",
            candidate=candidate,
            expected_version=attempt.version,
        )
    status = answered_and_chosen(
        request, service, attempt.pk, organization_id=organization
    )
    with web_login():
        (receipt,) = [
            item
            for item in credential_status(request, service, attempt.pk)
            if item.target == "parishsoft"
        ]
    return status, receipt


def context(event_type):
    """The one audit context recorded for ``event_type``, newest first."""
    event = AuditEvent.objects.filter(event_type=event_type).latest("created_at")
    return AuditContext.objects.get(event=event).context


def source_page(request, service):
    """The Check parish data page as its view renders it, and the draft."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.setup_views import _context

    with web_login():
        draft = view_draft(request, service)
        context = _context(draft, "source")
    context["source_step"] = context["wizard"].step("source")
    return render_to_string("stewardship/setup-source.html", context), context


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


def restore_0102(cursor, text):
    """Put back 0102's own email and Slack live checks, which 0168 (#1578)
    replaced since, so 0102's check runs against what 0102 installed; the
    caller rolls back."""
    for name in ("stewardship_setup_mail_live_v1", "stewardship_setup_slack_live_v1"):
        start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
        quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, start)
        cursor.execute(
            text[start : text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1]
        )


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
        restore_0102(cursor, text)
        cursor.execute(check)
        transaction.set_rollback(True)
    for name in EARLIER:
        with (
            pytest.raises(DatabaseError, match="0102"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            restore_0102(cursor, text)
            cursor.execute(earlier_definition(name))
            cursor.execute(check)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(text)
        cursor.execute(check)
        # 0104 (#1402) replaces 0102's safe context again; rolling back keeps
        # the installed (later) bodies for the tests that follow.
        transaction.set_rollback(True)
