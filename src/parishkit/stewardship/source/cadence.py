"""Bounded source-refresh slots with stable identities and canonical DST rules."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local

from .canonical import canonical_payload

SLOT_NAMESPACE = UUID("ec9c1e19-b158-454d-96fd-1cfe6e721d11")
# How often a scheduled full refresh runs. "daily" uses the configured local
# times; the others use UTC hour or quarter-hour boundaries. The scheduled full
# cause stays "nightly" in stored commands and slot identities.
FREQUENCIES = ("daily", "hourly", "quarter_hour")
# How often the incremental ("delta") refresh runs between full refreshes, on
# UTC quarter-hour or hour boundaries, or not at all (#465).
DELTA_REFRESHES = ("quarter_hour", "hourly", "off")
# At most this many local full-refresh times a day; eight is already about
# half an hour of ParishSoft API time daily.
MAX_FULL_REFRESH_TIMES = 8
DEFAULT_TIME = "02:00"
DEFAULT_DELTA_REFRESH = "quarter_hour"
TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
QUARTER_HOUR = timedelta(minutes=15)
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class RefreshSlot:
    """An exact due slot; its request may coalesce but its provenance does not.

    ``nightly_time`` is the configured local time a full slot fell due at (the
    tick stores it, and the SQL guard checks it is a configured time); a delta
    slot has none.
    """

    cause: str
    due_at: datetime
    slot_key: str
    command_id: UUID
    nightly_time: str | None = None


def canonical_time(value):
    """Whether ``value`` is a canonical local ``HH:MM`` wall time."""
    return type(value) is str and TIME_PATTERN.fullmatch(value) is not None


def canonical_times(nightly_time, full_refresh_times):
    """Whether the times are 1–8 sorted unique times whose earliest is the nightly.

    The earliest listed time is the nightly refresh, the one full refresh that
    never waits for a Family send, and old documents name only it; the form
    derives ``nightly_time`` the same way, so the two settings cannot drift.
    """
    return (
        type(full_refresh_times) in (list, tuple)
        and 1 <= len(full_refresh_times) <= MAX_FULL_REFRESH_TIMES
        and all(canonical_time(value) for value in full_refresh_times)
        and list(full_refresh_times) == sorted(set(full_refresh_times))
        and full_refresh_times[0] == nightly_time
    )


def refresh_settings(settings):
    """The scheduling inputs from applied ParishSoft integration settings.

    Each setting is optional in the stored document, so the defaults live
    here, once, for the scheduler and the Admin pages alike: the nightly time
    alone when no list is stored, and quarter-hour deltas.
    """
    nightly_time = settings.get("nightly_time", DEFAULT_TIME)
    return {
        "nightly_time": nightly_time,
        "frequency": settings.get("full_refresh", "daily"),
        "full_refresh_times": tuple(
            settings.get("full_refresh_times") or (nightly_time,)
        ),
        "delta_refresh": settings.get("delta_refresh", DEFAULT_DELTA_REFRESH),
    }


def _validate(*, nightly_time, frequency, full_refresh_times, delta_refresh):
    """Refuse non-canonical cadence inputs; return the effective times tuple."""
    if full_refresh_times is None:
        full_refresh_times = (nightly_time,)
    if (
        not canonical_time(nightly_time)
        or not canonical_times(nightly_time, full_refresh_times)
        or frequency not in FREQUENCIES
        or delta_refresh not in DELTA_REFRESHES
    ):
        raise ValueError("Refresh scheduling requires canonical cadence inputs.")
    return tuple(full_refresh_times)


def _occurrences(day, wall, timezone):
    """The instants a wall time falls at on ``day`` and the days either side."""
    return [
        resolve_local(datetime.combine(day + timedelta(days=offset), wall), timezone)
        for offset in (-1, 0, 1)
    ]


def _floor(now, step):
    """The latest UTC boundary of ``step`` (a divisor of an hour) at or before now."""
    minutes = int(step.total_seconds()) // 60
    return now.replace(minute=now.minute // minutes * minutes, second=0, microsecond=0)


def covered_by_full(frequency, delta_refresh):
    """Whether every delta slot coincides with a full refresh, so none is needed.

    A full refresh every quarter hour covers quarter-hour and hourly deltas;
    an hourly full refresh covers hourly deltas.
    """
    return frequency == "quarter_hour" or (
        frequency == "hourly" and delta_refresh == "hourly"
    )


def _latest_full(now, timezone, times, nightly_time):
    """The latest configured local time due at or before ``now`` and that time.

    Every configured time is resolved for the local day and the day before,
    through the shared DST resolver, and the latest instant not after ``now``
    wins. Times inside a spring-forward gap all resolve to the first real
    instant, so they tie; the nightly time wins a tie (its tick must still
    exist, and it is the refresh a Family send never holds), then the later
    wall time, so the choice is deterministic.
    """
    day = now.astimezone(ZoneInfo(timezone)).date()
    candidates = [
        (due, value == nightly_time, value)
        for value in times
        for due in _occurrences(day, time.fromisoformat(value), timezone)[:2]
        if due <= now
    ]
    due, _, value = max(candidates)
    return due, value


def due_slots(
    *,
    now,
    timezone,
    nightly_time,
    scope_fingerprint,
    frequency="daily",
    full_refresh_times=None,
    delta_refresh=DEFAULT_DELTA_REFRESH,
):
    """Select the latest full-refresh and delta slot, never an unbounded backlog.

    ``frequency`` chooses the full refresh's slot: for "daily" the latest of
    the configured local ``full_refresh_times`` (the nightly time alone by
    default), else the latest UTC hour or quarter hour. ``delta_refresh``
    chooses the delta slot: the latest UTC quarter hour or hour, or none.

    Full comes first so a simultaneously due delta can coalesce into it. After
    downtime, one current coherent observation covers stale refresh slots;
    unlike delivery obligations, missed read-only polls need no individual replay.
    Identities ignore unrelated configuration edits but include the actual source
    scope, timezone and cadence. Persist them before emitting broker hints.
    """
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() is None
        or type(timezone) is not str
        or type(scope_fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", scope_fingerprint) is None
    ):
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    times = _validate(
        nightly_time=nightly_time,
        frequency=frequency,
        full_refresh_times=full_refresh_times,
        delta_refresh=delta_refresh,
    )
    now = now.astimezone(UTC)
    if frequency == "daily":
        full, chosen = _latest_full(now, timezone, times, nightly_time)
    else:
        # A frequent full refresh keeps the nightly time as its recorded
        # time, as it always has; the time plays no part in its slot.
        full = _floor(now, HOUR if frequency == "hourly" else QUARTER_HOUR)
        chosen = nightly_time
    slots = [("nightly", full, chosen)]
    if delta_refresh != "off" and not covered_by_full(frequency, delta_refresh):
        step = HOUR if delta_refresh == "hourly" else QUARTER_HOUR
        slots.append(("delta", _floor(now, step), None))
    result = []
    for cause, due, value in slots:
        _, key = canonical_payload(
            {
                "schema": "source-refresh-slot-v1",
                "scope_fingerprint": scope_fingerprint,
                "timezone": timezone,
                "nightly_time": value,
                "cause": cause,
                "due_at": due.isoformat(),
            }
        )
        result.append(RefreshSlot(cause, due, key, uuid5(SLOT_NAMESPACE, key), value))
    return tuple(result)


def next_full_at(
    *, now, timezone, nightly_time, frequency="daily", full_refresh_times=None
):
    """When the next scheduled full refresh falls due, for the Admin banner.

    Mirrors ``due_slots``: "daily" uses the next of the configured local
    times (DST-resolved), the others the next UTC hour or quarter hour. It
    only informs Admins; scheduling itself never reads this.
    """
    times = _validate(
        nightly_time=nightly_time,
        frequency=frequency,
        full_refresh_times=full_refresh_times,
        delta_refresh=DEFAULT_DELTA_REFRESH,
    )
    now = now.astimezone(UTC)
    if frequency != "daily":
        step = HOUR if frequency == "hourly" else QUARTER_HOUR
        return _floor(now, step) + step
    day = now.astimezone(ZoneInfo(timezone)).date()
    return min(
        due
        for value in times
        for due in _occurrences(day, time.fromisoformat(value), timezone)[1:]
        if due > now
    )


def longest_gap(*, frequency, full_refresh_times, delta_refresh):
    """The longest wait between scheduled refreshes of any kind, as a timedelta.

    With deltas on, their interval; with deltas off, the longest interval
    between consecutive full-refresh times around the clock (daylight-saving
    shifts ignored). Plus the lateness margin, it is how long without a
    successful attempt the connection line waits before it reads "not
    checked" (``data_age.connection_threshold``, #510).
    """
    if frequency == "quarter_hour":
        return QUARTER_HOUR
    if delta_refresh == "quarter_hour":
        return QUARTER_HOUR
    if frequency == "hourly" or delta_refresh == "hourly":
        return HOUR
    minutes = sorted(
        int(value[:2]) * 60 + int(value[3:]) for value in full_refresh_times
    )
    gaps = [b - a for a, b in zip(minutes, minutes[1:], strict=False)]
    gaps.append(24 * 60 - minutes[-1] + minutes[0])
    return timedelta(minutes=max(gaps))
