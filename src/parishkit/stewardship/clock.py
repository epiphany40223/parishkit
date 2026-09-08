"""Injectable UTC clock contract for domain policies and deterministic scenarios."""

from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """A source of aware UTC instants; no browser timezone or implicit local time."""

    def now(self) -> datetime:
        """Return the instant against which one policy decision is evaluated."""
        ...


class SystemClock:
    """Default process clock; transaction-authoritative DB adapters come with DAT-01."""

    def now(self) -> datetime:
        """Read the process clock explicitly in UTC."""
        return datetime.now(UTC)


def utc_instant(value: datetime) -> datetime:
    """Reject naive or non-datetime values before normalizing a supplied instant."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("an aware datetime is required")
    return value.astimezone(UTC)


def in_timezone(instant: datetime, zone: str) -> datetime:
    """Project an existing instant into an explicit parish or browser IANA zone.

    This does not resolve ambiguous/nonexistent wall times or campaign bounds;
    DOM-02 owns that resolver. Capture one instant and project it separately
    for parish-day selection and browser-local display.
    """
    return utc_instant(instant).astimezone(ZoneInfo(zone))
