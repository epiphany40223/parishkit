"""Retention of the slot decision record (#632).

The scheduler records a skipped or held decision for a refresh slot (see
``production``); nothing reads a decision more than a week after its due
time (the seven-day preview and every lookback are shorter), so the worker's
hourly maintenance removes them eight days after their due time. The
table's guard refuses any younger deletion.
"""

from django.db import connection

from .refresh_models import SLOT_DECISION_RETENTION_DAYS


def prune_slot_decisions():
    """Delete slot decisions due more than eight days ago; return how many.

    Idempotent and bounded by the table's size (a few hundred rows a day at
    most); the worker runs it inside its fenced effect.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM public.stewardship_source_slot_decision "
            "WHERE due_at<statement_timestamp()-make_interval(days=>%s)",
            [SLOT_DECISION_RETENTION_DAYS],
        )
        return cursor.rowcount
