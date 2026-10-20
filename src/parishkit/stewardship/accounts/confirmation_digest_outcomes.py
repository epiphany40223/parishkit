"""Count actual report send records, never rescan live report contents.

Catch-up plans slots, not recipient content. The report email pipeline
(#1786, which replaced the Daily and Weekly digests) produces durable
recipient intents later; this reader compares those real message records
with the preview instead of predicting recipients inside catch-up locks. As
the preview does (``campaigns.readiness_digests``), a report as of the end of
the previous day counts as "daily", one as of when it is made as "weekly".
"""

from django.db.models import F

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.reports.report_email_models import (
    ReportEmailManualRequest,
    ReportEmailPreparation,
    ReportEmailRecipient,
)


def digest_outcomes(demand):
    """Return current cutoff selections' actual message totals and preparation state.

    Exclude later ordinary dates, Testing, other campaigns and Send now
    reports. Counts include retained created messages, not provider attempts
    or delivery claims. Revision replacement selects its current outcome;
    immutable recipient records remain the evidence.
    """
    selected = ScheduleOccurrence.objects.filter(
        definition__campaign_id=demand.campaign_id,
        definition__kind="report",
        revision_id=F("definition__current_revision_id"),
        mode="production",
        target="admins",
        due_at__lte=demand.cutoff,
        created_at__gte=demand.created_at,
    ).exclude(state="coalesced")
    results = {}
    for key, as_of in (
        ("daily_messages", "previous_day_end"),
        ("weekly_messages", "send_time"),
    ):
        occurrences = selected.filter(revision__values__as_of=as_of)
        preparations = ReportEmailPreparation.objects.filter(
            campaign_id=demand.campaign_id,
            mode="production",
            rehearsal_epoch_id__isnull=True,
            occurrence_id__in=occurrences.values("id"),
        ).exclude(pk__in=ReportEmailManualRequest.objects.values("id"))
        actual = ReportEmailRecipient.objects.filter(
            snapshot__preparation__in=preparations, outbox__isnull=False
        ).count()
        complete = bool(demand.completed_at) and (
            not preparations.exclude(phase__in=("complete", "cancelled")).exists()
            and not occurrences.exclude(
                pk__in=preparations.values("occurrence_id")
            ).exists()
        )
        results[key] = {"actual": actual, "complete": complete}
    return results
