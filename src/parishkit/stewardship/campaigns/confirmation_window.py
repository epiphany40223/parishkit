"""Time changes can invalidate a preview even when no database version changes."""

from datetime import datetime, timedelta

from .domain import UTCInterval
from .schedule_evaluation import SchedulePlan

PREVIEW_LIFETIME = timedelta(minutes=5)


def confirmation_deadline(*, observed_at, source_expires_at, interval, plans):
    """Expire before source/DNS, start/close or the next newly due schedule slot.

    Only preview construction calls this enumerator. Final confirmation compares
    its current database instant to the signed deadline and current version
    guards, without enumerating dates or Families again. Use the execution
    owner's civil-time planner, including skipped days and DST gaps/folds.
    """
    if (
        not isinstance(interval, UTCInterval)
        or any(
            type(value) is not datetime or value.utcoffset() != timedelta(0)
            for value in (observed_at, source_expires_at)
        )
        or observed_at
        > datetime.max.replace(tzinfo=observed_at.tzinfo) - PREVIEW_LIFETIME
    ):
        raise ValueError("Confirmation preview requires bounded UTC instants.")
    deadline = min(observed_at + PREVIEW_LIFETIME, source_expires_at, interval.end)
    if observed_at < interval.start:
        deadline = min(deadline, interval.start)
    if deadline <= observed_at:
        raise ValueError("Confirmation readiness is already expired.")
    for plan in plans:
        if not isinstance(plan, SchedulePlan):
            raise TypeError("Confirmation preview requires canonical schedules.")
        cursor = None
        while True:
            page = plan.page(through=deadline, after=cursor, limit=100)
            for slot in page.slots:
                if observed_at < slot.due_at < deadline:
                    deadline = slot.due_at
            if page.exhausted:
                break
            cursor = page.cursor
    return deadline
