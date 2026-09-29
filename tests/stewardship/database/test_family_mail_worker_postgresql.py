"""Installed MAIL consumer, finite-provider boundary and maintained Task lifetime."""

from contextlib import contextmanager
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts.key_files import file_fingerprint, write_private
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue

from . import campaign_builders
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
KEY = b"synthetic-installed-family-workspace-key"


@pytest.fixture
def dispatch_worker(request, monkeypatch, tmp_path):
    """Install public Workspace identity through the same canonical root fixture."""
    original = campaign_builders.configuration_document

    def document():
        value = original()
        value["sections"]["integrations"].append(
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "google_workspace",
                    "settings": {"delegated_email": "sender@example.org"},
                    "credential_fingerprint": file_fingerprint(KEY),
                },
            }
        )
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    harness = request.getfixturevalue("family_mail")
    path = tmp_path / "workspace"
    write_private(path, KEY)
    return harness, path


def family_owner(harness, path):
    """The installed Family MAIL handler, with its own delivery circuit."""
    return delivery_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin="http://localhost:8000",
        credential_path=path,
    )


def deliver(harness, path, message, owner=None):
    """Execute the real maintained worker, including connection closure/rebinding.

    Pass ``owner`` to share one handler (and its circuit) across deliveries,
    as the long-lived worker process does.
    """
    owner = owner or family_owner(harness, path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        execution = claim_hint(
            message.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: owner},
        )
        with maintain_execution(execution):
            owner.execute(execution)
    return owner


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("status", list(Status))
def test_installed_worker_commits_before_exactly_one_provider_call(
    dispatch_worker, monkeypatch, production, status
):
    """Synthetic provider sees committed intent and only the admitted credentials."""
    harness, path = dispatch_worker
    if production:
        harness = activate_response_service(harness)
        complete_empty_catchup(harness.campaign, uuid4())
    calls = []

    def provider(value, settings, mail, *, seconds, check):
        assert not connection.in_atomic_block and 0 < seconds <= 30
        check()
        assert OutboxMessage.objects.get().state == "submitting"
        assert value == KEY and settings["delegated_email"] == "sender@example.org"
        # Read under the mail role: no From name is set, so the Parish name.
        assert settings["sender_name"] == "Example Parish"
        calls.append(mail.recipients)
        return FamilyDeliveryResult(status, len(mail.recipients))

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", provider
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert calls == [("valid@example.org",) if production else ("test@example.org",)]
    assert (
        TaskRun.objects.get(pk=message.task_id).state
        == {
            Status.ACCEPTED: "succeeded",
            Status.TRANSIENT: "retry_wait",
            Status.UNAVAILABLE: "retry_wait",
            Status.PERMANENT: "failed",
            Status.UNKNOWN: "failed",
            Status.SYSTEMIC: "failed",
        }[status]
    )
    assert message.attempt == 1


def test_unexpected_private_transport_failure_is_unknown(dispatch_worker, monkeypatch):
    """After durable submission, an unexpected exception cannot authorize a retry."""
    harness, path = dispatch_worker

    def lost(*args, **kwargs):
        raise RuntimeError("synthetic private provider response")

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", lost
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert (
        message.state == "delivery_unknown" and message.sealed_substitutions is not None
    )
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"


def test_mixed_refusals_retry_same_occurrence_only_to_remaining_address(
    dispatch_worker, monkeypatch
):
    """Definitive non-acceptance does not fabricate a deliverability recovery edge."""
    from threading import Event

    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.jobs.recipient_models import RecipientRefusal

    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    source = response_source()
    source.members[3]["emailAddress"] = "valid@example.org; zother@example.org"
    refresh(harness, source)
    calls = []

    def provider(value, settings, mail, **kwargs):
        calls.append(mail.recipients)
        return (
            FamilyDeliveryResult(Status.TRANSIENT, 2, permanent=(0,), transient=(1,))
            if len(calls) == 1
            else FamilyDeliveryResult(Status.ACCEPTED, 1)
        )

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.RETRY_BASE_SECONDS", 1
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", provider
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
        message.refresh_from_db()
        assert message.state == "retry_wait"
        assert RecipientRefusal.objects.get().address == "valid@example.org"
        assert FamilyCampaign.objects.get(pk=message.family_id).email_deliverable
        Event().wait(1.1)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert calls == [
        ("valid@example.org", "zother@example.org"),
        ("zother@example.org",),
    ]
    assert message.state == "delivered" and message.attempt == 2
    assert ScheduleOccurrence.objects.count() == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "succeeded"


@pytest.mark.parametrize("status", [Status.ACCEPTED, Status.UNKNOWN, Status.PERMANENT])
def test_partial_refusals_are_recorded_independently_of_data_outcome(
    dispatch_worker, monkeypatch, status
):
    """A refused address is suppressed even if other recipients' DATA is uncertain."""
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.jobs.recipient_models import RecipientRefusal

    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    source = response_source()
    source.members[3]["emailAddress"] = "valid@example.org; zother@example.org"
    refresh(harness, source)

    def provider(value, settings, mail, **kwargs):
        assert mail.recipients == ("valid@example.org", "zother@example.org")
        return FamilyDeliveryResult(
            status, 2, permanent=(0, 1) if status is Status.PERMANENT else (0,)
        )

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", provider
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert set(RecipientRefusal.objects.values_list("address", flat=True)) == (
        {"valid@example.org", "zother@example.org"}
        if status is Status.PERMANENT
        else {"valid@example.org"}
    )
    assert FamilyCampaign.objects.get(pk=message.family_id).email_deliverable is (
        status is not Status.PERMANENT
    )
    assert (
        message.state
        == {
            Status.ACCEPTED: "delivered",
            Status.UNKNOWN: "delivery_unknown",
            Status.PERMANENT: "permanent_failure",
        }[status]
    )


def test_definitive_retry_budget_ends_without_uncertain_resend(
    dispatch_worker, monkeypatch
):
    """Bounded non-acceptance retries leave a terminal reviewable delivery outcome."""
    from threading import Event

    harness, path = dispatch_worker
    calls = []

    def provider(value, settings, mail, **kwargs):
        calls.append(mail.semantic_key)
        return FamilyDeliveryResult(Status.TRANSIENT, len(mail.recipients))

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.MAX_ATTEMPTS", 2
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.RETRY_BASE_SECONDS", 1
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", provider
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
        Event().wait(1.1)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert calls == [message.semantic_key, message.semantic_key]
    assert message.state == "permanent_failure" and message.attempt == 2
    assert message.sealed_substitutions is None
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"


def test_an_outage_pause_lifts_after_its_cooldown(dispatch_worker, monkeypatch, caplog):
    """Three outages pause the long-lived worker; after the cooldown mail flows.

    Every Family purpose (invitations, reminders, receipts) shares this one
    handler and circuit, so a short Google outage during a send no longer
    stops all of them until someone restarts the mail worker. The pause is
    recorded durably by the CRITICAL mail_provider_failed entry the stored
    outcomes raise. After a pause one probe is sent; its failure pauses again
    at once, logged as a WARNING rather than a second CRITICAL.
    """
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.jobs import family_mail_dispatch

    harness, path = dispatch_worker
    clock = [1000.0]
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.monotonic",
        lambda: clock[0],
    )
    monkeypatch.setattr(family_mail_dispatch, "RETRY_BASE_SECONDS", 1)
    outage = FamilyDeliveryResult(Status.UNAVAILABLE, 1)
    results = [outage] * 4 + [FamilyDeliveryResult(Status.ACCEPTED, 1)]
    calls = []

    def provider(value, settings, mail, **kwargs):
        calls.append(mail.semantic_key)
        return results[len(calls) - 1]

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family",
        provider,
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        owner = family_owner(harness, path)
        for attempt in range(3):
            assert deliver(harness, path, message, owner) is owner
            # Past both the one-minute probe spacing and the retry backoff.
            clock[0] += 60
            Event().wait(2**attempt + 0.1)
        circuit = owner.admit.keywords["circuit"]
        assert circuit.halted.is_set() and not circuit.stopped
        assert OperationalLog.objects.filter(
            event="mail_provider_failed", level="CRITICAL"
        ).exists()
        # The pause began at the third outage, one minute ago.
        clock[0] += 600 - 60 - 1
        # Still paused: admission refuses the claim, and nothing is sent.
        with pytest.raises(PermissionError):
            deliver(harness, path, message, owner)
        clock[0] += 1
        # The single probe fails: sending pauses again at once, quietly.
        assert deliver(harness, path, message, owner) is owner
        pauses = [r for r in caplog.records if "sending pauses for" in r.getMessage()]
        assert [r.levelname for r in pauses] == ["CRITICAL", "WARNING"]
        Event().wait(8.1)
        clock[0] += 60
        with pytest.raises(PermissionError):
            deliver(harness, path, message, owner)
        clock[0] += 600
        assert deliver(harness, path, message, owner) is owner
    message.refresh_from_db()
    # Five provider results, four of them outages: none spent the budget.
    assert len(calls) == 5 and message.state == "delivered"


NEXT_KEY = b"synthetic-next-workspace-key"


def workspace_key_change(old, new, *, installed):
    """Record a sealed Workspace key change from ``old`` to ``new``.

    It stays in progress (``staged``), or with ``installed`` is walked
    through the installer's states to ``applied``: the new key is in place
    and acknowledged, but no configuration selects it, as after a failed
    automatic switch. It is bound to the key's real consumers, because row
    security shows a key change only to them.
    """
    from datetime import timedelta

    from django.utils import timezone

    from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
    from parishkit.stewardship.accounts.cryptography import Key
    from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
    from parishkit.stewardship.accounts.secret_requests import stage_secret_request
    from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

    request_id = uuid4()
    private = PrivateHandoff("google_workspace", Key("h", "active", b"w" * 32))
    stage_secret_request(
        request_id=request_id,
        target="google_workspace",
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(seconds=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint=file_fingerprint(old),
        correlation_id=uuid4(),
        sealed_candidate=private.public().seal(request_id, new),
        candidate_fingerprint=file_fingerprint(new),
        required_consumers=tuple(
            role.value
            for role, names in ALLOWED_SECRETS.items()
            if "google_workspace" in names
        ),
    )
    row = SecretReplacementRequest.objects.get(pk=request_id)
    if installed:
        from parishkit.stewardship.accounts.credential_installation import (
            acknowledge_loaded_credential,
        )
        from parishkit.stewardship.accounts.secret_models import (
            SealedCredentialStaging,
        )

        def advance(state, **values):
            """One installer transition, as the SQL state guard requires."""
            row.state = state
            row.version += 1
            row.actor_id = None
            for name, value in values.items():
                setattr(row, name, value)
            with installer_identity():
                row.save()

        advance("testing")
        advance("installing", resulting_fingerprint=file_fingerprint(new))
        advance("awaiting_ack")
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            acknowledge_loaded_credential(
                request_id=request_id, consumer="mail-dispatch", loaded_value=new
            )
        row.refresh_from_db()
        advance("cleanup_pending", cleanup_reason="applied")
        with installer_identity():
            SealedCredentialStaging.objects.filter(request_id=request_id).update(
                ciphertext=None
            )
        row.refresh_from_db()
        advance("applied")
    return row


@contextmanager
def installer_identity():
    """Act as the Workspace credential installer's SQL login.

    The state guard lets only that login advance a Workspace key change.
    Tests never provision it, so a disposable role stands in; it is a
    superuser only so this test needs no copy of the installer's grants.
    """
    name = "pk_stewardship_credential_google_workspace"
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{name}" SUPERUSER')
        cursor.execute(f'SET SESSION AUTHORIZATION "{name}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f'DROP ROLE "{name}"')


def hold_quickly(monkeypatch, tasks):
    """Make held messages retry after one second, and fail a charged attempt."""
    monkeypatch.setattr(tasks, "MAX_ATTEMPTS", 1)
    held = tasks._defer_held
    monkeypatch.setattr(
        tasks, "_defer_held", lambda execution, seconds=30: held(execution, seconds=1)
    )


@pytest.mark.parametrize("change", ["in_progress", "installed", None])
def test_key_change_mid_switch_holds_mail_without_spending_attempts(
    dispatch_worker, monkeypatch, caplog, change
):
    """A new Workspace key not yet selected holds Family mail.

    Between the installer renaming a new key into place and the configuration
    selecting it (or while an Administrator still has to select Finish
    switching after the automatic switch failed), the file differs from the
    selected fingerprint. That is not a bad key, so the message waits
    without charging its attempt budget, however long it takes (#307 M1).
    A key installed but unselected past 15 minutes is also logged as an
    ERROR, once an hour, so the held mail is noticed (#338 review). A
    mismatch with no key change under way is still an ordinary failure.
    """
    from parishkit.stewardship.accounts import integration_selection
    from parishkit.stewardship.jobs import family_mail_delivery_tasks as tasks
    from parishkit.stewardship.jobs.models import TaskRunEvent
    from parishkit.stewardship.jobs.phases import TaskPhase

    harness, path = dispatch_worker
    write_private(path, NEXT_KEY)
    if change is not None:
        workspace_key_change(KEY, NEXT_KEY, installed=change == "installed")
    # Treat the installed key as long unfinished, so its first hold alerts.
    monkeypatch.setattr(integration_selection, "SWITCH_ALERT_AFTER", timedelta(0))
    monkeypatch.setattr(integration_selection, "_alerted", {})

    def provider(*args, **kwargs):
        pytest.fail("No mail is sent with a key that is not selected.")

    monkeypatch.setattr(tasks, "submit_family", provider)
    hold_quickly(monkeypatch, tasks)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
        if change is not None:
            # Past the (one-attempt) budget, it is still waiting.
            Event().wait(1.1)
            deliver(harness, path, message)
    task = TaskRun.objects.get(pk=message.task_id)
    message.refresh_from_db()
    alerts = [
        record
        for record in caplog.records
        if getattr(record, "extra", {}).get("failure_kind")
        == "credential_switch_unfinished"
    ]
    if change is None:
        assert task.state == "failed" and not alerts
        return
    assert task.state == "retry_wait" and message.submitted_at is None
    holds = TaskRunEvent.objects.filter(
        run_id=task.pk, action="retryable_failure", phase=TaskPhase.RECONCILING
    )
    assert holds.count() == 2
    # Only an installed key can be stuck; one alert for two holds.
    assert len(alerts) == (1 if change == "installed" else 0)
    if alerts:
        assert alerts[0].levelname == "ERROR"
