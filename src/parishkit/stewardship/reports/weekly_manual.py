"""Send a weekly report now: Send now of the converted Weekly report (#1786).

Until the Report emails page's Send now ships (report-emails spec, delivery
plan item 5), Emailed reports' "Send a weekly report now" and ``pk-admin
digest weekly-request`` send the report the conversion made from the Weekly
digest, through Send now (``report_email_manual.request_send_now``): its
database guard checks the report's current revision and that no other send
of it is open, and a replay returns the same send.
"""

from uuid import UUID

from parishkit.stewardship.accounts.report_rows import WEEKLY, digest_kind
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition

from .report_email_manual import request_send_now


def weekly_report(campaign_id):
    """The campaign's converted Weekly report definition, or None."""
    for definition in ScheduleDefinition.objects.select_related(
        "current_revision"
    ).filter(campaign_id=campaign_id, kind="report", current_revision__isnull=False):
        if digest_kind(definition.current_revision.values) == WEEKLY:
            return definition
    return None


def request_manual_report(store, user_id, campaign_id, *, command_id, configuration_id):
    """Send the converted Weekly report now, once per command.

    A replay of a recorded command names the report and revision it
    recorded, so it returns the same send even after the report changed.
    Raises ``ScheduleDefinition.DoesNotExist`` when the campaign has none.
    """
    from .report_email_models import ReportEmailManualRequest

    if not isinstance(campaign_id, UUID):
        raise ValueError("Manual reports require canonical identities.")
    recorded = (
        ReportEmailManualRequest.objects.filter(pk=command_id)
        .values_list("definition_id", "revision_id")
        .first()
    )
    if recorded is None:
        definition = weekly_report(campaign_id)
        if definition is None:
            raise ScheduleDefinition.DoesNotExist("The campaign has no Weekly report.")
        recorded = (definition.pk, definition.current_revision_id)
    return request_send_now(
        store,
        user_id,
        campaign_id,
        *recorded,
        command_id=command_id,
        configuration_id=configuration_id,
    )
