"""Count configured next slots, without promising recipients or provider effects."""

from collections import Counter

from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.storage import StorageInvariantError


def next_due(campaign, now):
    """Return the earliest configured instant and number of slots by mail type.

    Message counts remain the actual outbox inventory. A future weekly report
    may be empty and future Family eligibility may change; this projection is
    explicitly schedules, never a prediction that those recipients will be sent.
    """
    definitions = list(
        ScheduleDefinition.objects.filter(
            campaign=campaign, current_revision__isnull=False
        )
        .select_related("current_revision")
        .order_by("id")[:103]
    )
    if len(definitions) > 102:
        raise StorageInvariantError("Delivery schedules exceed configuration bounds.")
    future = []
    for definition in definitions:
        plan = SchedulePlan.from_values(
            definition.current_revision.values, campaign.active_configuration.values
        )
        slot = plan.next_slot(after=now)
        if slot is not None:
            future.append((slot.due_at, definition.kind))
    if not future:
        return {"at": None, "count": 0, "types": {}}
    due = min(instant for instant, _ in future)
    kinds = Counter(kind for instant, kind in future if instant == due)
    return {
        "at": due.isoformat(),
        "count": sum(kinds.values()),
        "types": dict(sorted(kinds.items())),
    }
