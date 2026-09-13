"""Exact setup mail intake and irreversible outcomes with restricted SQL roles."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from psycopg import sql

from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_mail import (
    begin_submission,
    finish_submission,
    recover_pending,
    request_sample,
)
from parishkit.stewardship.accounts.setup_preview import PREVIEW_SALT, prepare_preview
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..test_setup_forms import VALUES
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_preview_postgresql import complete_draft
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def prepared(service, monkeypatch, tmp_path):
    """Use the actual source/branding/credential/preview APIs before journal intake."""
    request, attempt, _ = complete_draft(service, monkeypatch, tmp_path)
    with web_login():
        token = signing.dumps(
            prepare_preview(request, service).binding(), salt=PREVIEW_SALT
        )
        key = uuid4()
        delivery = request_sample(
            request, service, preview_token=token, request_key=key
        )
    return request, attempt, token, key, delivery


def claim(delivery):
    """The internal fixture admits only its exact journal's existing Task root."""
    row = TaskRun.objects.get(pk=delivery.task_id)
    status = change_run(
        run_id=row.pk,
        expected_version=row.version,
        action="claim",
        actor_id=uuid4(),
        correlation_id=uuid4(),
        lease_seconds=120,
        admit=lambda action, task: task.domain_request_id == delivery.identifier,
    )
    return TaskClaim(status.run_id, status.fence, status.worker_id)


def test_exact_intake_is_idempotent_and_never_contacts_provider(
    setup_service, monkeypatch, tmp_path
):
    """Replayed browser requests preserve one pending test and one durable root."""
    request, _, token, key, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        assert (
            request_sample(request, setup_service, preview_token=token, request_key=key)
            == delivery
        )
        with pytest.raises(StaleRecordError):
            request_sample(
                request, setup_service, preview_token=token, request_key=uuid4()
            )
    row = SetupMailDelivery.objects.get()
    assert row.mail["recipient"] == VALUES["testing"]["testing_recipient"]
    assert "synthetic-private" not in str(row.mail)
    assert row.state == "queued" and row.submitted_at is None
    assert TaskRun.objects.filter(task_type="setup_mail_test").count() == 1
    assert not setup_service.configured()


def test_collecting_mail_is_not_subject_to_finished_loading_watchdog(
    setup_service, monkeypatch, tmp_path
):
    """An active original login may test mail after the two-hour loading bound."""
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    from .test_setup_progress_postgresql import aged_original_load

    request, _, _ = complete_draft(setup_service, monkeypatch, tmp_path)
    aged_original_load(SetupAttempt.objects.get().source_task_id, minutes=121)
    with web_login():
        token = signing.dumps(
            prepare_preview(request, setup_service).binding(), salt=PREVIEW_SALT
        )
        result = request_sample(
            request, setup_service, preview_token=token, request_key=uuid4()
        )
    assert SetupMailDelivery.objects.get(pk=result.identifier).state == "queued"


@pytest.mark.parametrize("outcome", list(DeliveryOutcome))
def test_only_mail_worker_can_submit_and_terminal_result_cannot_replay(
    setup_service, monkeypatch, tmp_path, outcome
):
    """The before-IO marker and result are real commits under the dedicated role."""
    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        with work_transaction(), pytest.raises(StorageInvariantError):
            begin_submission(delivery.identifier, owner)
        mail, deadline = begin_submission(delivery.identifier, owner)
        assert mail.delivery_id == delivery.identifier
        assert SetupMailDelivery.objects.get().deadline_at == deadline
        assert (
            finish_submission(delivery.identifier, owner, outcome).state
            == outcome.value
        )
        with pytest.raises(PermissionError):
            begin_submission(delivery.identifier, owner)
        with pytest.raises(DatabaseError), work_transaction():
            SetupMailDelivery.objects.update(state="queued", version=F("version") + 1)
        for statement in (
            "SELECT ciphertext FROM stewardship_setup_sealed_credential",
            "SELECT canonical FROM stewardship_source_family",
            "SELECT session_id FROM stewardship_portal_session",
        ):
            with (
                pytest.raises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as c,
            ):
                c.execute(statement)


@pytest.mark.parametrize("submitted", [False, True])
def test_cancel_scrubs_sample_without_inventing_nonsubmission(
    setup_service, monkeypatch, tmp_path, submitted
):
    """Cancellation cannot turn an already launched, unknown send into a safe retry."""
    request, attempt, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    if submitted:
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            begin_submission(delivery.identifier, claim(delivery))
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    row = SetupMailDelivery.objects.get()
    assert row.mail == {} and row.scrubbed_at is not None
    assert row.state == ("submitting" if submitted else "cancelled")
    scrub = AuditEvent.objects.get(event_type="setup_mail_scrubbed")
    assert AuditContext.objects.get(event=scrub).context["outcome"] == (
        "changed" if submitted else "cancelled"
    )
    with pytest.raises(DatabaseError), work_transaction():
        SetupMailDelivery.objects.update(
            mail={"subject": "new"}, version=F("version") + 1
        )


def test_scheduler_cannot_read_draft_or_rendered_mail_payloads(
    setup_service, monkeypatch, tmp_path
):
    """Real scrub-only grants still permit stale-send recovery after a draft edit."""
    from parishkit.stewardship.accounts.setup_mail_tasks import setup_mail_handler
    from parishkit.stewardship.jobs.scanning import collect_hints

    request, attempt, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        for statement in (
            "SELECT values FROM stewardship_setup_draft_section",
            "SELECT mail FROM stewardship_setup_mail_delivery",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert error.value.__cause__.sqlstate == "42501"
        assert recover_pending() == 0
        hints, _ = collect_hints(
            handlers={"setup_mail_test": setup_mail_handler(scheduler=True)}
        )
        assert [hint.run_id for hint in hints] == [delivery.task_id]
    with web_login():
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="testing",
            values={"testing_recipient": "different@example.org"},
            expected_version=attempt.version,
        )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
    assert SetupMailDelivery.objects.get().state == "cancelled"


def test_changed_recipient_invalidates_queued_submission(
    setup_service, monkeypatch, tmp_path
):
    """Queued work never silently retargets to the latest address or key settings."""
    request, attempt, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="testing",
            values={"testing_recipient": "changed@example.org"},
            expected_version=attempt.version,
        )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        with pytest.raises(PermissionError):
            begin_submission(delivery.identifier, owner)


def test_raw_web_sql_cannot_forge_delivery_success(
    setup_service, monkeypatch, tmp_path
):
    """The web role's cleanup privilege cannot advance a send or alter its recipient."""
    prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        for mutation in (
            {"state": "accepted"},
            {"mail": {"recipient": "other@example.org"}},
        ):
            with pytest.raises(DatabaseError), work_transaction():
                SetupMailDelivery.objects.update(**mutation, version=F("version") + 1)


def age_delivery(identifier, *, task=False):
    """Install historical timestamps only in the disposable schema-owner fixture.

    All runtime checks execute with triggers restored and the real wall clock.
    No real provider call or running helper exists in these journal-only tests.
    """
    row = SetupMailDelivery.objects.get(pk=identifier)
    table = "stewardship_task_run" if task else "stewardship_setup_mail_delivery"
    fields = ["lease_expires_at"] if task else ["submitted_at", "deadline_at"]
    with transaction.atomic(), connection.cursor() as cursor:
        name = sql.Identifier(table)
        cursor.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(name))
        try:
            for field in fields:
                cursor.execute(
                    sql.SQL("UPDATE {} SET {}={}- %s WHERE id=%s").format(
                        name, sql.Identifier(field), sql.Identifier(field)
                    ),
                    [
                        timedelta(minutes=3),
                        (row.run_id or row.task_id) if task else row.pk,
                    ],
                )
                assert cursor.rowcount == 1
        finally:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(name))


def test_recovery_waits_for_both_fences_and_never_retries(
    setup_service, monkeypatch, tmp_path
):
    """A stale worker yields a permanent unknown and needs explicit resend consent."""
    request, _, token, key, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        begin_submission(delivery.identifier, claim(delivery))
    age_delivery(delivery.identifier)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 0  # Original Task can still report its outcome.
    age_delivery(delivery.identifier, task=True)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
        assert recover_pending() == 0
    assert SetupMailDelivery.objects.get().state == "delivery_unknown"
    assert TaskRun.objects.filter(task_type="setup_mail_test").count() == 1
    with web_login():
        assert (
            request_sample(
                request, setup_service, preview_token=token, request_key=key
            ).state
            == "delivery_unknown"
        )
        with pytest.raises(ValueError, match="acknowledge"):
            request_sample(
                request, setup_service, preview_token=token, request_key=uuid4()
            )
        assert (
            request_sample(
                request,
                setup_service,
                preview_token=token,
                request_key=uuid4(),
                acknowledge_unknown=True,
            ).state
            == "queued"
        )


@pytest.mark.parametrize("crossing", [False, True])
def test_expired_helper_outcome_is_unknown_even_with_live_task(
    setup_service, monkeypatch, tmp_path, crossing
):
    """A result arriving outside the finite helper deadline cannot claim readiness."""
    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        begin_submission(delivery.identifier, owner)
    age_delivery(delivery.identifier)
    if crossing:
        from parishkit.stewardship.accounts import delivery_results

        original = delivery_results.database_now
        before = SetupMailDelivery.objects.get(pk=delivery.identifier).deadline_at
        calls = iter([before - timedelta(seconds=1), original()])
        monkeypatch.setattr(delivery_results, "database_now", lambda: next(calls))
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        result = finish_submission(delivery.identifier, owner, DeliveryOutcome.ACCEPTED)
        assert result.state == "delivery_unknown"


def test_scheduler_cancels_only_stale_queued_work(setup_service, monkeypatch, tmp_path):
    """Unchanged setup stays queued; a changed recipient cannot block it forever."""
    request, attempt, _, _, _ = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 0
    with web_login():
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="testing",
            values={"testing_recipient": "changed@example.org"},
            expected_version=attempt.version,
        )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
        assert recover_pending() == 0
    assert SetupMailDelivery.objects.get().state == "cancelled"


def test_journal_audit_contains_outcomes_but_no_rendered_or_recipient_values(
    setup_service, monkeypatch, tmp_path
):
    """Every state change leaves immutable, private-safe evidence in the shared log."""
    request, attempt, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        begin_submission(delivery.identifier, owner)
        finish_submission(delivery.identifier, owner, DeliveryOutcome.ACCEPTED)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    events = AuditEvent.objects.filter(subject_id=delivery.identifier).order_by(
        "created_at"
    )
    assert list(events.values_list("event_type", flat=True)) == [
        "setup_mail_queued",
        "setup_mail_submitting",
        "setup_mail_accepted",
        "setup_mail_scrubbed",
    ]
    contexts = list(
        AuditContext.objects.filter(event__in=events).values_list("context", flat=True)
    )
    assert len(contexts) == 4
    assert all(set(value) == {"version", "outcome"} for value in contexts)
    assert "@" not in str(contexts) and "synthetic" not in str(contexts)
