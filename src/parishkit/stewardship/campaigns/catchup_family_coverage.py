"""Forward interrupted Family selections without reviving old occurrences."""

from django.db.models import Q

from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.storage import StorageInvariantError

from .catchup_ownership import claim_event
from .schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleRecoveryReplacement,
)


def forward_family_coverage(demand, claim, family_id, selected_id):
    """Retain old coalescing evidence and attach it to the current safe selection.

    Configuration forbids reminders without an initial invitation. Removing
    all Family schedules therefore preserves history without a successor;
    replacing the initial can forward coverage to its new ordinary occurrence.
    """
    if selected_id is None:
        return
    target = f"family:{family_id}"
    prior = list(
        ScheduleOccurrence.objects.filter(
            definition__campaign_id=demand.campaign_id,
            definition__kind__in=("initial", "reminder"),
            mode="production",
            target=target,
            state="skipped",
            reason__in=("schedule_replaced", "schedule_removed"),
            recovery_replacement__isnull=True,
        )
        .filter(
            Q(
                pk__in=ScheduleFulfillment.objects.filter(
                    mode="production", target=target, disposition="coalesced"
                ).values("occurrence_id")
            )
            | Q(
                pk__in=ScheduleRecoveryReplacement.objects.filter(demand=demand).values(
                    "replacement_id"
                )
            )
        )
        .order_by("id")[:101]
    )
    if len(prior) > 100:
        raise StorageInvariantError("Family recovery predecessors exceed group bounds.")
    correlation = claim_event(claim)
    for row in prior:
        lock_task_claim(claim)
        ScheduleRecoveryReplacement.objects.create(
            demand=demand,
            previous=row,
            replacement_id=selected_id,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
