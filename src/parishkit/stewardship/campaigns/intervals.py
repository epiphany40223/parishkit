"""Canonical campaign and reporting boundaries, including skipped local dates."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import LocalDayInterval, UTCInterval


def resolve_local(local: datetime, timezone: str) -> datetime:
    """Resolve a wall time using the earlier fold or the first instant after a gap.

    Round trips distinguish real wall times from ZoneInfo's hypothetical gap
    offsets. For a gap those offsets bracket the UTC transition: binary search
    finds its exact microsecond rather than assuming a one-hour DST adjustment.
    This also handles a whole skipped date without allocating daily occurrences.
    """
    if type(local) is not datetime or local.tzinfo is not None:
        raise ValueError("A naive local datetime is required.")
    if type(timezone) is not str:
        raise ValueError("An IANA timezone is required.")
    try:
        zone = ZoneInfo(timezone)
        candidates = sorted(
            {local.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)}
        )
        valid = [
            instant
            for instant in candidates
            if instant.astimezone(zone).replace(tzinfo=None) == local
        ]
        if valid:
            return valid[0]
        low, high = candidates
        while high - low > timedelta(microseconds=1):
            middle = low + (high - low) // 2
            if middle.astimezone(zone).replace(tzinfo=None) < local:
                low = middle
            else:
                high = middle
        return high
    except (ZoneInfoNotFoundError, OverflowError, ValueError):
        raise ValueError(
            "Local time cannot be resolved in the supplied timezone."
        ) from None


def local_day(day: date, timezone: str) -> LocalDayInterval:
    """Return the half-open local day; a skipped date has an empty UTC interval."""
    if type(day) is not date or day == date.max:
        raise ValueError("A date with a representable following day is required.")
    return LocalDayInterval(
        day,
        timezone,
        UTCInterval(
            resolve_local(datetime.combine(day, time.min), timezone),
            resolve_local(
                datetime.combine(day + timedelta(days=1), time.min), timezone
            ),
        ),
    )


def campaign_interval(start: date, end: date, timezone: str) -> UTCInterval:
    """Resolve inclusive campaign dates once, with end strictly after start."""
    if type(start) is not date or type(end) is not date or end <= start:
        raise ValueError("Campaign end date must be after its start date.")
    return UTCInterval(
        local_day(start, timezone).interval.start, local_day(end, timezone).interval.end
    )


def financial_period_end(start: date) -> date:
    """Return the day before the first anniversary, clipping leap-day anniversaries."""
    if type(start) is not date or start.year == 9999:
        raise ValueError("A date with a representable first anniversary is required.")
    try:
        anniversary = start.replace(year=start.year + 1)
    except ValueError:
        anniversary = start.replace(year=start.year + 1, day=28)
    return anniversary - timedelta(days=1)
