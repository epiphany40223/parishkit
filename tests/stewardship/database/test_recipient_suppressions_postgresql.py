"""Production refusal evidence and source correction use real durable owners."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import return_to_testing
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.models import TaskRun
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
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.snapshots import promote_snapshot

from ..campaign_factory import campaign as campaign_record
from ..test_outbox_validation import rendering
from .campaign_builders import (
    add_draft,
    admit_test_work,
    campaign_clock,
    close_campaign,
    command,
)
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_outbox_postgresql import change, permit, provider_evidence, submit
from .test_source_families_postgresql import prepare, reconcile
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def refused(harness, *, mode="production", address="valid@example.org", intended=None):
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
            intended_recipients=(address,) if intended is None else intended,
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


def test_refusal_requires_intended_as_well_as_routed_address(response_service):
    """The Python evidence boundary rejects mismatched routing before SQL writes."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        event = refused(harness, intended=("another@example.org",))
        with pytest.raises(PermissionError, match="definitive delivery"):
            remember(event)
    assert not RecipientRefusal.objects.exists()


def test_unresolved_refusal_survives_a_new_annual_campaign(
    response_service, auth_service
):
    """New campaign-specific Family IDs cannot reset an unchanged refused address."""
    harness = activate_response_service(response_service)
    actor = uuid4()
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        refusal = remember(refused(harness))
        close_campaign(harness.campaign, actor)
    # Settle synthetic fixture tasks through their real terminal owner, without
    # deleting delivery/source history or bypassing campaign archive guards.
    for row in TaskRun.objects.filter(state="running"):
        act(_status(row), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        runtime = SystemConfiguration.objects.get()
        return_to_testing(
            campaign_id=harness.campaign.pk,
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        receipt, row, _ = add_draft(
            auth_service.store,
            auth_service.store.active(),
            actor,
            campaign_record(name="Following annual campaign"),
        )
        assert receipt.state == "applied"
        successor = replace(harness, campaign=Campaign.objects.get(pk=row["id"]))
        refresh(successor, response_source())
    current = FamilyCampaign.objects.get(campaign=successor.campaign, family_duid=1)
    assert current.pk != refusal.family_id and current.email_eligible
    assert not current.email_deliverable
    assert not RecipientRefusalResolution.objects.exists()


def test_partial_refusal_keeps_other_head_address_deliverable(response_service):
    """Remaining-recipient dispatch belongs to BG-06, not a fabricated boolean edge."""
    harness = activate_response_service(response_service)
    with campaign_clock(harness.campaign.active_configuration.starts_at):
        data = response_source()
        data.members[3]["emailAddress"] = "valid@example.org; remaining@example.org"
        refresh(harness, data)
        remember(refused(harness))
        family = FamilyCampaign.objects.get(family_duid=1)
        assert family.email_deliverable
        with work_transaction():
            selection = source_suppressions(SimpleNamespace(campaign=harness.campaign))
        assert selection.entries == frozenset({(1, "valid@example.org")})


@pytest.mark.parametrize(
    "role",
    [
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    ],
)
def test_refusal_writes_wait_for_compiled_dispatch_owner(response_service, role):
    """No current runtime login receives the future dispatcher write capability."""
    with (
        task_login(role, exact=True),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        with pytest.raises(ProgrammingError, match="permission denied") as error:
            cursor.execute(
                "INSERT INTO stewardship_recipient_refusal(id) VALUES (%s)", (uuid4(),)
            )
        assert error.value.__cause__.sqlstate == "42501"
