"""Purpose-specific receipt admission; shared Family transport owns SMTP fencing."""

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.responses.models import (
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .admission import _scope
from .family_mail_inputs import load_family_mail_source
from .outbox_models import OutboxMessage
from .outbox_storage import _status
from .ownership import database_now
from .receipt_rendering import current_receipt_render

SUBMISSION_FIELDS = (
    "id",
    "family_id",
    "campaign_id",
    "mode",
    "rehearsal_epoch_id",
    "submitted_at",
    "configuration_id",
)


def bound_receipt(message):
    """Bind through immutable intent and submission metadata, never response answers."""
    require_work_order()
    if message.purpose != "receipt" or message.credential_namespace != "none":
        raise PermissionError("Receipt delivery identity differs.")
    if not SubmissionReceiptOccurrence.objects.filter(
        outbox_id=message.pk, submission_id=message.semantic_key, disposition="queued"
    ).exists():
        raise PermissionError("Receipt submission binding differs.")
    return Submission.objects.only(*SUBMISSION_FIELDS).get(
        pk=message.semantic_key,
        campaign_id=message.campaign_id,
        family_id=message.family_id,
        mode="live" if message.mode == "production" else "test",
    )


def receipt_disposition(message):
    """Post-close live receipts remain obligations; only obsolete Testing is cancelled.

    A receipt has no schedule revision or Family access credential. Its original
    rehearsal epoch comes from the submission, never from a new current epoch.
    """
    from .family_mail_dispatch import FamilyDeliveryHeld

    response = bound_receipt(message)
    scope = _scope(message.campaign_id)
    runtime, campaign = scope.runtime, scope.campaign
    population = CampaignCredentialState.objects.get(campaign=campaign)
    if message.mode == "testing" and (
        runtime.mode != "testing"
        or runtime.current_campaign_id != campaign.pk
        or population.rehearsal_epoch_id != response.rehearsal_epoch_id
        or not RehearsalEpoch.objects.filter(
            pk=response.rehearsal_epoch_id, campaign=campaign, state="active"
        ).exists()
    ):
        return "scope_replaced"
    if (
        runtime.current_campaign_id != campaign.pk
        or runtime.mode != message.mode
        or runtime.restore_review_required
        or population.go_live_gate
        or campaign.state
        not in (
            {"draft"}
            if message.mode == "testing"
            else {"scheduled", "active", "closed"}
        )
        or CampaignWorkGate.objects.filter(campaign=campaign)
        .exclude(state="released")
        .exists()
    ):
        raise FamilyDeliveryHeld("Receipt delivery awaits current campaign authority.")
    from .delivery_pause import message_released

    if (
        message.mode == "production"
        and campaign.delivery_paused
        and not message_released(message)
    ):
        return "delivery_paused"
    if (
        population.population_dirty
        or not SourceCurrent.objects.filter(
            snapshot_id=population.source_snapshot_id,
            generation=population.source_generation,
        ).exists()
    ):
        raise FamilyDeliveryHeld("Receipt delivery awaits source reconciliation.")
    if not FamilyCampaign.objects.filter(
        pk=message.family_id,
        campaign=campaign,
        source_generation=population.source_generation,
        active=True,
        email_eligible=True,
        email_deliverable=True,
    ).exists():
        raise FamilyDeliveryHeld(
            "Receipt delivery awaits deliverable source recipients."
        )
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
        raise FamilyDeliveryHeld(
            "Receipt delivery awaits an unresolved provider outcome."
        )
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Receipt delivery retry is not due.")
    return None


def current_receipt_content(message, scope, *, public_origin):
    """Render current source/configuration without reading any credential key."""
    response = bound_receipt(message)
    family = FamilyCampaign.objects.only(
        "id", "campaign_id", "family_duid", "source_generation"
    ).get(pk=message.family_id)
    render = current_receipt_render(
        _status(message).identity,
        response,
        runtime=scope.runtime,
        campaign=scope.campaign,
        source=load_family_mail_source(family),
        public_origin=public_origin,
    )
    mail = FamilyDeliveryMail(
        message.semantic_key,
        render.sender,
        render.reply_to,
        render.routed_recipients,
        render.subject,
        render.html,
        render.text,
    )
    return render, None, mail
