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
# How often the incremental ("delta", or quick) refresh runs between full
# refreshes: on UTC quarter-hour or hour boundaries, or not at all (#465), or
# at the listed local ``quick_refresh_times`` (``times``, #632).
DELTA_REFRESHES = ("quarter_hour", "hourly", "off", "times")
# At most this many daily times, full and quick together: one every quarter
# hour. The 15-minute spacing rule already implies it; there is no other cap
# (#632), the settings page shows the cost instead.
MAX_DAILY_TIMES = 96
# A schedule saved without its rules (every schedule before #632) keeps the
# old limit of eight full times and no listed quick times: it behaves as it
# always has.
MAX_LEGACY_FULL_TIMES = 8
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


def canonical_list(values):
    """Whether ``values`` is a non-empty sorted list of unique ``HH:MM`` times.

    At most ``MAX_DAILY_TIMES`` of them, the most a day can hold 15 minutes
    apart.
    """
    return (
        type(values) in (list, tuple)
        and 1 <= len(values) <= MAX_DAILY_TIMES
        and all(canonical_time(value) for value in values)
        and list(values) == sorted(set(values))
    )


def canonical_times(nightly_time, full_refresh_times):
    """Whether the full times are a canonical list whose earliest is the nightly.

    The earliest listed time is the nightly refresh, the one full refresh that
    never waits for a Family send, and old documents name only it; the form
    derives ``nightly_time`` the same way, so the two settings cannot drift.
    The cap of eight is gone (#632): the spacing rule bounds the list.
    """
    return canonical_list(full_refresh_times) and full_refresh_times[0] == nightly_time


def refresh_settings(settings):
    """The scheduling inputs from applied ParishSoft integration settings.

    Each setting is optional in the stored document, so the defaults live
    here, once, for the scheduler and the Admin pages alike: the nightly time
    alone when no list is stored, quarter-hour deltas, and no listed quick
    times. Of ``refresh_rules`` (#632), how the Administrator entered the
    schedule, only its presence is read (``rules``): it decides which limits
    the lists are held to. Equal results here mean an unchanged schedule
    (``data_age.schedule_runs``, and the tick guard's own copy of this
    normalization, ``stewardship_refresh_schedule_v1``, for the
    schedule-change catch-up).
    """
    nightly_time = settings.get("nightly_time", DEFAULT_TIME)
    return {
        "nightly_time": nightly_time,
        "frequency": settings.get("full_refresh", "daily"),
        "full_refresh_times": tuple(
            settings.get("full_refresh_times") or (nightly_time,)
        ),
        "delta_refresh": settings.get("delta_refresh", DEFAULT_DELTA_REFRESH),
        "quick_refresh_times": tuple(settings.get("quick_refresh_times") or ()),
        "rules": "refresh_rules" in settings,
    }


def skips_around_emails(settings):
    """Whether applied settings ask to skip refreshes around Family emails.

    Only a schedule saved with its rules has the setting (#632); every other
    schedule never skips and never records a slot decision.
    """
    rules = settings.get("refresh_rules")
    return type(rules) is dict and rules.get("skip_around_family_emails") is True


def _validate(
    *,
    nightly_time,
    frequency,
    full_refresh_times,
    delta_refresh,
    quick_refresh_times=(),
    rules=False,
):
    """Refuse non-canonical cadence inputs; return the effective times tuple.

    Listed quick times (``delta_refresh`` "times") need a schedule saved
    with its rules (``rules``), daily full times and at least one quick
    time, none of them also a full time; any other quick setting lists
    none. Without rules, at most ``MAX_LEGACY_FULL_TIMES`` full times, as
    before #632.
    """
    if full_refresh_times is None:
        full_refresh_times = (nightly_time,)
    quick = tuple(quick_refresh_times or ())
    listed = delta_refresh == "times"
    if (
        not canonical_time(nightly_time)
        or not canonical_times(nightly_time, full_refresh_times)
        or (not rules and (listed or len(full_refresh_times) > MAX_LEGACY_FULL_TIMES))
        or frequency not in FREQUENCIES
        or delta_refresh not in DELTA_REFRESHES
        or (listed and (frequency != "daily" or not canonical_list(quick)))
        or (quick and not listed)
        or set(quick) & set(full_refresh_times)
    ):
        raise ValueError("Refresh scheduling requires canonical cadence inputs.")
    return tuple(full_refresh_times)


def local_instant(day, value, timezone):
    """The instant the ``HH:MM`` wall time ``value`` falls at on local ``day``.

    The one place every daily time, full or quick, is resolved through the
    shared daylight-saving resolver: a repeated time (clocks go back) at its
    earlier instant, a time in the missing hour (clocks go forward) at the
    first real instant after the gap. The scheduler's slots and the settings
    page's preview (``schedule_preview``) both use it.
    """
    return resolve_local(datetime.combine(day, time.fromisoformat(value)), timezone)


def _occurrences(day, value, timezone):
    """The instants a wall time falls at on ``day`` and the days either side."""
    return [
        local_instant(day + timedelta(days=offset), value, timezone)
        for offset in (-1, 0, 1)
    ]


def precedence(due, value, nightly_time):
    """How the scheduler ranks configured times due at or before now.

    The latest instant wins. Times inside a spring-forward gap all resolve to
    the first real instant, so they tie; the nightly time wins a tie (its
    tick must still exist, and it is the refresh a Family send never holds),
    then the later wall time, so the choice is deterministic. Shared by
    ``_latest`` and the preview, which must name the same winner.
    """
    return (due, value == nightly_time, value)


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


def _latest(now, timezone, times, nightly_time):
    """The latest configured local time due at or before ``now`` and that time.

    Every configured time is resolved for the local day and the day before,
    through the shared DST resolver, and the latest by ``precedence`` among
    those not after ``now`` wins. Listed quick times use the same rule with
    no nightly time.
    """
    day = now.astimezone(ZoneInfo(timezone)).date()
    candidates = [
        precedence(due, value, nightly_time)
        for value in times
        for due in _occurrences(day, value, timezone)[:2]
        if due <= now
    ]
    due, _, value = max(candidates)
    return due, value


def _check_scope(timezone, scope_fingerprint):
    """Refuse a non-string zone or a non-canonical scope fingerprint."""
    if (
        type(timezone) is not str
        or type(scope_fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", scope_fingerprint) is None
    ):
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")


def _slot(cause, due, value, *, timezone, scope_fingerprint):
    """The slot for ``cause`` due at ``due``, with its stable identity.

    The identity's inputs are the source scope, time zone, cause, due
    instant and, for a scheduled full slot, the configured time it fell due
    at (``value``; None otherwise). They have not changed since #465, so a
    quick slot at a listed local quarter hour has the identity the old
    quarter-hour delta slot had at the same instant.
    """
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
    return RefreshSlot(cause, due, key, uuid5(SLOT_NAMESPACE, key), value)


def due_slots(
    *,
    now,
    timezone,
    nightly_time,
    scope_fingerprint,
    frequency="daily",
    full_refresh_times=None,
    delta_refresh=DEFAULT_DELTA_REFRESH,
    quick_refresh_times=(),
    rules=False,
):
    """Select the latest full-refresh and delta slot, never an unbounded backlog.

    ``frequency`` chooses the full refresh's slot: for "daily" the latest of
    the configured local ``full_refresh_times`` (the nightly time alone by
    default), else the latest UTC hour or quarter hour. ``delta_refresh``
    chooses the delta slot: the latest UTC quarter hour or hour, none, or
    ("times", #632) the latest of the listed local ``quick_refresh_times``,
    resolved like the full times. ``rules`` says the schedule was saved with
    its rules (``refresh_settings``), which allows listed quick times and
    more than eight full times.

    Full comes first so a simultaneously due delta can coalesce into it. After
    downtime, one current coherent observation covers stale refresh slots;
    unlike delivery obligations, missed read-only polls need no individual replay.
    Identities ignore unrelated configuration edits but include the actual source
    scope, timezone and cadence. Persist them before emitting broker hints.
    """
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    _check_scope(timezone, scope_fingerprint)
    times = _validate(
        nightly_time=nightly_time,
        frequency=frequency,
        full_refresh_times=full_refresh_times,
        delta_refresh=delta_refresh,
        quick_refresh_times=quick_refresh_times,
        rules=rules,
    )
    now = now.astimezone(UTC)
    if frequency == "daily":
        full, chosen = _latest(now, timezone, times, nightly_time)
    else:
        # A frequent full refresh keeps the nightly time as its recorded
        # time, as it always has; the time plays no part in its slot.
        full = _floor(now, HOUR if frequency == "hourly" else QUARTER_HOUR)
        chosen = nightly_time
    slots = [("nightly", full, chosen)]
    if delta_refresh == "times":
        quick, _ = _latest(now, timezone, tuple(quick_refresh_times), None)
        slots.append(("delta", quick, None))
    elif delta_refresh != "off" and not covered_by_full(frequency, delta_refresh):
        step = HOUR if delta_refresh == "hourly" else QUARTER_HOUR
        slots.append(("delta", _floor(now, step), None))
    return tuple(
        _slot(cause, due, value, timezone=timezone, scope_fingerprint=scope_fingerprint)
        for cause, due, value in slots
    )


def listed_due_slots(
    *,
    now,
    timezone,
    nightly_time,
    scope_fingerprint,
    full_refresh_times,
    quick_refresh_times=(),
):
    """Every listed full and quick slot due today or yesterday, oldest first.

    For a schedule saved with its rules (#632) that skips refreshes around
    Family emails: the scheduler decides each due, undecided slot, not only
    the latest of each kind (see ``production``). Each listed time is
    resolved on the local day and the day before through the shared
    daylight-saving resolver, exactly as ``due_slots`` resolves the latest
    one, so the latest slot of each kind here is the one ``due_slots``
    returns. Slots with the same identity (a repeated wall time) appear once.
    On the spring-forward day two full times in the missing hour resolve to
    the same instant with different identities (each names its own time):
    both appear, ``due_slots`` picks one as the latest (the nightly wins,
    then the later wall time), and the other may be decided on its own, a
    harmless twin.
    """
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    _check_scope(timezone, scope_fingerprint)
    times = _validate(
        nightly_time=nightly_time,
        frequency="daily",
        full_refresh_times=full_refresh_times,
        delta_refresh="times" if quick_refresh_times else "off",
        quick_refresh_times=quick_refresh_times,
        rules=True,
    )
    now = now.astimezone(UTC)
    day = now.astimezone(ZoneInfo(timezone)).date()
    slots = {}
    for cause, values in (("nightly", times), ("delta", tuple(quick_refresh_times))):
        for value in values:
            for due in _occurrences(day, value, timezone)[:2]:
                if due > now:
                    continue
                slot = _slot(
                    cause,
                    due,
                    value if cause == "nightly" else None,
                    timezone=timezone,
                    scope_fingerprint=scope_fingerprint,
                )
                slots[slot.slot_key] = slot
    return tuple(sorted(slots.values(), key=lambda slot: (slot.due_at, slot.cause)))


def catch_up_slot(*, effective_at, timezone, scope_fingerprint):
    """The schedule-change catch-up: one full slot due when a schedule took effect.

    When a full slot of the previous schedule was already overdue as a new
    schedule took effect, the new schedule may not include that time, so its
    slot could never be created; the scheduler requests this one instead
    (#632, the operations spec's "changing the schedule never hides a late
    refresh"). Its due instant is the effective instant truncated to the
    second (ticks store whole seconds), and its identity has no configured
    time. The tick guard admits it only at exactly that instant.
    """
    if (
        type(effective_at) is not datetime
        or effective_at.tzinfo is None
        or effective_at.utcoffset() is None
    ):
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    _check_scope(timezone, scope_fingerprint)
    due = effective_at.astimezone(UTC).replace(microsecond=0)
    return _slot(
        "catch_up", due, None, timezone=timezone, scope_fingerprint=scope_fingerprint
    )


def next_full_at(
    *,
    now,
    timezone,
    nightly_time,
    frequency="daily",
    full_refresh_times=None,
    rules=False,
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
        rules=rules,
    )
    now = now.astimezone(UTC)
    if frequency != "daily":
        step = HOUR if frequency == "hourly" else QUARTER_HOUR
        return _floor(now, step) + step
    day = now.astimezone(ZoneInfo(timezone)).date()
    return min(
        due
        for value in times
        for due in _occurrences(day, value, timezone)[1:]
        if due > now
    )


def _minutes(value):
    """Minutes after midnight of a canonical ``HH:MM`` time."""
    return int(value[:2]) * 60 + int(value[3:])


def longest_gap(
    *, frequency, full_refresh_times, delta_refresh, quick_refresh_times=()
):
    """The longest wait between scheduled refreshes of any kind, as a timedelta.

    With UTC-cadence deltas on, their interval; otherwise the longest interval
    between consecutive listed times, full and quick, around the clock
    (daylight-saving shifts ignored). Plus the lateness margin, it is how
    long without a successful attempt the connection line waits before it
    reads "not checked" (``data_age.connection_threshold``, #510).
    """
    if frequency == "quarter_hour":
        return QUARTER_HOUR
    if delta_refresh == "quarter_hour":
        return QUARTER_HOUR
    if frequency == "hourly" or delta_refresh == "hourly":
        return HOUR
    listed = tuple(full_refresh_times)
    if delta_refresh == "times":
        listed += tuple(quick_refresh_times)
    minutes = sorted(_minutes(value) for value in listed)
    gaps = [b - a for a, b in zip(minutes, minutes[1:], strict=False)]
    gaps.append(24 * 60 - minutes[-1] + minutes[0])
    return timedelta(minutes=max(gaps))
