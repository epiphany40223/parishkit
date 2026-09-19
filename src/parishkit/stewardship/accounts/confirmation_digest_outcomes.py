"""Count actual digest preparation records, never rescan live report contents.

Catch-up plans slots, not recipient content. The existing daily/weekly owners
produce durable recipient intents later; this reader compares those real message
records with the preview instead of predicting recipients inside catch-up locks.
"""

from django.db.models import F

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestRecipient,
)
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
    WeeklyManualRequest,
)


def digest_outcomes(demand):
    """Return current cutoff selections' actual message totals and preparation state.

    Exclude later ordinary dates, Testing, other campaigns and manual weekly
    reports. Counts include retained created messages, not provider attempts or
    delivery claims. Revision replacement selects its current outcome; immutable
    recipient records remain the evidence, not mutable additional-information.
    """
    selected = ScheduleOccurrence.objects.filter(
        definition__campaign_id=demand.campaign_id,
        revision_id=F("definition__current_revision_id"),
        mode="production",
        target="admins",
        due_at__lte=demand.cutoff,
        created_at__gte=demand.created_at,
    ).exclude(state="coalesced")
    results = {}
    for key, kind, model, recipients, path in (
        (
            "daily_messages",
            "daily_digest",
            DailyDigestPreparation,
            DailyDigestRecipient,
            "ready__snapshot__preparation",
        ),
        (
            "weekly_messages",
            "weekly_digest",
            WeeklyDigestPreparation,
            WeeklyDigestRecipient,
            "snapshot__preparation",
        ),
    ):
        occurrences = selected.filter(definition__kind=kind).values("id")
        preparations = model.objects.filter(
            campaign_id=demand.campaign_id,
            mode="production",
            rehearsal_epoch_id__isnull=True,
            occurrence_id__in=occurrences,
        )
        if kind == "weekly_digest":
            preparations = preparations.exclude(
                pk__in=WeeklyManualRequest.objects.values("id")
            )
        actual = recipients.objects.filter(
            **{f"{path}__in": preparations}, outbox__isnull=False
        ).count()
        complete = bool(demand.completed_at) and (
            not preparations.exclude(phase__in=("complete", "cancelled")).exists()
            and not selected.filter(definition__kind=kind)
            .exclude(pk__in=preparations.values("occurrence_id"))
            .exists()
        )
        results[key] = {"actual": actual, "complete": complete}
    return results
