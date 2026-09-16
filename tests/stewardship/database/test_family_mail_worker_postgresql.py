"""Installed MAIL consumer, finite-provider boundary and maintained Task lifetime."""

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


def deliver(harness, path, message):
    """Execute the real maintained worker, including connection closure/rebinding."""
    owner = delivery_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin="http://localhost:8000",
        credential_path=path,
    )
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
