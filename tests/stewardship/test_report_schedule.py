"""A report email's sends through the schedule evaluator (#1786).

``ReportPlan`` evaluates a report's repeat rule or one-time send with its
campaign bounds, slot basis, skip dates and pause intervals (report-emails
spec, "Schedules"). The shared case table pins it, and the database's slot
check pins itself to the same table (``test_report_pipeline_postgresql``).
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from parishkit.stewardship.campaigns.schedule_evaluation import (
    ReportPlan,
    SchedulePlan,
)

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "report_slot_cases.json").read_text()
)["cases"]
NEVER = datetime.max.replace(tzinfo=UTC)
NEW_YORK = {
    "timezone": "America/New_York",
    "start_date": "2026-10-05",
    "end_date": "2026-11-06",
}


def report(**values):
    """Valid report values with ``values`` replacing the defaults."""
    base = {
        "campaign_id": "6f1c5a52-6c3e-4f2f-9a59-0f4b3c1f2e10",
        "kind": "report",
        "name": "Report",
        "subject": "",
        "intro_html": "",
        "intro_text": "",
        "recipients": [{"group": "administrators"}],
        "schedule": {
            "type": "repeat",
            "frequency": "daily",
            "from": "campaign_start",
            "until": "campaign_end",
            "time": "07:00:00",
        },
        "as_of": "send_time",
        "blocks": [{"block": "response_funnel", "version": 1, "options": {}}],
        "content_required": False,
        "skip_dates": [],
        "paused_ranges": [],
        "slot_basis": "send_date",
    }
    return base | values


def due(plan, slot):
    """The send ``plan`` makes for the slot key ``slot``, or None."""
    try:
        day = date.fromisoformat(slot)
    except ValueError:
        return None
    if day.isoformat() != slot:
        return None
    page = plan.page(through=NEVER, after=day - timedelta(days=1), limit=1)
    found = [item for item in page.slots if item.key == slot]
    return found[0].due_at.isoformat() if found else None


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_shared_case_table(case):
    """Each slot's due instant, or no send, is the table's."""
    plan = SchedulePlan.from_values(report(**case["values"]), case["campaign"])
    assert type(plan) is ReportPlan
    assert {slot: due(plan, slot) for slot in case["slots"]} == case["slots"]


def test_pages_keep_a_slot_cursor_past_skipped_and_paused_sends():
    """The cursor is the last examined slot, even one that is not a send.

    A converted Daily keys its slots by the day it reports on, so its
    cursor names report days; a page is exhausted at the first send due
    after the cutoff, and a full page is not.
    """
    plan = SchedulePlan.from_values(
        report(
            schedule={
                "type": "repeat",
                "frequency": "daily",
                "from": "day_after_campaign_start",
                "until": "day_after_campaign_end",
                "time": "07:00:00",
            },
            slot_basis="report_date",
            skip_dates=["2026-10-06"],
        ),
        NEW_YORK,
    )
    first = plan.page(through=NEVER, limit=2)
    assert [slot.key for slot in first.slots] == ["2026-10-05"]
    assert (first.cursor, first.exhausted) == (date(2026, 10, 6), False)
    cutoff = datetime(2026, 10, 9, 11, tzinfo=UTC)
    rest = plan.page(through=cutoff, after=first.cursor)
    assert [slot.key for slot in rest.slots] == ["2026-10-07", "2026-10-08"]
    assert (rest.cursor, rest.exhausted) == (date(2026, 10, 8), True)
    assert rest.slots[-1].due_at == cutoff
    tail = plan.page(through=NEVER, after=date(2026, 11, 5))
    assert [slot.key for slot in tail.slots] == ["2026-11-06"]
    assert tail.exhausted


def test_until_archived_pages_are_bounded_by_the_cutoff():
    """A rule with no last day still ends each page at its cutoff or limit."""
    plan = SchedulePlan.from_values(
        report(
            schedule={
                "type": "repeat",
                "frequency": "weekly",
                "weekdays": [0, 3],
                "from": "campaign_start",
                "until": "archive",
                "time": "08:00:00",
            }
        ),
        NEW_YORK,
    )
    page = plan.page(through=NEVER, limit=100)
    assert len(page.slots) == 100 and not page.exhausted
    assert page.slots[-1].local_date == page.cursor
    later = plan.page(through=datetime(2026, 10, 9, tzinfo=UTC))
    assert [slot.key for slot in later.slots] == ["2026-10-05", "2026-10-08"]
    assert later.exhausted


def test_the_next_send_skips_pauses_and_stops_at_an_open_one():
    """A closed pause hides its sends; an open one has no send after it."""
    pauses = [
        {"start": "2026-10-06T00:00:00+00:00", "end": "2026-10-08T00:00:00+00:00"},
    ]
    plan = SchedulePlan.from_values(
        report(
            paused_ranges=pauses, schedule=report()["schedule"] | {"until": "archive"}
        ),
        NEW_YORK,
    )
    after = datetime(2026, 10, 5, 12, tzinfo=UTC)
    assert plan.next_slot(after=after).key == "2026-10-08"
    paused = SchedulePlan.from_values(
        report(
            paused_ranges=pauses[:1]
            + [{"start": "2026-10-09T00:00:00+00:00", "end": None}],
            schedule=report()["schedule"] | {"until": "archive"},
        ),
        NEW_YORK,
    )
    assert paused.next_slot(after=datetime(2026, 10, 8, 12, tzinfo=UTC)) is None
    assert paused.next_slot(after=after).key == "2026-10-08"


def test_a_one_time_send_has_one_slot():
    """Its slot is its date; once it is due there is no next send."""
    plan = SchedulePlan.from_values(
        report(schedule={"type": "once", "date": "2026-12-01", "time": "09:00:00"}),
        NEW_YORK,
    )
    slot = plan.next_slot(after=datetime(2026, 10, 1, tzinfo=UTC))
    assert (slot.key, slot.due_at) == (
        "2026-12-01",
        datetime(2026, 12, 1, 14, tzinfo=UTC),
    )
    assert plan.next_slot(after=slot.due_at) is None
    page = plan.page(through=slot.due_at)
    assert ([item.key for item in page.slots], page.exhausted) == (["2026-12-01"], True)
    assert plan.page(through=slot.due_at, after=page.cursor).slots == ()


def test_only_a_one_time_send_has_a_one_time_due_instant():
    """As ``SchedulePlan``'s field, which the schedule table reads for each row."""
    once = SchedulePlan.from_values(
        report(schedule={"type": "once", "date": "2026-12-01", "time": "09:00:00"}),
        NEW_YORK,
    )
    assert once.one_time_due == datetime(2026, 12, 1, 14, tzinfo=UTC)
    assert SchedulePlan.from_values(report(), NEW_YORK).one_time_due is None


def test_planning_reads_only_the_schedule_fields():
    """A stored report whose blocks no longer validate still plans its sends.

    Full validation belongs to save and Apply; a later block version must
    not stop a saved report's schedule.
    """
    values = report(blocks=[{"block": "retired", "version": 9, "options": {}}])
    plan = SchedulePlan.from_values(values, NEW_YORK)
    assert plan.next_slot(after=datetime(2026, 10, 1, tzinfo=UTC)).key == "2026-10-05"


def test_evaluation_refuses_a_local_cutoff():
    """Cutoffs are UTC instants, as every schedule evaluation's are."""
    plan = SchedulePlan.from_values(report(), NEW_YORK)
    with pytest.raises(ValueError):
        plan.page(through=datetime(2026, 10, 5))
    with pytest.raises(ValueError):
        plan.next_slot(after=datetime(2026, 10, 5))
