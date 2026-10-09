"""The refresh schedule's seven-day preview, cost and freshness (#632).

The preview must say exactly what the scheduler does. The parity tests below
run the scheduler's own slot functions (``cadence.due_slots`` and
``cadence.listed_due_slots``) and its decision step (``production._decide``,
with the two record tables replaced by in-memory sets) in a loop at every
instant a slot falls due, on ordinary days and both daylight-saving days,
and compare the slots it creates and skips with the preview's.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from parishkit.stewardship.source import production
from parishkit.stewardship.source.cadence import (
    due_slots,
    listed_due_slots,
    refresh_settings,
)
from parishkit.stewardship.source.refresh_rules import (
    check_schedule,
    converted_rules,
    stored_settings,
)
from parishkit.stewardship.source.schedule_preview import (
    TYPICAL,
    Durations,
    cost,
    durations,
    freshness,
    preview_days,
    summarize,
)
from parishkit.stewardship.source.send_windows import Window

ZONE = "America/New_York"
MARGIN = timedelta(minutes=30)
FINGERPRINT = "a" * 64
SPRING = date(2027, 3, 14)  # 02:00 EST becomes 03:00 EDT
FALL = date(2026, 11, 1)  # 02:00 EDT becomes 01:00 EST
ORDINARY = date(2026, 9, 11)


def rules(*rows, skip=False, skips=()):
    """A ``refresh_rules`` document."""
    return {
        "rules": list(rows),
        "skips": list(skips),
        "skip_around_family_emails": skip,
    }


def every(kind, step, start, end):
    """A range rule row."""
    return {"kind": kind, "every": step, "from": start, "to": end}


def at(kind, value):
    """A single-time rule row."""
    return {"kind": kind, "at": value}


# Busy around both clock changes: full refreshes at 00:00, 02:15, 03:00 and
# every two hours from 08:00; quick updates every 15 minutes from 01:00 to
# 02:45 and hourly from 04:00. On the spring day 02:00–02:59 do not exist,
# so 02:00, 02:15, 02:30, 02:45 and 03:00 all fall due at 03:00 EDT; on the
# fall day 01:00–01:59 happen twice.
BUSY = (
    at("full", "00:00"),
    at("full", "02:15"),
    at("full", "03:00"),
    every("full", 120, "08:00", "20:00"),
    every("quick", 15, "01:00", "02:45"),
    every("quick", 60, "04:00", "23:00"),
)
# The nightly refresh itself in the missing hour, tied with 03:00.
NIGHTLY_IN_GAP = (
    at("full", "02:30"),
    at("full", "03:00"),
    every("quick", 60, "04:00", "23:00"),
)


def utc(day, hour, minute=0):
    """A UTC instant on ``day``."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def one_day(document, day, *, timezone=ZONE, windows=()):
    """The preview of one day."""
    (found,) = preview_days(
        document, timezone=timezone, first=day, days=1, windows=windows
    )
    return found


def by_time(day):
    """``{(kind, time): PreviewTime}`` of one preview day."""
    return {(entry.kind, entry.time): entry for entry in day.times}


# ---------------------------------------------------------------------------
# The scheduler, simulated with its own functions.


class _Rows:
    """An in-memory stand-in for one record table's ``objects`` manager."""

    def __init__(self, rows):
        self.rows = rows

    def filter(self, slot_key__in):
        """Rows with the given slot keys (``_decide``'s only query)."""
        self.keys = set(slot_key__in)
        return self

    def values_list(self, *fields, flat=False):
        """``slot_key`` alone, or ``(slot_key, decision)`` pairs."""
        found = {key: value for key, value in self.rows.items() if key in self.keys}
        if flat:
            return list(found)
        return list(found.items())


class _Table:
    """A model class whose ``objects`` reads ``rows`` (key -> value)."""

    def __init__(self, rows):
        self.objects = _Rows(rows)


def simulate(monkeypatch, settings, first, last, *, timezone=ZONE, windows=()):
    """Run the scheduler's slot step at every due instant from ``first`` to ``last``.

    As ``production.produce_refreshes`` does in each loop, without a hold:
    the latest slots (``due_slots``); for a schedule that skips around
    Family emails, every due, undecided slot decided first
    (``production._decide``, the real function); then a refresh for each
    latest slot that has none and was not skipped. Returns
    ``(ticked, skipped)``, slot key -> slot.
    """
    schedule = refresh_settings(settings)
    skipping = settings["refresh_rules"]["skip_around_family_emails"]
    ticked, decided, skipped = {}, {}, {}
    monkeypatch.setattr(production, "SourceRefreshTick", _Table(ticked))
    monkeypatch.setattr(production, "SourceSlotDecision", _Table(decided))

    def record(slot, decision, cause=None):
        """Record a decision, as the slot decision record does once per slot."""
        decided[slot.slot_key] = decision
        if decision == "skipped":
            skipped[slot.slot_key] = slot
        return True

    # Loop at every instant the scheduler itself says a slot falls due.
    instants = set()
    day = first - timedelta(days=1)
    while day <= last + timedelta(days=1):
        end = datetime.combine(day, datetime.min.time()).replace(tzinfo=UTC)
        for slot in listed_due_slots(
            now=end + timedelta(days=1),
            timezone=timezone,
            nightly_time=schedule["nightly_time"],
            scope_fingerprint=FINGERPRINT,
            full_refresh_times=schedule["full_refresh_times"],
            quick_refresh_times=schedule["quick_refresh_times"],
        ):
            instants.add(slot.due_at)
        day += timedelta(days=1)
    for now in sorted(instants):
        latest = due_slots(
            now=now, timezone=timezone, scope_fingerprint=FINGERPRINT, **schedule
        )
        extra = ()
        if skipping:
            candidates = listed_due_slots(
                now=now,
                timezone=timezone,
                nightly_time=schedule["nightly_time"],
                scope_fingerprint=FINGERPRINT,
                full_refresh_times=schedule["full_refresh_times"],
                quick_refresh_times=schedule["quick_refresh_times"],
            )
            _, extra = production._decide(
                windows=list(windows),
                skip_since=None,
                candidates=candidates,
                latest=latest,
                held=False,
                nightly_time=schedule["nightly_time"],
                record=record,
                skipped=set(),
                waiting=set(),
            )
        for slot in (*latest, *extra):
            if slot.slot_key in ticked or decided.get(slot.slot_key) == "skipped":
                continue
            ticked[slot.slot_key] = slot
    return ticked, skipped


def identities(slots, day):
    """``(cause, due_at, configured time)`` of the slots due during ``day``."""
    return {
        (slot.cause, slot.due_at, slot.nightly_time)
        for slot in slots.values()
        if day.start <= slot.due_at < day.end
    }


def expected_runs(day):
    """The slots the preview says the scheduler creates during ``day``.

    A full time that runs is a full slot with its own time; a quick time
    that runs is a quick slot. A quick time joined to a full one at the same
    instant is still a quick slot, whose request coalesces into the full
    one; a quick time joined to another quick one shares its slot, and a
    full time joined to another full one never runs.
    """
    full = {entry.time for entry in day.times if entry.kind == "full"}
    found = set()
    for entry in day.times:
        if entry.kind == "full" and entry.status == "runs":
            found.add(("nightly", entry.due_at, entry.time))
        elif entry.kind == "quick" and (
            entry.status == "runs" or (entry.status == "joined" and entry.by in full)
        ):
            found.add(("delta", entry.due_at, None))
    return found


def expected_skips(day):
    """The slots the preview says are skipped around a Family email."""
    return {
        ("nightly", entry.due_at, entry.time)
        if entry.kind == "full"
        else ("delta", entry.due_at, None)
        for entry in day.times
        if entry.status == "skipped"
    }


def assert_parity(monkeypatch, document, first, *, days=3, timezone=ZONE, windows=()):
    """The preview of each day equals what the simulated scheduler does."""
    settings = stored_settings(document)
    assert check_schedule(settings) == []
    preview = preview_days(
        document, timezone=timezone, first=first, days=days, windows=windows
    )
    ticked, skipped = simulate(
        monkeypatch,
        settings,
        first,
        first + timedelta(days=days - 1),
        timezone=timezone,
        windows=windows,
    )
    for day in preview:
        assert identities(ticked, day) == expected_runs(day), day.day
        assert identities(skipped, day) == expected_skips(day), day.day
    return preview


# ---------------------------------------------------------------------------
# Parity with the scheduler.


@pytest.mark.parametrize(
    "first",
    [
        ORDINARY - timedelta(days=1),
        SPRING - timedelta(days=1),
        FALL - timedelta(days=1),
    ],
)
@pytest.mark.parametrize("document", [rules(*BUSY), rules(*NIGHTLY_IN_GAP)])
def test_the_preview_creates_the_scheduler_slots_around_clock_changes(
    monkeypatch, first, document
):
    """The day before, the day and the day after: the same slots run."""
    preview = assert_parity(monkeypatch, document, first)
    assert any(day.times for day in preview)


def test_the_preview_skips_the_slots_the_scheduler_skips(monkeypatch):
    """Windows on the spring day, one over the nightly: skipped exactly alike."""
    windows = (
        # A reminder due 10:00 EDT: preparing from 08:00, sending until 11:00.
        Window(utc(SPRING, 12), utc(SPRING, 14), "reminder_preparing"),
        Window(utc(SPRING, 14), utc(SPRING, 15), "reminder_sending"),
        # An invitation sending across midnight and the missing hour.
        Window(utc(SPRING, 4, 30), utc(SPRING, 7, 30), "initial_sending"),
    )
    preview = assert_parity(
        monkeypatch,
        rules(*BUSY, skip=True),
        SPRING - timedelta(days=1),
        windows=windows,
    )
    spring = by_time(preview[1])
    # The nightly refresh is never skipped; it says it runs inside a window.
    assert spring[("full", "00:00")].status == "runs"
    assert spring[("full", "00:00")].window == "initial_sending"
    assert spring[("full", "03:00")].status == "skipped"
    assert spring[("quick", "09:00")].window == "reminder_preparing"
    assert spring[("full", "10:00")].status == "skipped"
    assert spring[("full", "12:00")].status == "runs"


def test_the_preview_follows_a_half_hour_clock_change(monkeypatch):
    """Lord Howe Island moves its clocks by 30 minutes (02:00 becomes 02:30)."""
    document = rules(
        at("full", "00:00"),
        every("quick", 15, "01:30", "03:00"),
        every("full", 240, "04:00", "20:00"),
    )
    preview = assert_parity(
        monkeypatch, document, date(2026, 10, 3), timezone="Australia/Lord_Howe"
    )
    changed = by_time(preview[1])
    assert preview[1].change == "forward"
    assert changed[("quick", "02:00")].shift == "forward"
    assert changed[("quick", "02:15")].status == "joined"


def test_a_converted_hourly_schedule_runs_at_the_same_instants(monkeypatch):
    """In a whole-hour zone a converted hourly refresh keeps its instants.

    Its slot identities change (each full slot records its own time), as the
    review says. The instants do not, except on the day clocks go back: the
    UTC hourly refresh runs in both passes of the repeated hour, and a local
    01:00 runs once, at the first.
    """
    legacy = {"full_refresh": "hourly", "delta_refresh": "off", "nightly_time": "02:00"}
    for first in (SPRING, FALL, ORDINARY):
        day = one_day(converted_rules(legacy), first)
        instants = {entry.due_at for entry in day.runs("full")}
        schedule = refresh_settings(legacy)
        legacy_instants = {
            due_slots(
                now=day.start + timedelta(minutes=minute),
                timezone=ZONE,
                scope_fingerprint=FINGERPRINT,
                **schedule,
            )[0].due_at
            for minute in range(0, int((day.end - day.start).total_seconds() // 60), 5)
        }
        repeated = {utc(FALL, 6)} if first == FALL else set()
        assert instants == legacy_instants - repeated


def _legacy_quick_instants(legacy, day):
    """The instants the old scheduler runs a quick update on one preview day.

    Steps ``due_slots`` through the day every five minutes (every legacy
    boundary is a multiple of five minutes) and keeps the delta slots that
    fall due that day, less those due with a full slot, which join it.
    """
    schedule = refresh_settings(legacy)
    full, quick = set(), set()
    for minute in range(0, int((day.end - day.start).total_seconds() // 60), 5):
        for slot in due_slots(
            now=day.start + timedelta(minutes=minute),
            timezone=ZONE,
            scope_fingerprint=FINGERPRINT,
            **schedule,
        ):
            if day.start <= slot.due_at < day.end:
                (full if slot.cause == "nightly" else quick).add(slot.due_at)
    return quick - full


@pytest.mark.parametrize(
    "legacy",
    [
        {"delta_refresh": "quarter_hour"},
        {"nightly_time": "02:10", "full_refresh_times": ["02:10"]},
        {
            "nightly_time": "08:30",
            "full_refresh_times": ["08:30"],
            "delta_refresh": "hourly",
        },
    ],
    ids=["daily-quarter-hour", "off-quarter-hour-full", "off-hour-full-hourly"],
)
def test_a_converted_schedule_keeps_every_quick_update_that_runs_today(legacy):
    """Converted rules run quick updates at exactly the old scheduler's instants.

    The spec's conversion keeps each quick time coverage would remove as a
    "Quick at" row, so no quick update that runs today disappears. The only
    difference is the day clocks go back: the old UTC boundaries run the
    repeated hour (01:00–01:59 EST, 06:00–06:59 UTC) a second time, and a
    listed local time runs once, at the earlier instant.
    """
    converted = converted_rules(legacy)
    for first in (ORDINARY, SPRING, FALL):
        day = one_day(converted, first)
        instants = {entry.due_at for entry in day.runs("quick")}
        expected = _legacy_quick_instants(legacy, day)
        if first == FALL:
            repeated = {t for t in expected if utc(FALL, 6) <= t < utc(FALL, 7)}
            assert repeated
            expected -= repeated
        assert instants == expected, first


# ---------------------------------------------------------------------------
# What each day says.


def test_an_ordinary_day_lists_each_time_with_its_instant():
    """Full and quick times at their EDT instants; covered times by their full."""
    day = one_day(rules(*BUSY), ORDINARY)
    times = by_time(day)
    assert day.change is None and day.end - day.start == timedelta(hours=24)
    nightly = times[("full", "00:00")]
    assert nightly.nightly and nightly.due_at == utc(ORDINARY, 4)
    assert times[("quick", "01:00")].due_at == utc(ORDINARY, 5)
    # Precedence: the 08:00 quick time is shown covered by the 08:00 full one.
    covered = times[("quick", "08:00")]
    assert (covered.status, covered.by) == ("covered", "08:00")
    assert all(entry.shift is None for entry in day.times)
    # Full first at equal times, wall-time order throughout.
    assert [entry.time for entry in day.times] == sorted(e.time for e in day.times)


def test_coverage_is_listed_with_the_covering_full_time():
    """A rule's quick time a full time covers is listed, not run."""
    day = one_day(
        rules(at("full", "02:00"), every("quick", 60, "00:00", "23:00")), ORDINARY
    )
    entry = by_time(day)[("quick", "02:00")]
    assert (entry.status, entry.by, entry.due_at) == ("covered", "02:00", None)


def test_the_spring_day_runs_the_missing_hour_once_at_three():
    """02:15 joins 03:00 (later wall time); quick times there join the full one."""
    day = one_day(rules(*BUSY), SPRING)
    times = by_time(day)
    assert day.change == "forward" and day.end - day.start == timedelta(hours=23)
    three = utc(SPRING, 7)
    assert times[("full", "03:00")].status == "runs"
    assert times[("full", "02:15")].status == "joined"
    assert times[("full", "02:15")].by == "03:00"
    assert times[("full", "02:15")].shift == "forward"
    for value in ("02:00", "02:30", "02:45"):
        assert times[("quick", value)].status == "joined"
        assert times[("quick", value)].due_at == three
    assert times[("quick", "01:45")].status == "runs"


def test_the_nightly_wins_a_tie_in_the_missing_hour():
    """The nightly 02:30 and 03:00 both fall due at 03:00 EDT; the nightly runs."""
    times = by_time(one_day(rules(*NIGHTLY_IN_GAP), SPRING))
    assert times[("full", "02:30")].status == "runs"
    assert times[("full", "02:30")].nightly
    assert times[("full", "03:00")].status == "joined"
    assert times[("full", "03:00")].by == "02:30"


def test_the_fall_day_runs_the_repeated_hour_once():
    """01:00–01:45 run once each, at their EDT instants, marked as repeated."""
    day = one_day(rules(*BUSY), FALL)
    times = by_time(day)
    assert day.change == "back" and day.end - day.start == timedelta(hours=25)
    entry = times[("quick", "01:30")]
    assert (entry.status, entry.shift, entry.due_at) == (
        "runs",
        "back",
        utc(FALL, 5, 30),
    )
    assert times[("full", "02:15")].shift is None


def test_windows_skip_nothing_unless_the_schedule_skips_around_emails():
    """With the switch off no time is skipped and no window is named."""
    window = Window(utc(ORDINARY, 12), utc(ORDINARY, 16), "reminder_sending")
    off = one_day(rules(*BUSY), ORDINARY, windows=(window,))
    assert all(
        entry.status != "skipped" and entry.window is None for entry in off.times
    )
    on = one_day(rules(*BUSY, skip=True), ORDINARY, windows=(window,))
    skipped = {entry.time for entry in on.times if entry.status == "skipped"}
    assert skipped == {"08:00", "09:00", "10:00", "11:00"}


# ---------------------------------------------------------------------------
# Cost and freshness.


def test_durations_are_typical_until_three_runs_then_the_median():
    """Two runs are not enough; three give their median."""
    minute = timedelta(minutes=1)
    two = durations({"full": [minute, 3 * minute], "quick": []})
    assert two == Durations(TYPICAL["full"], TYPICAL["quick"])
    three = durations({"full": [minute, 9 * minute, 3 * minute], "quick": [minute] * 4})
    assert three == Durations(3 * minute, minute, True, True)


def test_cost_counts_only_the_refreshes_that_run():
    """Covered, joined and skipped times cost nothing; shares use the day's length."""
    minute = timedelta(minutes=1)
    measured = Durations(6 * minute, 2 * minute)
    ordinary = one_day(rules(*BUSY), ORDINARY)
    spring = one_day(rules(*BUSY), SPRING)
    found = cost((ordinary, spring), measured)
    full, quick = len(ordinary.runs("full")), len(ordinary.runs("quick"))
    # 01:00–02:45 every 15 minutes less 02:15 (a full time), and 04:00–23:00
    # hourly less the even hours from 08:00 to 20:00.
    assert (full, quick) == (10, 7 + 13)
    assert found.per_day[0] == (
        full * 6 * minute + quick * 2 * minute,
        (full * 6 + quick * 2) / (24 * 60),
    )
    # Spring: 02:15 joins 03:00, and the 02:00, 02:30 and 02:45 quick
    # updates join it too.
    assert len(spring.runs("full")) == 9 and len(spring.runs("quick")) == 4 + 13
    assert found.per_day[1][1] == (9 * 6 + 17 * 2) / (23 * 60)
    assert not found.warning


def test_cost_warns_above_a_quarter_of_the_day():
    """Every quarter hour a full refresh of 7.3 minutes is about half the day."""
    day = one_day(rules(every("full", 15, "00:00", "23:45")), ORDINARY)
    found = cost((day,), Durations(TYPICAL["full"], TYPICAL["quick"]))
    assert found.warning and found.share == pytest.approx(96 * 7.3 / 1440)


def test_freshness_is_the_longest_wait_between_full_refreshes():
    """00:00 then 08:00: eight hours, measured into the preview's first morning."""
    document = rules(at("full", "00:00"), every("full", 120, "08:00", "20:00"))
    found = summarize(
        document,
        timezone=ZONE,
        today=ORDINARY,
        windows=(),
        measured=durations({}),
        margin=MARGIN,
    )
    assert len(found.days) == 7 and found.days[0].day == ORDINARY
    assert found.freshness.longest == timedelta(hours=8)
    assert found.freshness.start == utc(ORDINARY, 4)
    assert found.freshness.margin == MARGIN


def test_freshness_counts_a_skipped_full_refresh_as_no_data():
    """A window over 08:00 and 10:00 makes the wait 00:00 to 12:00."""
    document = rules(
        at("full", "00:00"), every("full", 120, "08:00", "20:00"), skip=True
    )
    window = Window(utc(ORDINARY, 12), utc(ORDINARY, 15), "reminder_sending")
    days = preview_days(
        document, timezone=ZONE, first=ORDINARY, days=1, windows=(window,)
    )
    found = freshness(days, MARGIN)
    assert (found.longest, found.end) == (timedelta(hours=12), utc(ORDINARY, 16))


def test_quick_updates_do_not_count_toward_freshness():
    """Hourly quick updates between full refreshes leave the wait unchanged."""
    document = rules(
        at("full", "00:00"),
        at("full", "12:00"),
        every("quick", 60, "01:00", "23:00"),
    )
    days = preview_days(document, timezone=ZONE, first=ORDINARY, days=2)
    assert freshness(days, MARGIN).longest == timedelta(hours=12)


def test_the_summary_keeps_only_windows_it_applies_inside_the_preview():
    """Windows outside the seven days, or with the switch off, are not shown."""
    inside = Window(utc(ORDINARY, 12), utc(ORDINARY, 14), "initial_sending")
    outside = Window(
        utc(ORDINARY, 12) + timedelta(days=9),
        utc(ORDINARY, 14) + timedelta(days=9),
        "initial_sending",
    )
    common = {
        "timezone": ZONE,
        "today": ORDINARY,
        "windows": (inside, outside),
        "measured": durations({}),
        "margin": MARGIN,
    }
    assert summarize(rules(*BUSY, skip=True), **common).windows == (inside,)
    assert summarize(rules(*BUSY), **common).windows == ()
