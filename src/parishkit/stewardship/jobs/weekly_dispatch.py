"""Weekly admission reads retained per-Admin bytes, never raw live requests."""

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.family_schedule_planning import _planning_scope
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold, ScheduleRevision
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.reports.weekly_digest import WeeklyDigestContent
from parishkit.stewardship.reports.weekly_fanout import retained_render
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
    WeeklyDigestSnapshot,
)
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

from .outbox_storage import _status
from .ownership import database_now


def bound_weekly(message):
    """Opaque relationship columns suffice for scheduler recovery, without PII."""
    require_work_order()
    if (
        message.purpose != "weekly_digest"
        or message.family_id is not None
        or message.credential_namespace != "none"
    ):
        raise PermissionError("Weekly delivery identity differs.")
    recipient = WeeklyDigestRecipient.objects.only(
        "id", "snapshot_id", "outbox_id"
    ).get(pk=message.semantic_key, outbox_id=message.pk)
    snapshot = WeeklyDigestSnapshot.objects.only("id", "preparation_id").get(
        pk=recipient.snapshot_id
    )
    return WeeklyDigestPreparation.objects.get(
        pk=snapshot.preparation_id, campaign_id=message.campaign_id, mode=message.mode
    )


def weekly_disposition(message, *, check_recipient=False):
    """Recheck current authority before every submission, including prepared retries."""
    from .family_mail_dispatch import FamilyDeliveryHeld

    preparation = bound_weekly(message)
    runtime = SystemConfiguration.objects.get()
    if (
        runtime.current_campaign_id != preparation.campaign_id
        or runtime.mode != preparation.mode
        or not ScheduleDefinition.objects.filter(
            pk=preparation.definition_id, current_revision_id=preparation.revision_id
        ).exists()
        or (
            preparation.mode == "testing"
            and not CampaignCredentialState.objects.filter(
                campaign_id=preparation.campaign_id,
                rehearsal_epoch_id=preparation.rehearsal_epoch_id,
            ).exists()
        )
        or not ScheduleOccurrence.objects.filter(
            pk=preparation.occurrence_id, state__in=("pending", "running")
        ).exists()
    ):
        return "scope_replaced"
    if preparation.phase != "complete":
        raise FamilyDeliveryHeld("Weekly recipient preparation is unfinished.")
    occurrence = ScheduleOccurrence.objects.get(pk=preparation.occurrence_id)
    if RestoreDeliveryHold.objects.filter(
        definition_id=preparation.definition_id,
        mode=preparation.mode,
        target="admins",
        slot=occurrence.slot,
        state__in=("unreviewed", "assumed_delivered"),
    ).exists():
        raise FamilyDeliveryHeld("Weekly delivery awaits restore reconciliation.")
    try:
        scope, _ = _planning_scope(message.campaign_id, postclose=True)
    except PermissionError:
        raise FamilyDeliveryHeld(
            "Weekly delivery awaits current campaign authority."
        ) from None
    if check_recipient:
        recipient = WeeklyDigestRecipient.objects.only("address").get(
            outbox_id=message.pk
        )
        if not AddressRule.objects.filter(
            configuration_id=runtime.active_configuration_id,
            email=recipient.address,
            roles__contains=["administrator"],
        ).exists():
            return "recipient_revoked"
    from .delivery_pause import message_released

    if (
        message.mode == "production"
        and scope.campaign.delivery_paused
        and not message_released(message)
    ):
        return "delivery_paused"
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Weekly delivery retry is not due.")
    return None


def current_weekly_content(message, scope):
    """Reuse this Admin's frozen subset under current approved prose and routing."""
    preparation = bound_weekly(message)
    recipient = WeeklyDigestRecipient.objects.get(outbox_id=message.pk)
    content = WeeklyDigestContent(recipient.subject, recipient.html, recipient.text)
    render = retained_render(
        _status(message).identity,
        content,
        scope,
        ScheduleRevision.objects.get(pk=preparation.revision_id),
        recipient.address,
    )
    return (
        render,
        None,
        WeeklyDeliveryMail(
            message.semantic_key,
            render.sender,
            render.reply_to,
            render.routed_recipients,
            render.subject,
            render.html,
            render.text,
        ),
    )
