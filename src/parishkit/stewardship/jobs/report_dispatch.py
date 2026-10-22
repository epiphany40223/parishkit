"""Mail dispatch of one report email message (#1786), from its kept snapshot.

A report send's fanout kept, per cohort address, a message whose render is
the snapshot's email to that address (``reports.report_email_fanout``).
Dispatch rechecks the send's scope and the recipient before each
submission (report-emails spec, "Resolving recipients") and sends the
snapshot's bytes again on a retry, including its retained chart images
(spec, "Report snapshot"). The database's write gate repeats every check
(``stewardship_report_email_dispatch_write_v1``).
"""

import base64

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.digest_delivery import DigestDeliveryMail
from parishkit.stewardship.reports.report_email_cohort import still_recipient
from parishkit.stewardship.reports.report_email_fanout import recipient_render
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailPreparation,
    ReportEmailRecipient,
    ReportEmailSnapshot,
)
from parishkit.stewardship.reports.report_email_ownership import report_scope
from parishkit.stewardship.web.digest_content import CHART_ID, SERVER_CHART_IDS
from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

from .ownership import database_now

PURPOSE = "admin_report"


def bound_report(message):
    """The message's preparation, from opaque identity columns only.

    Scheduler recovery reads this too, so it never touches the snapshot's
    content or the recipient's address.
    """
    require_work_order()
    if (
        message.purpose != PURPOSE
        or message.family_id is not None
        or message.credential_namespace != "none"
    ):
        raise PermissionError("Report delivery identity differs.")
    recipient = ReportEmailRecipient.objects.only("id", "snapshot_id", "outbox_id").get(
        pk=message.semantic_key, outbox_id=message.pk
    )
    snapshot = ReportEmailSnapshot.objects.only("id", "preparation_id").get(
        pk=recipient.snapshot_id
    )
    return ReportEmailPreparation.objects.get(
        pk=snapshot.preparation_id, campaign_id=message.campaign_id, mode=message.mode
    )


def report_disposition(message, *, check_recipient=False):
    """Cancel obsolete or revoked work; hold what is only temporarily blocked.

    A send whose report, revision, campaign, mode or Testing epoch changed,
    or whose occurrence is settled, is "scope_replaced"; a recipient no
    longer one (removed from the report, or no longer an Administrator or
    Staff member) is "recipient_revoked". Restore review, the work gates,
    the live delivery pause and a retry not yet due hold the message.
    Scheduler hints leave the private recipient check to mail dispatch.
    """
    from .family_mail_dispatch import FamilyDeliveryHeld

    preparation = bound_report(message)
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
        raise FamilyDeliveryHeld("Report recipient preparation is unfinished.")
    occurrence = ScheduleOccurrence.objects.get(pk=preparation.occurrence_id)
    if RestoreDeliveryHold.objects.filter(
        definition_id=preparation.definition_id,
        mode=preparation.mode,
        target="admins",
        slot=occurrence.slot,
        state__in=("unreviewed", "assumed_delivered"),
    ).exists():
        raise FamilyDeliveryHeld("Report delivery awaits restore reconciliation.")
    try:
        scope, _ = report_scope(message.campaign_id)
    except PermissionError:
        raise FamilyDeliveryHeld(
            "Report delivery awaits current campaign authority."
        ) from None
    if check_recipient:
        recipient = ReportEmailRecipient.objects.only("address", "kind", "ref").get(
            outbox_id=message.pk
        )
        member = {"address": recipient.address, "kind": recipient.kind}
        member["ref"] = recipient.ref
        if not still_recipient(preparation.definition_id, member):
            return "recipient_revoked"
    from .delivery_pause import message_released

    if (
        message.mode == "production"
        and scope.campaign.delivery_paused
        and not message_released(message)
    ):
        return "delivery_paused"
    if message.not_before > database_now():
        raise FamilyDeliveryHeld("Report delivery retry is not due.")
    return None


def _images(snapshot):
    """The snapshot's retained images, ``(content id, PNG bytes)``, as kept."""
    return tuple(
        (name, base64.b64decode(data, validate=True)) for name, data in snapshot.images
    )


def delivery_mail(semantic_key, render, images):
    """The private transport envelope of one compiled report.

    A report with the participation chart travels as the Daily digest's
    chart mail did, with the server-drawn charts beside it; one with no
    image as the Weekly digest's text report did. These are the only two
    shapes a report's blocks compile to in this release: the server-drawn
    charts are drawn only beside the participation chart.
    """
    names = [name for name, _ in images]
    if not images:
        return WeeklyDeliveryMail(
            semantic_key,
            render.sender,
            render.reply_to,
            render.routed_recipients,
            render.subject,
            render.html,
            render.text,
        )
    if names[0] != CHART_ID or any(name not in SERVER_CHART_IDS for name in names[1:]):
        raise ValueError("A report's images must follow its participation chart.")
    return DigestDeliveryMail(
        semantic_key,
        render.sender,
        render.reply_to,
        render.routed_recipients,
        render.subject,
        render.html,
        render.text,
        images[0][1],
        tuple(images[1:]),
    )


def current_report_content(message, scope):
    """The snapshot's email to this recipient, routed as its mode requires."""
    bound_report(message)
    recipient = ReportEmailRecipient.objects.select_related("snapshot").get(
        outbox_id=message.pk
    )
    snapshot = recipient.snapshot
    version = scope.runtime.active_configuration
    render = recipient_render(
        snapshot,
        {"address": recipient.address, "kind": recipient.kind, "ref": recipient.ref},
        configuration_id=version.pk,
        email=AppliedIntegration.objects.get(configuration=version, kind="email"),
        mode=message.mode,
        testing=scope.runtime.testing_recipient,
    )
    return render, None, delivery_mail(message.semantic_key, render, _images(snapshot))
