"""Bounded civil slot evaluation without a database, broker or wall-clock sleeps."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.schedule_evaluation import (
    SchedulePlan,
    preview_slots,
)

from .campaign_factory import campaign, schedule

FUTURE = datetime(2030, 1, 1, tzinfo=UTC)


def plan(kind="daily_digest", *, campaign_options=None, **options):
    """Use real configuration validation with fresh, non-secret version inputs."""
    owner = campaign(**(campaign_options or {}))
    values = schedule(
        owner["id"],
        kind=kind,
        date="2026-10-01" if kind in {"initial", "reminder"} else None,
        **({"weekday": 0} if kind == "weekly_digest" else {}),
    )["values"]
    values.update(options)
    return SchedulePlan.from_values(values, owner["values"])


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_one_time_slot_has_exact_due_boundary_and_revision_independent_key(kind):
    """No early execution, and a changed civil time retains semantic identity."""
    rule = plan(kind)
    due = datetime(2026, 10, 1, 13, tzinfo=UTC)
    before = rule.page(through=due - timedelta(microseconds=1))
    assert before.slots == () and before.cursor is None and before.exhausted
    page = rule.page(through=due)
    assert len(page.slots) == 1
    assert (page.slots[0].key, page.slots[0].due_at) == ("once", due)
    assert page.cursor == date(2026, 10, 1) and page.exhausted
    assert rule.page(through=FUTURE, after=page.cursor).slots == ()
    assert plan(kind, time="10:00:00").page(through=FUTURE).slots[0].key == "once"


def test_daily_slots_name_the_reported_day_and_include_the_final_day_after_close():
    """The midnight digest reports yesterday; close does not omit its final day."""
    rule = plan(time="00:15:00")
    page = rule.page(through=FUTURE)
    assert len(page.slots) == 31 and page.exhausted
    first, last = page.slots[0], page.slots[-1]
    assert (first.key, first.due_at) == (
        "2026-10-01",
        datetime(2026, 10, 2, 4, 15, tzinfo=UTC),
    )
    assert (last.key, last.due_at) == (
        "2026-10-31",
        datetime(2026, 11, 1, 4, 15, tzinfo=UTC),
    )
    assert not any(slot.final_weekly for slot in page.slots)


@pytest.mark.parametrize("limit", [1, 3, 10, 100])
def test_stable_cursor_partitions_equal_whole_result_and_replay_is_pure(limit):
    """Changing batch sizes does not duplicate or lose any covered local date."""
    rule = plan()
    expected = rule.page(through=FUTURE).slots
    cursor, collected = None, []
    for _ in range(33):
        page = rule.page(through=FUTURE, after=cursor, limit=limit)
        assert page == rule.page(through=FUTURE, after=cursor, limit=limit)
        assert len(page.slots) <= limit
        collected.extend(page.slots)
        cursor = page.cursor
        if page.exhausted:
            break
    else:
        pytest.fail("The bounded schedule did not terminate.")
    assert tuple(collected) == expected


def test_later_cutoff_resumes_after_only_previously_due_slots():
    """A no-more-due result must not advance the cursor across a future slot."""
    rule = plan()
    first_due = datetime(2026, 10, 2, 13, tzinfo=UTC)
    early = rule.page(through=first_due - timedelta(seconds=1))
    assert early.slots == () and early.cursor is None
    first = rule.page(through=first_due)
    assert first.cursor == date(2026, 10, 1) and len(first.slots) == 1
    second = rule.page(through=first_due + timedelta(days=1), after=first.cursor)
    assert [slot.key for slot in second.slots] == ["2026-10-02"]


@pytest.mark.parametrize(
    "campaign_options,clock,day,due",
    [
        (
            {"start_date": "2026-03-06", "end_date": "2026-03-10"},
            "02:30:00",
            "2026-03-07",
            datetime(2026, 3, 8, 7, tzinfo=UTC),
        ),
        (
            {"start_date": "2026-10-30", "end_date": "2026-11-03"},
            "01:30:00",
            "2026-10-31",
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        ),
        (
            {
                "start_date": "2026-10-02",
                "end_date": "2026-10-06",
                "timezone": "Australia/Lord_Howe",
            },
            "02:15:00",
            "2026-10-03",
            datetime(2026, 10, 3, 15, 30, tzinfo=UTC),
        ),
    ],
)
def test_recurring_due_uses_first_valid_gap_instant_and_earlier_fold(
    campaign_options, clock, day, due
):
    """The same resolver covers one-hour and half-hour transitions exactly once."""
    page = plan(campaign_options=campaign_options, time=clock).page(through=FUTURE)
    matches = [slot for slot in page.slots if slot.key == day]
    assert len(matches) == 1 and matches[0].due_at == due


def test_skipped_civil_day_advances_bounded_cursor_without_a_daily_obligation():
    """A one-row batch cannot get stuck on Samoa's nonexistent campaign date."""
    rule = plan(
        campaign_options={
            "timezone": "Pacific/Apia",
            "start_date": "2011-12-29",
            "end_date": "2011-12-31",
        },
        time="00:15:00",
    )
    first = rule.page(through=FUTURE, limit=1)
    assert first.cursor == date(2011, 12, 29)
    skipped = rule.page(through=FUTURE, after=first.cursor, limit=1)
    assert skipped.slots == () and skipped.cursor == date(2011, 12, 30)
    assert not skipped.exhausted
    last = rule.page(through=FUTURE, after=skipped.cursor, limit=1)
    assert [slot.key for slot in last.slots] == ["2011-12-31"]


def test_weekly_dates_and_optional_final_candidate_are_finite():
    """Post-close input coverage may need one more slot, never endless empty mail."""
    rule = plan("weekly_digest", time="09:00:00")
    ordinary = rule.page(through=FUTURE)
    assert [slot.key for slot in ordinary.slots] == [
        "2026-10-05",
        "2026-10-12",
        "2026-10-19",
        "2026-10-26",
    ]
    final = rule.page(through=FUTURE, include_final_weekly=True)
    assert final.slots[:-1] == ordinary.slots
    assert final.slots[-1].key == "2026-11-02" and final.slots[-1].final_weekly
    assert final.exhausted
    assert (
        rule.page(through=FUTURE, after=final.cursor, include_final_weekly=True).slots
        == ()
    )


def test_weekly_ending_on_scheduled_day_can_inspect_only_next_weeks_final_slot():
    """The optional candidate follows close rather than duplicating its last date."""
    rule = plan("weekly_digest", campaign_options={"end_date": "2026-10-26"})
    page = rule.page(
        through=FUTURE, after=date(2026, 10, 26), include_final_weekly=True
    )
    assert [slot.key for slot in page.slots] == ["2026-11-02"]


@pytest.mark.parametrize("kind", ["daily_digest", "weekly_digest"])
def test_cursor_before_start_and_after_all_history_does_not_replay(kind):
    """Keyset evaluation jumps forward and accepts terminal date-range cursors."""
    rule = plan(kind)
    assert rule.page(through=FUTURE, after=date(1900, 1, 1)) == rule.page(
        through=FUTURE
    )
    page = rule.page(through=FUTURE, after=date.max)
    assert page.slots == () and page.cursor == date.max and page.exhausted


@pytest.mark.parametrize(
    "arguments",
    [
        {"through": datetime(2026, 1, 1)},
        {"through": date(2026, 1, 1)},
        {"through": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))},
        {"after": "2026-10-01"},
        {"after": datetime(2026, 10, 1)},
        {"limit": True},
        {"limit": 0},
        {"limit": 1001},
        {"limit": 1.5},
        {"include_final_weekly": 1},
    ],
)
def test_invalid_evaluation_arguments_are_rejected(arguments):
    """Do not accept naive cutoffs, coerced dates or unbounded batch sizes."""
    with pytest.raises(ValueError):
        plan().page(**({"through": FUTURE} | arguments))


def test_preview_is_bounded_and_recomputed_from_the_proposed_campaign_zone():
    """A draft timezone preview cannot accidentally use the parish's old zone."""
    owner = campaign()
    values = schedule(owner["id"], kind="daily_digest", date=None)["values"]
    before = preview_slots(values, owner["values"], limit=2)
    after = preview_slots(
        values, owner["values"] | {"timezone": "America/Los_Angeles"}, limit=2
    )
    assert len(before.slots) == len(after.slots) == 2
    assert [slot.key for slot in before.slots] == [slot.key for slot in after.slots]
    assert after.slots[0].due_at - before.slots[0].due_at == timedelta(hours=3)
    assert not before.exhausted


def test_invalid_schedule_uses_the_existing_configuration_contract():
    """The planner cannot silently repair an invitation outside campaign dates."""
    with pytest.raises(ConfigError):
        plan("initial", date="2026-09-30")
