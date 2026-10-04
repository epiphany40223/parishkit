"""Timeline tests for the local campaign seeder (#476, OPS-10.07).

No Docker and no database: the timeline is pure. Pinned at 100 and 1,100
Families, with ``E`` (the Portal-eligible count) at the specification's
figures, these assert the calendar rules, that nothing is later than now,
today's floor, the response shape, the Reminder floors, the cumulative-share
table with floors applied inside the allocation, the ordered funnel,
determinism, and that phase 1's Friday 09:00 is always a forward jump.
"""

import math
import statistics
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.local import seed_timeline as timeline
from parishkit.stewardship.local.synthetic_parish import generate

ZONE = "America/New_York"
TZ = ZoneInfo(ZONE)
# Portal-eligible counts at the pinned sizes (about 92% of Families); 20 is
# the documented small VM run.
SIZES = {20: 18, 100: 92, 1100: 1012}
# A Saturday, so start dates and elapsed days can be constructed exactly.
SATURDAY = datetime(2026, 9, 26, tzinfo=TZ)
NOW = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
# "Total by now" from the specification, as a percentage of E by days elapsed.
TABLE = {8: 12.4, 9: 13.1, 10: 13.7, 11: 14.6, 12: 15.3, 13: 16.0, 14: 16.5}


def build(size, now=NOW, **options):
    """A timeline at a pinned size with stable Family keys."""
    options.setdefault("ministries", range(5))
    return timeline.build(7, size, now, range(SIZES[size]), timezone=ZONE, **options)


def now_at(elapsed, wall=time(12, 0)):
    """An instant whose campaign start is SATURDAY and elapsed days ``elapsed``."""
    return (SATURDAY + timedelta(days=elapsed)).replace(
        hour=wall.hour, minute=wall.minute
    )


def local_date(instant):
    return instant.astimezone(TZ).date()


def families_by_kind(result, kind):
    return {event.family for event in result.events if event.kind == kind}


# Calendar.
@pytest.mark.parametrize("weekday", range(7))
@pytest.mark.parametrize("wall", [time(0, 1), time(8, 59), time(12, 0), time(23, 58)])
def test_calendar_rules_hold_for_every_weekday_and_time_of_day(weekday, wall):
    """Saturday start 8 to 14 days back, Monday end, Tuesday/Thursday Reminders."""
    now = (SATURDAY + timedelta(days=9 + weekday)).replace(
        hour=wall.hour, minute=wall.minute
    )
    cal = timeline.calendar(now, ZONE)
    assert cal.start.weekday() == 5 and cal.end.weekday() == 0
    assert (cal.end - cal.start).days == 30
    assert 8 <= cal.elapsed_days <= 14
    assert cal.today == now.date()
    assert cal.initial_at == datetime.combine(cal.start, time(10, 0), tzinfo=TZ)
    assert len(cal.reminders) == 8
    for instant in cal.reminders:
        assert instant.weekday() in {1, 3}
        assert instant.timetz().replace(tzinfo=None) == time(9, 0)
        assert cal.start < instant.date() < cal.end
    assert cal.reminders[0].date() == cal.start + timedelta(days=3)
    assert cal.reminders[-1].date() == cal.end - timedelta(days=4)
    # About a third elapsed: 8 to 14 of 31 local days.
    assert 0.25 <= cal.elapsed_days / 31 <= 0.46
    # Phase 1's Friday 09:00 is always later than now minus 17 days.
    assert cal.prepare_at.weekday() == 4
    assert cal.prepare_at == datetime.combine(
        cal.start - timedelta(days=1), time(9, 0), tzinfo=TZ
    )
    assert cal.prepare_at > now - timedelta(days=17)
    assert cal.prepare_at < cal.initial_at


def test_calendar_requires_an_aware_now():
    with pytest.raises(ConfigError, match="aware instant"):
        timeline.calendar(datetime(2026, 10, 7, 12), ZONE)


# Rates and the cumulative table.
@pytest.mark.parametrize("elapsed", sorted(TABLE))
def test_cumulative_share_reproduces_the_specification_table(elapsed):
    """The stated rates and Reminder factors give the table to one decimal."""
    cal = timeline.calendar(now_at(elapsed), ZONE)
    assert cal.elapsed_days == elapsed
    share = 100 * timeline.cumulative_share(cal, elapsed)
    # Within the table's one-decimal rounding (14.65 sits exactly on a half).
    assert abs(share - TABLE[elapsed]) <= 0.051


def test_daily_rates_have_the_specified_shape():
    cal = timeline.calendar(now_at(14), ZONE)
    rates = [timeline.baseline_rate(day) for day in range(15)]
    assert rates[:3] == [2.0, 3.0, 1.5]
    assert rates[3] == 1.0 and rates[13] == rates[14] == 0.4
    assert all(a > b for a, b in zip(rates[3:13], rates[4:14], strict=True))
    factors = [timeline.reminder_factor(cal, day) for day in range(14)]
    # Tuesday day 3 and Thursday day 5 (then 10 and 12): 1.6 on the day, 1.2 after.
    assert factors[3] == factors[5] == factors[10] == factors[12] == 1.6
    assert factors[4] == factors[6] == factors[11] == factors[13] == 1.2
    assert factors[7] == factors[8] == factors[9] == 1.0


@pytest.mark.parametrize("size", sorted(SIZES))
@pytest.mark.parametrize("elapsed", sorted(TABLE))
def test_submissions_before_today_equal_the_table_share_times_e(size, elapsed):
    """Rounded nearest; floors move submissions between days, not the total."""
    result = build(size, now_at(elapsed))
    eligible = SIZES[size]
    cal = result.calendar
    expected = math.floor(timeline.cumulative_share(cal, elapsed) * eligible + 0.5)
    assert sum(result.daily_submissions) == expected
    assert abs(expected - TABLE[elapsed] / 100 * eligible) < 1
    before_today = {
        event.family
        for event in result.events
        if event.kind == "submission"
        and event.data["version"] == 1
        and local_date(event.at) < cal.today
    }
    assert len(before_today) == expected


# Nothing after now; today's floor.
@pytest.mark.parametrize("size", sorted(SIZES))
@pytest.mark.parametrize(
    "now",
    [NOW, now_at(8, time(7, 5)), now_at(14, time(23, 50)), now_at(11, time(0, 10))],
)
def test_no_event_is_later_than_now_and_today_has_activity(size, now):
    result = build(size, now)
    assert all(event.at <= now for event in result.events)
    today = result.calendar.today
    assert any(
        event.kind in {"session", "submission"} and local_date(event.at) == today
        for event in result.events
    )
    assert result.events == tuple(
        sorted(result.events, key=lambda e: (e.at, timeline._KIND_ORDER[e.kind]))
    )


# Shape.
def test_small_parish_still_has_an_early_responder_or_none():
    """At 20 Families day 0 keeps a submission (the early responder) when any exist."""
    result = build(20, now_at(8))
    assert sum(result.daily_submissions) >= 1
    assert result.daily_submissions[0] >= 1
    first = min(
        (e for e in result.events if e.kind == "submission"), key=lambda e: e.at
    )
    assert first.family == result.early_responder
    assert first.at < result.calendar.initial_at
    assert all(event.at <= result.calendar.now for event in result.events)
    # A parish with no submission before today has no early responder.
    tiny = timeline.build(7, 5, now_at(8), range(2), timezone=ZONE)
    assert tiny.early_responder is None or tiny.daily_submissions[0] >= 1


@pytest.mark.parametrize(("size", "scale"), [(100, 4), (1100, 1)])
def test_weekend_peak_lower_monday_and_taper(size, scale):
    """Sunday > Monday, Saturday >= Monday, Monday >= median later plain day."""
    result = build(size, now_at(14), response_scale=scale)
    daily = result.daily_submissions
    saturday, sunday, monday = daily[0], daily[1], daily[2]
    assert sunday > monday
    assert saturday >= monday
    reminder_days = {r.date() for r in result.calendar.reminders}
    later = [
        daily[day]
        for day in range(3, len(daily))
        if result.calendar.start + timedelta(days=day) not in reminder_days
    ]
    assert monday >= statistics.median(later)
    assert all(count > 1 for count in daily[:3])


# The floors cannot all hold at 20 Families (five floors, three submissions);
# the specification pins them at 100 and 1,100.
@pytest.mark.parametrize("size", [size for size in sorted(SIZES) if size >= 100])
def test_each_complete_reminder_window_has_a_visible_uptick(size):
    """At least one submission and at least the baseline rounded up (1.25x at 1,100)."""
    result = build(size, now_at(14, time(12, 0)))
    cal = result.calendar
    eligible = SIZES[size]
    for instant in cal.past_reminders():
        window_end = instant + timedelta(hours=24)
        if window_end > cal.now:
            continue
        inside = [
            event
            for event in result.events
            if event.kind == "submission" and instant <= event.at < window_end
        ]
        day = (instant.date() - cal.start).days
        baseline = timeline.baseline_rate(day) * eligible / 100
        assert len(inside) >= 1
        assert len(inside) >= math.ceil(baseline)
        if size == 1100:
            assert len(inside) >= 1.25 * baseline


# Funnel and edge cases.
@pytest.mark.parametrize("size", sorted(SIZES))
def test_funnel_is_ordered_and_stages_are_disjoint(size):
    result = build(size)
    linked = families_by_kind(result, "session")
    opened = families_by_kind(result, "baseline")
    progressed = {
        event.family
        for event in result.events
        if event.kind == "presence" and event.data["section"] != "welcome"
    }
    submitted = families_by_kind(result, "submission")
    assert len(linked) >= len(opened) >= len(progressed) >= len(submitted) > 0
    assert submitted <= progressed <= opened <= linked
    assert len(linked) <= SIZES[size]
    stages = result.stages
    all_keys = [key for keys in stages.values() for key in keys]
    assert len(all_keys) == len(set(all_keys))
    assert set(stages["submitted"]) == submitted
    # Default ratios, each capped at E.
    total = len(submitted)
    assert len(linked) == min(SIZES[size], round(1.7 * total))
    assert len(opened) == min(SIZES[size], round(1.4 * total))
    assert len(progressed) == min(SIZES[size], round(1.2 * total))
    # Link-only sessions exist and so do partway stops (at the pinned sizes;
    # at 20 Families the rounded ratios can coincide).
    if size >= 100:
        assert linked - opened and opened - progressed and progressed - submitted


@pytest.mark.parametrize("size", sorted(SIZES))
def test_edge_cases_early_responder_resubmissions_and_no_email_family(size):
    result = build(size, no_email_family=0)
    cal = result.calendar
    assert 0 not in result.eligible
    assert not any(event.family == 0 for event in result.events)
    first = min(
        (e for e in result.events if e.kind == "submission"), key=lambda e: e.at
    )
    assert first.family == result.early_responder
    assert local_date(first.at) == cal.start and first.at < cal.initial_at
    versions = {}
    for event in result.events:
        if event.kind == "submission":
            versions.setdefault(event.family, []).append(event.data["version"])
    resubmitted = [family for family, found in versions.items() if found == [1, 2]]
    assert len(resubmitted) >= max(1, round(0.03 * len(versions)) - 1)
    assert all(sorted(found) in ([1], [1, 2]) for found in versions.values())
    # Every submission has a session, a baseline and presence through review first.
    by_family = {}
    for event in result.events:
        if not event.is_occurrence:
            by_family.setdefault(event.family, []).append(event)
    for family in result.submitted:
        kinds = [event.kind for event in by_family[family]]
        assert kinds[:2] == ["session", "baseline"]
        assert "submission" in kinds
        sections = [
            e.data["section"] for e in by_family[family] if e.kind == "presence"
        ]
        assert sections[: len(timeline.SECTIONS)] == list(timeline.SECTIONS)
    answers = [e.data["answers"] for e in result.events if e.kind == "submission"]
    assert {a["pledge"] for a in answers} - {None}
    assert any(a["ministry_interest"] for a in answers)
    assert any(a["census_edit"] for a in answers)
    assert any(a["information"] for a in answers)
    if size == 1100:
        assert any(a["email_opt_out"] for a in answers)


def test_occurrence_instants_are_the_boundary_initial_reminders_and_midnights():
    result = build(100, now_at(10, time(12, 0)))
    cal = result.calendar
    kinds = [event.kind for event in result.occurrences()]
    assert kinds[0] == "boundary_start"
    assert kinds.count("initial") == 1
    # Elapsed day 10 is a Tuesday: its 09:00 Reminder has passed by noon, so
    # the Tuesday and Thursday of week one and this Tuesday are past.
    assert kinds.count("reminder") == len(cal.past_reminders()) == 3
    assert kinds.count("midnight") == 10
    initial = next(e for e in result.events if e.kind == "initial")
    assert initial.at == cal.initial_at
    assert all(e.at <= cal.now for e in result.occurrences())


# Scale and determinism.
def test_response_scale_multiplies_and_is_capped_at_ninety_percent():
    base = build(100, now_at(14))
    denser = build(100, now_at(14), response_scale=3)
    assert sum(denser.daily_submissions) == pytest.approx(
        3 * sum(base.daily_submissions), abs=2
    )
    capped = build(100, now_at(14), response_scale=100)
    responded = sum(capped.daily_submissions) + capped.today_submissions
    assert responded <= 0.9 * SIZES[100] + 1
    assert capped.response_scale < 100
    with pytest.raises(ConfigError, match="positive"):
        build(100, response_scale=0)


@pytest.mark.parametrize("size", sorted(SIZES))
def test_same_inputs_give_the_same_timeline(size):
    assert build(size) == build(size)
    assert build(size) != timeline.build(
        8, size, NOW, range(SIZES[size]), timezone=ZONE, ministries=range(5)
    )
    assert build(size) != build(size, NOW + timedelta(minutes=1))


def test_synthetic_parish_eligible_count_matches_the_pinned_e():
    """The generator's active Families with a head email are about 92 of 100."""
    parish = generate(7, 100, datetime(2026, 9, 24).date())
    with_email = {
        row["familyDUID"]
        for row in parish.members
        if row["memberType"] in {"Head", "Husband", "Wife"}
        and (row.get("emailAddress") or "").strip()
    }
    eligible = [
        row["familyDUID"]
        for row in parish.families
        if row.get("familyActive", True) and row["familyDUID"] in with_email
    ]
    assert 85 <= len(eligible) <= 95
    result = timeline.build(7, 100, NOW, eligible, timezone=ZONE)
    assert set(result.submitted) <= set(eligible)


def test_presence_sections_are_the_real_ones():
    """The seeder reports presence with the names the heartbeat view admits."""
    from parishkit.stewardship.campaigns.credential_models import PRESENCE_SECTIONS

    assert list(timeline.SECTIONS) == list(PRESENCE_SECTIONS)
