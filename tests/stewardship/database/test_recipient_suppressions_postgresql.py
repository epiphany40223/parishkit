"""Production refusal evidence and source correction use real durable owners."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.outbox_models import OutboxEvent
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity
from parishkit.stewardship.jobs.recipient_models import (
    RecipientRefusal,
    RecipientRefusalResolution,
)
from parishkit.stewardship.jobs.recipient_suppressions import (
    record_refusal,
    source_suppressions,
)
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.snapshots import promote_snapshot

from ..test_outbox_validation import rendering
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_outbox_postgresql import change, permit, provider_evidence, submit
from .test_source_families_postgresql import prepare, reconcile

pytestmark = pytest.mark.django_db(transaction=True)


def refused(harness, *, mode="production", address="valid@example.org"):
    """Record a synthetic provider refusal through real task/outbox state edges."""
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    status = create_message(
        identity=DeliveryIdentity(
            scope_id=harness.campaign.pk,
            campaign_id=harness.campaign.pk,
            family_id=family.pk,
            semantic_key=uuid4(),
            mode=mode,
            routing="production" if mode == "production" else "testing_override",
            purpose="initial",
        ),
        render=rendering(
            configuration_id=harness.campaign.active_configuration.configuration_id,
            intended_recipients=(address,),
            routed_recipients=(address,)
            if mode == "production"
            else ("test@example.org",),
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    failed = change(
        submit(status),
        DeliveryAction.FAIL_UNACCEPTED,
        evidence=replace(provider_evidence(), reason="recipient_refused"),
    )
    return OutboxEvent.objects.get(message_id=failed.message_id, version=failed.version)


def remember(event, address="valid@example.org"):
    """The delivery owner keeps refusal recording within an ordered transaction."""
    with work_transaction():
        return record_refusal(
            event_id=event.pk,
            address=address,
            actor_id=event.actor_id,
            correlation_id=event.correlation_id,
        )


def refresh(harness, data, *, interrupt=False):
    """Apply real source promotion, durable suppressions and identity atomically."""
    snapshot, claim = prepare(data)

    def effects(value):
        """A later effect failure must roll back refusal resolution as well."""
        with task_login(ServiceRole.WORKER, exact=True):
            suppressions = source_suppressions(
                SimpleNamespace(campaign=harness.campaign)
            )
        reconcile(
            value,
            claim,
            harness.campaign,
            harness.rings,
            suppressed_addresses=suppressions,
        )
        if interrupt:
            raise RuntimeError("Injected promotion interruption")
        return True

    try:
        with work_transaction():
            promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    finally:
        release_source(claim)


def test_refusal_is_idempotent_and_source_correction_restores_deliverability(
    response_service,
):
    """Refusal and resolution stay retained while current eligibility changes."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        event = refused(harness)
        record = remember(event)
        assert remember(event).pk == record.pk
        assert not FamilyCampaign.objects.get(family_duid=1).email_deliverable
        refresh(harness, response_source())
        family = FamilyCampaign.objects.get(family_duid=1)
        assert family.email_eligible and not family.email_deliverable
        assert not RecipientRefusalResolution.objects.exists()
        corrected = response_source()
        corrected.members[3]["emailAddress"] = "corrected@example.org"
        refresh(harness, corrected)
        family.refresh_from_db()
        assert family.email_deliverable
        resolution = RecipientRefusalResolution.objects.get(refusal_id=record.pk)
        assert resolution.source_generation == family.source_generation
        assert RecipientRefusal.objects.count() == 1


def test_source_resolution_rolls_back_with_failed_promotion(response_service):
    """An interrupted refresh cannot clear a refusal while retaining old source."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        remember(refused(harness))
        refresh(harness, response_source())
        corrected = response_source()
        corrected.members[3]["emailAddress"] = "corrected@example.org"
        with pytest.raises(RuntimeError, match="Injected"):
            refresh(harness, corrected, interrupt=True)
        assert not RecipientRefusalResolution.objects.exists()
        assert not FamilyCampaign.objects.get(family_duid=1).email_deliverable


def test_testing_refusal_cannot_suppress_real_recipients(response_service):
    """Both the service and raw SQL deny Testing failure as real-address evidence."""
    with campaign_clock(response_service.campaign.active_configuration.starts_at):
        event = refused(response_service, mode="testing")
        with pytest.raises(PermissionError, match="definitive delivery"):
            remember(event)
        with (
            pytest.raises(IntegrityError, match="Production recipient"),
            transaction.atomic(),
        ):
            RecipientRefusal.objects.create(
                family_id=event.message.family_id,
                event_id=event.pk,
                address="valid@example.org",
            )
    assert not RecipientRefusal.objects.exists()


def test_inactivation_does_not_clear_unchanged_contact_refusal(response_service):
    """Activity is not an address correction, even when there are no active heads."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        remember(refused(harness))
        inactive = response_source()
        inactive.members[3]["memberStatus"] = "Inactive"
        refresh(harness, inactive)
        assert not RecipientRefusalResolution.objects.exists()
        refresh(harness, response_source())
        assert not FamilyCampaign.objects.get(family_duid=1).email_deliverable
