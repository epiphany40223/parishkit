"""Purpose-specific admission for chosen-Family test sends; shared transport sends.

A test message is bound to its Admin ticket, not to a schedule occurrence, so
its dispatch can never move schedule state or write a fulfillment. Dates are
not checked: the Family's Testing credential exists only while the rehearsal
epoch is active, and the Family portal keeps its own date gates.
"""

from uuid import UUID

from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .admission import _scope
from .family_mail_models import FamilyMailTest
from .outbox_models import OutboxMessage
from .ownership import database_now

TICKET_FIELDS = (
    "id",
    "campaign_id",
    "configuration_id",
    "template_id",
    "rehearsal_epoch_id",
    "outbox_id",
    "state",
)


def bound_family_test(message):
    """Bind through the prepared ticket; the ticket itself no longer names a Family."""
    require_work_order()
    if (
        message.purpose != "family_test"
        or message.mode != "testing"
        or message.credential_namespace != "rehearsal"
    ):
        raise PermissionError("Family test delivery identity differs.")
    ticket = (
        FamilyMailTest.objects.only(*TICKET_FIELDS)
        .filter(
            pk=message.semantic_key,
            outbox_id=message.pk,
            campaign_id=message.campaign_id,
            rehearsal_epoch_id=message.rehearsal_epoch_id,
            state="prepared",
        )
        .first()
    )
    if ticket is None:
        raise PermissionError("Family test ticket binding differs.")
    return ticket


def family_test_disposition(message):
    """Cancel a test whose Testing scope is gone; hold on temporary gates only."""
    from .family_mail_dispatch import FamilyDeliveryHeld

    ticket = bound_family_test(message)
    scope = _scope(message.campaign_id)
    runtime, campaign = scope.runtime, scope.campaign
    population = CampaignCredentialState.objects.filter(campaign=campaign).first()
    # Mode, campaign, draft state and epoch are irreversible for this message.
    if (
        runtime.mode != "testing"
        or runtime.current_campaign_id != campaign.pk
        or campaign.state != "draft"
        or population is None
        or population.rehearsal_epoch_id != ticket.rehearsal_epoch_id
        or not RehearsalEpoch.objects.filter(
            pk=ticket.rehearsal_epoch_id, campaign=campaign, state="active"
        ).exists()
    ):
        return "scope_replaced"
    if (
        runtime.restore_review_required
        or population.go_live_gate
        or CampaignWorkGate.objects.filter(campaign=campaign)
        .exclude(state="released")
        .exists()
    ):
        raise FamilyDeliveryHeld("Family test awaits current campaign authority.")
    if (
        population.population_dirty
        or not SourceCurrent.objects.filter(
            snapshot_id=population.source_snapshot_id,
            generation=population.source_generation,
        ).exists()
    ):
        raise FamilyDeliveryHeld("Family test awaits source reconciliation.")
    if not FamilyCampaign.objects.filter(
        pk=message.family_id,
        campaign=campaign,
        source_generation=population.source_generation,
        active=True,
        email_eligible=True,
        email_deliverable=True,
    ).exists():
        raise FamilyDeliveryHeld("Family test awaits deliverable source recipients.")
    if (
        OutboxMessage.objects.filter(
            family_id=message.family_id,
            campaign=campaign,
            mode=message.mode,
            state__in=("submitting", "delivery_unknown"),
        )
        .exclude(pk=message.pk)
        .exists()
    ):
        raise FamilyDeliveryHeld("Family test awaits an unresolved provider outcome.")
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Family test retry is not due.")
    return None


def family_test_template(message):
    """The stable template record the Admin ticket named; dispatch re-renders it."""
    ticket = bound_family_test(message)
    return UUID(
        str(
            ContentVersion.objects.values_list("record_id", flat=True).get(
                pk=ticket.template_id
            )
        )
    )
