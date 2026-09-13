"""Deterministic UTC source-retention anchors, independent of cleanup execution."""

from datetime import UTC, datetime, timedelta
from uuid import UUID


def retention_cutoffs(now):
    """Use UTC instants and a calendar year; February 29 maps to February 28."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Source retention requires an aware cutoff instant.")
    now = now.astimezone(UTC)
    try:
        yearly = now.replace(year=now.year - 1)
    except ValueError:
        yearly = now.replace(year=now.year - 1, day=28)
    return now - timedelta(days=90), yearly


def retention_anchors(stamps, *, now):
    """Select UUIDs from (UUID, promoted UTC instant, generation) metadata tuples.

    Select from permanent promotion history, independently of iteration order.
    Current/protected snapshots are additional mandatory keeps. Tied instants
    use monotonic generation, not nondeterministic UUID order.
    """
    recent, yearly = retention_cutoffs(now)
    retained, buckets = set(), {}
    for identifier, instant, generation in stamps:
        if (
            not isinstance(identifier, UUID)
            or type(generation) is not int
            or generation < 1
            or not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() is None
        ):
            raise ValueError("Source retention requires valid promotion metadata.")
        instant = instant.astimezone(UTC)
        if instant >= recent:
            retained.add(identifier)
            continue
        key = (
            ("day", instant.date())
            if instant >= yearly
            else ("month", instant.year, instant.month)
        )
        prior = buckets.get(key)
        if prior is None or (instant, generation) > prior[:2]:
            buckets[key] = (instant, generation, identifier)
    return retained | {item[2] for item in buckets.values()}
