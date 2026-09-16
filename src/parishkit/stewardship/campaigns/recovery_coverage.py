"""Bounded original-date traversal across immutable recovery replacements."""

from datetime import date
from uuid import UUID

from django.db import connection


def covered_dates(occurrence_id, *, after=None, limit=100):
    """Return exact retained date identities; these are not pinned report facts.

    Follow predecessor edges, not just the latest aggregate's direct coverage.
    UNION prevents accidental cycles from making a corrupted inventory loop;
    ordinary constraints only permit forward, current-revision replacement.
    """
    if (
        not isinstance(occurrence_id, UUID)
        or (after is not None and type(after) is not date)
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("Recovery dates require a canonical bounded cursor.")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE lineage(id) AS (
                SELECT id FROM stewardship_schedule_occurrence WHERE id=%s
                UNION
                SELECT r.previous_id FROM stewardship_recovery_replacement r
                JOIN lineage parent ON parent.id=r.replacement_id
            ), dates(slot) AS (
                SELECT f.slot FROM stewardship_schedule_fulfillment f
                JOIN lineage parent ON parent.id=f.occurrence_id
                WHERE f.mode='production' AND f.target='admins'
                UNION
                SELECT o.slot FROM stewardship_schedule_occurrence o
                JOIN lineage parent ON parent.id=o.id
                WHERE o.mode='production' AND o.target='admins'
            )
            SELECT slot FROM dates WHERE slot ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                AND (%s::text IS NULL OR slot>%s)
            ORDER BY slot LIMIT %s
            """,
            [
                occurrence_id,
                after.isoformat() if after else None,
                after.isoformat() if after else None,
                limit,
            ],
        )
        return tuple(date.fromisoformat(row[0]) for row in cursor.fetchall())
