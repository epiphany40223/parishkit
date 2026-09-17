"""Daily report admission without household credentials or recaptured facts."""

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
from parishkit.stewardship.digest_delivery import DigestDeliveryMail
from parishkit.stewardship.reports.digest_fanout import retained_render
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestReady,
    DailyDigestRecipient,
    DailyDigestSnapshot,
)

from .outbox_storage import _status
from .ownership import database_now


def bound_digest(message):
    """Use only opaque identity columns so scheduler recovery sees no report PII."""
    require_work_order()
    if (
        message.purpose != "daily_digest"
        or message.family_id is not None
        or message.credential_namespace != "none"
    ):
        raise PermissionError("Daily delivery identity differs.")
    recipient = DailyDigestRecipient.objects.only("id", "ready_id", "outbox_id").get(
        pk=message.semantic_key, outbox_id=message.pk
    )
    ready = DailyDigestReady.objects.only("id", "snapshot_id").get(
        pk=recipient.ready_id
    )
    snapshot = DailyDigestSnapshot.objects.only("id", "preparation_id").get(
        pk=ready.snapshot_id
    )
    return DailyDigestPreparation.objects.get(
        pk=snapshot.preparation_id, campaign_id=message.campaign_id, mode=message.mode
    )


def digest_disposition(message, *, check_recipient=False):
    """Hold temporary policy loss; obsolete revision/epoch or revoked Admin cancels.

    Scheduler hints deliberately defer the private recipient check to MAIL's
    execution boundary. A hint is never authority to read a recipient or send.
    """
    from .family_mail_dispatch import FamilyDeliveryHeld

    preparation = bound_digest(message)
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
        raise FamilyDeliveryHeld("Daily report recipient preparation is unfinished.")
    occurrence = ScheduleOccurrence.objects.get(pk=preparation.occurrence_id)
    if RestoreDeliveryHold.objects.filter(
        definition_id=preparation.definition_id,
        mode=preparation.mode,
        target="admins",
        slot=occurrence.slot,
        state__in=("unreviewed", "assumed_delivered"),
    ).exists():
        raise FamilyDeliveryHeld("Daily delivery awaits restore reconciliation.")
    try:
        scope, _ = _planning_scope(message.campaign_id, postclose=True)
    except PermissionError:
        raise FamilyDeliveryHeld(
            "Daily delivery awaits current campaign authority."
        ) from None
    if check_recipient:
        recipient = DailyDigestRecipient.objects.get(outbox_id=message.pk)
        if not AddressRule.objects.filter(
            configuration_id=runtime.active_configuration_id,
            email=recipient.address,
            roles__contains=["administrator"],
        ).exists():
            return "recipient_revoked"
    if message.mode == "production" and scope.campaign.delivery_paused:
        return "delivery_paused"
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Daily delivery retry is not due.")
    return None


def current_digest_content(message, scope):
    """Render current approved envelope around the original immutable report."""
    preparation = bound_digest(message)
    recipient = DailyDigestRecipient.objects.select_related("ready").get(
        outbox_id=message.pk
    )
    ready = recipient.ready
    revision = ScheduleRevision.objects.get(pk=preparation.revision_id)
    render = retained_render(
        _status(message).identity, ready, scope, revision, recipient.address
    )
    return (
        render,
        None,
        DigestDeliveryMail(
            message.semantic_key,
            render.sender,
            render.reply_to,
            render.routed_recipients,
            render.subject,
            render.html,
            render.text,
            bytes(ready.chart),
        ),
    )
