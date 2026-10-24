"""Bounded source-refresh slots with stable identities and canonical DST rules."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.wall_clock import is_hhmm

from .canonical import canonical_payload

SLOT_NAMESPACE = UUID("ec9c1e19-b158-454d-96fd-1cfe6e721d11")
# How often a scheduled full refresh runs. "daily" uses the configured local
# times; the others use UTC hour or quarter-hour boundaries. The scheduled full
# cause stays "nightly" in stored commands and slot identities.
FREQUENCIES = ("daily", "hourly", "quarter_hour")
# The values a stored ``delta_refresh`` may hold. Quick updates (delta
# refreshes) ran on UTC quarter hours or hours, not at all (#465), or at the
# listed local ``quick_refresh_times`` (``times``, #632). They were removed
# (#1147): stored documents keep these settings and stay valid, but nothing
# schedules a quick update. Only ``schedule_identity`` still reads them.
DELTA_REFRESHES = ("quarter_hour", "hourly", "off", "times")
# At most this many daily times in a stored schedule (full and, in an older
# document, quick together): one every quarter hour. The 15-minute spacing
# rule already implies it; there is no other cap (#632), the settings page
# shows the cost instead.
MAX_DAILY_TIMES = 96
# A schedule saved without its rules (every schedule before #632) keeps the
# old limit of eight full times: it behaves as it always has.
MAX_LEGACY_FULL_TIMES = 8
DEFAULT_TIME = "02:00"
QUARTER_HOUR = timedelta(minutes=15)
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class RefreshSlot:
    """An exact due slot; its request may coalesce but its provenance does not.

    ``nightly_time`` is the configured local time a full slot fell due at (the
    tick stores it, and the SQL guard checks it is a configured time); a
    schedule-change catch-up has none.
    """

    cause: str
    due_at: datetime
    slot_key: str
    command_id: UUID
    nightly_time: str | None = None


def canonical_list(values):
    """Whether ``values`` is a non-empty sorted list of unique ``HH:MM`` times.

    At most ``MAX_DAILY_TIMES`` of them, the most a day can hold 15 minutes
    apart.
    """
    return (
        type(values) in (list, tuple)
        and 1 <= len(values) <= MAX_DAILY_TIMES
        and all(is_hhmm(value) for value in values)
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
    alone when no list is stored. Of ``refresh_rules`` (#632), how the
    Administrator entered the schedule, only its presence is read
    (``rules``): it decides which limits the lists are held to. A stored
    ``delta_refresh`` or ``quick_refresh_times`` is ignored: quick updates
    were removed (#1147), so a schedule runs only full refreshes.
    """
    nightly_time = settings.get("nightly_time", DEFAULT_TIME)
    return {
        "nightly_time": nightly_time,
        "frequency": settings.get("full_refresh", "daily"),
        "full_refresh_times": tuple(
            settings.get("full_refresh_times") or (nightly_time,)
        ),
        "rules": "refresh_rules" in settings,
    }


def schedule_identity(settings):
    """What makes two stored schedules the same, as the tick guard decides it.

    Equal results mean an unchanged schedule (``data_age.schedule_runs``)
    for the schedule-change catch-up, whose due instant the tick guard
    checks with its own copy of this normalization in SQL
    (``stewardship_refresh_schedule_v1``). That copy still compares the
    removed quick settings, with their old quarter-hour default, so this one
    does too: a change of only those settings starts a new schedule there,
    and must here, or the guard would refuse the catch-up's due instant.
    """
    return refresh_settings(settings) | {
        "delta_refresh": settings.get("delta_refresh", "quarter_hour"),
        "quick_refresh_times": tuple(settings.get("quick_refresh_times") or ()),
    }


def skips_around_emails(settings):
    """Whether applied settings ask to skip refreshes around Family emails.

    Only a schedule saved with its rules has the setting (#632); every other
    schedule never skips and never records a slot decision.
    """
    rules = settings.get("refresh_rules")
    return type(rules) is dict and rules.get("skip_around_family_emails") is True


def _validate(*, nightly_time, frequency, full_refresh_times, rules=False):
    """Refuse non-canonical cadence inputs; return the effective times tuple.

    Without rules, at most ``MAX_LEGACY_FULL_TIMES`` full times, as before
    #632.
    """
    if full_refresh_times is None:
        full_refresh_times = (nightly_time,)
    if (
        not is_hhmm(nightly_time)
        or not canonical_times(nightly_time, full_refresh_times)
        or (not rules and len(full_refresh_times) > MAX_LEGACY_FULL_TIMES)
        or frequency not in FREQUENCIES
    ):
        raise ValueError("Refresh scheduling requires canonical cadence inputs.")
    return tuple(full_refresh_times)


def local_instant(day, value, timezone):
    """The instant the ``HH:MM`` wall time ``value`` falls at on local ``day``.

    The one place every daily time is resolved through the
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


def _latest(now, timezone, times, nightly_time):
    """The latest configured local time due at or before ``now`` and that time.

    Every configured time is resolved for the local day and the day before,
    through the shared DST resolver, and the latest by ``precedence`` among
    those not after ``now`` wins.
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
    at (``value``; None otherwise). They have not changed since #465.
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
    rules=False,
):
    """Select the latest full-refresh slot, never an unbounded backlog.

    ``frequency`` chooses the slot: for "daily" the latest of the configured
    local ``full_refresh_times`` (the nightly time alone by default), else
    the latest UTC hour or quarter hour. ``rules`` says the schedule was
    saved with its rules (``refresh_settings``), which allows more than
    eight full times. A tuple, as it was when a quick-update slot could
    join it (removed by #1147).

    After downtime, one current coherent observation covers stale refresh
    slots; unlike delivery obligations, missed read-only polls need no
    individual replay. Identities ignore unrelated configuration edits but
    include the actual source scope, timezone and cadence. Persist them
    before emitting broker hints.
    """
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    _check_scope(timezone, scope_fingerprint)
    times = _validate(
        nightly_time=nightly_time,
        frequency=frequency,
        full_refresh_times=full_refresh_times,
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
    return (
        _slot(
            "nightly",
            full,
            chosen,
            timezone=timezone,
            scope_fingerprint=scope_fingerprint,
        ),
    )


def listed_due_slots(
    *, now, timezone, nightly_time, scope_fingerprint, full_refresh_times
):
    """Every listed full slot due today or yesterday, oldest first.

    For a schedule saved with its rules (#632) that skips refreshes around
    Family emails: the scheduler decides each due, undecided slot, not only
    the latest (see ``production``). Each listed time is resolved on the
    local day and the day before through the shared daylight-saving
    resolver, exactly as ``due_slots`` resolves the latest one, so the
    latest slot here is the one ``due_slots`` returns. Slots with the same
    identity (a repeated wall time) appear once. On the spring-forward day
    two full times in the missing hour resolve to the same instant with
    different identities (each names its own time): both appear,
    ``due_slots`` picks one as the latest (the nightly wins, then the later
    wall time), and the other may be decided on its own, a harmless twin.
    """
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    _check_scope(timezone, scope_fingerprint)
    times = _validate(
        nightly_time=nightly_time,
        frequency="daily",
        full_refresh_times=full_refresh_times,
        rules=True,
    )
    now = now.astimezone(UTC)
    day = now.astimezone(ZoneInfo(timezone)).date()
    slots = {}
    for value in times:
        for due in _occurrences(day, value, timezone)[:2]:
            if due > now:
                continue
            slot = _slot(
                "nightly",
                due,
                value,
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


def longest_gap(*, frequency, full_refresh_times):
    """The longest wait between scheduled refreshes, as a timedelta.

    With a frequent full refresh, its interval; otherwise the longest
    interval between consecutive listed full times, around the clock
    (daylight-saving shifts ignored). Plus the lateness margin, it is how
    long without a successful attempt the connection line waits before it
    reads "not checked" (``data_age.connection_threshold``, #510).
    """
    if frequency == "quarter_hour":
        return QUARTER_HOUR
    if frequency == "hourly":
        return HOUR
    minutes = sorted(_minutes(value) for value in full_refresh_times)
    gaps = [b - a for a, b in zip(minutes, minutes[1:], strict=False)]
    gaps.append(24 * 60 - minutes[-1] + minutes[0])
    return timedelta(minutes=max(gaps))
