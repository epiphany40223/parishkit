"""Bounded civil slot evaluation without a database, broker or wall-clock sleeps.

Family mail sends once; a report email's repeating rule is ``ReportPlan``'s
(``test_report_schedule.py``). The Daily and Weekly digests' rules retired
with the digests (#1834, #2107).
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.schedule_evaluation import (
    SchedulePlan,
    upcoming_slots,
)

from .campaign_factory import campaign, schedule
from .test_report_schedule import report

FUTURE = datetime(2058, 1, 1, tzinfo=UTC)
# Before the factory campaign starts.
BEFORE = datetime(2054, 9, 1, tzinfo=UTC)


def plan(kind="initial", *, campaign_options=None, **options):
    """Use real configuration validation with fresh, non-secret version inputs."""
    owner = campaign(**(campaign_options or {}))
    values = schedule(owner["id"], kind=kind, date="2054-10-01")["values"]
    values.update(options)
    return SchedulePlan.from_values(values, owner["values"])


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_one_time_slot_has_exact_due_boundary_and_revision_independent_key(kind):
    """No early execution, and a changed civil time retains semantic identity."""
    rule = plan(kind)
    due = datetime(2054, 10, 1, 13, tzinfo=UTC)
    before = rule.page(through=due - timedelta(microseconds=1))
    assert before.slots == () and before.cursor is None and before.exhausted
    page = rule.page(through=due)
    assert len(page.slots) == 1
    assert (page.slots[0].key, page.slots[0].due_at) == ("once", due)
    assert page.cursor == date(2054, 10, 1) and page.exhausted
    assert rule.page(through=FUTURE, after=page.cursor).slots == ()
    assert plan(kind, time="10:00:00").page(through=FUTURE).slots[0].key == "once"


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_next_slot_matches_complete_evaluation_at_the_due_boundary(kind):
    """Preview forecasts share the exact execution instant."""
    rule = plan(kind)
    (slot,) = rule.page(through=FUTURE).slots
    assert rule.next_slot(after=slot.due_at - timedelta(microseconds=1)) == slot
    assert rule.next_slot(after=slot.due_at) is None


@pytest.mark.parametrize("limit", [1, 3, 10, 100])
def test_stable_cursor_partitions_equal_whole_result_and_replay_is_pure(limit):
    """Changing batch sizes does not duplicate or lose any report send.

    A daily report email with a skipped send, so a page's cursor also
    passes a slot that is not a send.
    """
    owner = campaign()
    rule = SchedulePlan.from_values(
        report(campaign_id=owner["id"], skip_dates=["2054-10-02"]), owner["values"]
    )
    expected = rule.page(through=FUTURE, limit=1000).slots
    assert len(expected) == 30 and "2054-10-02" not in {s.key for s in expected}
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


@pytest.mark.parametrize("clock", [None, date(2054, 10, 1), datetime(2054, 10, 1)])
def test_next_slot_rejects_ambiguous_clock(clock):
    """No browser timezone or naive clock may select a different next occurrence."""
    with pytest.raises(ValueError, match="UTC cutoff"):
        plan().next_slot(after=clock)


def test_cursor_before_start_and_after_all_history_does_not_replay():
    """Keyset evaluation accepts terminal date-range cursors."""
    rule = plan()
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
        {"after": "2054-10-01"},
        {"after": datetime(2054, 10, 1)},
        {"limit": True},
        {"limit": 0},
        {"limit": 1001},
        {"limit": 1.5},
    ],
)
def test_invalid_evaluation_arguments_are_rejected(arguments):
    """Do not accept naive cutoffs, coerced dates or unbounded batch sizes."""
    with pytest.raises(ValueError):
        plan().page(**({"through": FUTURE} | arguments))


@pytest.mark.parametrize("kind", ["daily_digest", "weekly_digest"])
def test_a_retired_digest_has_no_schedule(kind):
    """No digest definition remains (#1834), so its values are refused (#2107)."""
    owner = campaign()
    values = schedule(owner["id"], kind="initial", date=None)["values"]
    with pytest.raises(ValueError, match="Only Family mail and report emails"):
        SchedulePlan.from_values(values | {"kind": kind}, owner["values"])


def test_preview_is_recomputed_from_the_proposed_campaign_zone():
    """A draft timezone preview cannot accidentally use the parish's old zone."""
    owner = campaign()
    values = schedule(owner["id"])["values"]
    before = upcoming_slots(values, owner["values"], now=BEFORE, limit=2)
    after = upcoming_slots(
        values,
        owner["values"] | {"timezone": "America/Los_Angeles"},
        now=BEFORE,
        limit=2,
    )
    assert [slot.key for slot in before.slots] == ["once"]
    assert [slot.key for slot in after.slots] == ["once"]
    assert after.slots[0].due_at - before.slots[0].due_at == timedelta(hours=3)
    assert before.exhausted


def test_invalid_schedule_uses_the_existing_configuration_contract():
    """The planner cannot silently repair an invitation outside campaign dates."""
    with pytest.raises(ConfigError):
        plan("initial", date="2054-09-30")


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_family_mail_never_takes_skip_dates(kind):
    """Only an Admin report skips a send; Family mail refuses skip dates."""
    with pytest.raises(ConfigError):
        plan(kind, skip_dates=["2054-10-01"])


def test_upcoming_sends_start_after_now_and_never_list_a_passed_one():
    """The next sends after now, not the first from the campaign's start (#1129)."""
    owner = campaign()
    values = report(campaign_id=owner["id"], schedule=REPORT_AT_NINE)
    # 9:00 AM New York on Saturday, October 10, 2054 is 13:00 UTC: a send
    # due exactly now is not after now, so it is not listed.
    now = datetime(2054, 10, 10, 13, tzinfo=UTC)
    page = upcoming_slots(values, owner["values"], now=now, limit=2)
    assert [slot.key for slot in page.slots] == ["2054-10-11", "2054-10-12"]
    assert all(slot.due_at > now for slot in page.slots)
    assert not page.exhausted


def test_upcoming_sends_end_with_the_schedule():
    """A dated send lists once until it passes; the last sends exhaust the page."""
    owner = campaign()
    values = schedule(owner["id"])["values"]
    due = datetime(2054, 10, 1, 13, tzinfo=UTC)
    ahead = upcoming_slots(values, owner["values"], now=due - timedelta(seconds=1))
    assert [(slot.key, slot.due_at) for slot in ahead.slots] == [("once", due)]
    assert ahead.exhausted
    passed = upcoming_slots(values, owner["values"], now=due)
    assert passed.slots == () and passed.exhausted
    daily = report(campaign_id=owner["id"], schedule=REPORT_AT_NINE)
    # Two sends remain on the morning of October 30: that day's and the
    # campaign's last day's.
    last = upcoming_slots(
        daily, owner["values"], now=datetime(2054, 10, 30, 12, tzinfo=UTC), limit=3
    )
    assert [slot.key for slot in last.slots] == ["2054-10-30", "2054-10-31"]
    assert last.exhausted


# A daily report email sent at 9:00 AM through the campaign's last day.
REPORT_AT_NINE = {
    "type": "repeat",
    "frequency": "daily",
    "from": "campaign_start",
    "until": "campaign_end",
    "time": "09:00:00",
}
