"""The funnel and activity chart specifications, from synthetic metrics (#477).

The builders are pure functions of ``ResponseMetrics``: the specs are plain
JSON that both renderers accept, the summaries and tables carry the exact
counts, and times are the campaign's wall clock whatever zone renders them.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.jobs.send_history import SendKey
from parishkit.stewardship.reports.chart_assets import VEGA_LITE_SCHEMA
from parishkit.stewardship.reports.chart_specs import (
    WIDTH,
    ChartDocument,
    activity_chart,
    funnel_chart,
    quiet_slots,
    share,
    stage_label,
)
from parishkit.stewardship.reports.response_metrics import (
    ActivityBucket,
    ResponseMetrics,
    ResponseScope,
    SendMarker,
    Stage,
    activity_series,
    stage_counts,
)
from parishkit.stewardship.web.dates import using

from .test_response_metrics import ROWS, START

NEW_YORK = "America/New_York"


def metrics(families=ROWS, *, grain="hour", sends=True):
    """Synthetic metrics over the response-metrics test rows."""
    scope = ResponseScope(uuid4())
    return ResponseMetrics(
        scope=scope,
        as_of=START + timedelta(days=1),
        timezone=NEW_YORK,
        grain=grain,
        families=families,
        stages=stage_counts(families),
        skipped_responded=0,
        submitted_uninvited=0,
        submitted_again=1,
        activity=activity_series(families, ZoneInfo(NEW_YORK), grain),
        sends=(
            (
                SendMarker(
                    SendKey(uuid4(), uuid4(), "production", 1),
                    "initial",
                    "Invitation",
                    START - timedelta(minutes=5),
                    len(families),
                    START - timedelta(minutes=4),
                    START,
                ),
                SendMarker(
                    SendKey(uuid4(), uuid4(), "production", 1),
                    "reminder",
                    "Reminder 1",
                    START + timedelta(hours=20),
                    0,
                    None,
                    None,
                ),
            )
            if sends
            else ()
        ),
    )


def test_funnel_chart_carries_the_stage_counts_everywhere():
    """Bars, tooltips, the summary and the table all come from the same stages."""
    with using("us_long"):
        chart = funnel_chart(metrics())
    assert isinstance(chart, ChartDocument)
    assert chart.key == "response-funnel" and chart.title == "Response funnel"
    spec = json.loads(json.dumps(chart.spec))
    assert spec["$schema"] == VEGA_LITE_SCHEMA and spec["width"] == WIDTH
    assert spec["description"] == chart.summary
    assert [row["stage"] for row in spec["data"]["values"]] == [
        "Invited",
        "Link followed (includes mail-scanner prefetches)",
        "Form opened",
        "Progressed past the first step",
        "Submitted",
    ]
    counts = [row["families"] for row in spec["data"]["values"]]
    assert counts == [stage.count for stage in metrics().stages] == [4, 5, 4, 3, 3]
    # Stages are not all nested: a Family may follow its link or submit
    # without a delivered invitation, so a share can pass 100%.
    assert [row["share"] for row in spec["data"]["values"]] == [
        "100%",
        "125%",
        "100%",
        "75%",
        "75%",
    ]
    assert chart.table.headings == ("Stage", "Families", "Compared with invited")
    assert chart.table.rows[1] == (
        "Link followed (includes mail-scanner prefetches)",
        "5",
        "125%",
    )
    assert chart.summary == (
        "Families by stage: 4 invited, 5 followed their link (includes "
        "mail-scanner prefetches), 4 opened the form, 3 progressed past the "
        "first step, 3 submitted."
    )
    assert [layer["mark"]["type"] for layer in spec["layer"]] == ["bar", "text"]
    assert chart.notes == ()


def test_funnel_chart_refuses_stages_out_of_order():
    """A funnel is read top to bottom; a reordered input is a programming error."""
    value = metrics()
    reordered = ResponseMetrics(
        **{**value.__dict__, "stages": tuple(reversed(value.stages))}
    )
    with pytest.raises(ValueError):
        funnel_chart(reordered)


def test_share_rounds_half_up_and_dashes_an_empty_total():
    assert share(1, 3) == "33%" and share(2, 3) == "67%" and share(1, 8) == "13%"
    assert share(0, 0) == "—" and share(0, 5) == "0%" and share(5, 5) == "100%"


def test_stage_label_appends_a_note_in_lower_case():
    assert stage_label(Stage("invited", 1)) == "Invited"
    assert stage_label(Stage("link_followed", 1, "Includes scanners")) == (
        "Link followed (includes scanners)"
    )


def test_activity_chart_plots_campaign_wall_time_on_a_utc_axis():
    """Buckets keep their New York wall time whatever zone draws the chart."""
    with using("us_long"):
        chart = activity_chart(metrics())
    spec = json.loads(json.dumps(chart.spec))
    assert chart.key == "response-activity"
    assert chart.title == "Response activity by hour" == spec["title"]["text"]
    assert spec["encoding"]["x"]["scale"] == {"type": "utc"}
    assert spec["encoding"]["x"]["title"] == "Campaign time (America/New_York)"
    lines, rules, labels = spec["layer"]
    # START is 10:00 New York; the first bucket is written as 10:00Z.
    first = lines["data"]["values"][0]
    assert first == {
        "time": "2026-10-03T10:00:00Z",
        "label": "Oct 3, 2026 10:00 AM",
        "series": "Link followed",
        "families": 5,
    }
    assert {row["series"] for row in lines["data"]["values"]} == {
        "Link followed",
        "Form opened",
        "Submitted",
    }
    # Every bucket contributes one row per series, and the counts sum to the
    # funnel totals.
    assert len(lines["data"]["values"]) == 3 * len(metrics().activity)
    assert sum(
        row["families"]
        for row in lines["data"]["values"]
        if row["series"] == "Submitted"
    ) == metrics().stage("submitted")
    assert lines["encoding"]["strokeDash"]["scale"]["range"] == [
        [1, 0],
        [6, 3],
        [2, 2],
    ]
    assert [marker["name"] for marker in rules["data"]["values"]] == [
        "Invitation",
        "Reminder 1",
    ]
    assert rules["data"]["values"][0]["time"] == "2026-10-03T09:55:00Z"
    assert labels["encoding"]["text"] == {"field": "name"}
    assert chart.table.headings == (
        "Campaign time",
        "Link followed",
        "Form opened",
        "Submitted",
    )
    assert chart.table.rows[0] == ("Oct 3, 2026 10:00 AM", "5", "4", "3")
    assert chart.notes == (
        "Invitation: scheduled October 3, 2026 at 9:55 AM EDT; 6 emails "
        "delivered by the cutoff.",
        "Reminder 1: scheduled October 4, 2026 at 6:00 AM EDT; 0 emails "
        "delivered by the cutoff.",
    )
    assert chart.summary == (
        "Link follows, form opens and submissions per hour in America/New_York, "
        "from Oct 3, 2026 10:00 AM to Oct 3, 2026 10:00 AM; 2 sends marked "
        "(Invitation, Reminder 1)."
    )


def test_activity_chart_by_day_labels_dates():
    with using("us_long"):
        chart = activity_chart(metrics(grain="day"))
    assert chart.title == "Response activity by day"
    assert chart.table.rows[0][0] == "Oct 3, 2026"
    assert chart.spec["layer"][0]["data"]["values"][0]["time"] == (
        "2026-10-03T00:00:00Z"
    )


def test_activity_chart_without_activity_or_sends_is_still_a_chart():
    """An empty series renders as an empty chart with an honest summary."""
    with using("us_long"):
        chart = activity_chart(metrics(families=(), sends=False))
    assert chart.summary == "No link follows, form opens or submissions yet."
    assert chart.table.rows == () and chart.notes == ()
    assert chart.spec["layer"][0]["data"]["values"] == []
    assert chart.spec["layer"][1]["data"]["values"] == []
    json.dumps(chart.spec)


def test_wall_time_follows_the_campaign_zone_not_the_instant_zone():
    """An instant given in UTC is still written as the New York wall clock."""
    rows = (ROWS[0],)
    with using("us_long"):
        chart = activity_chart(metrics(families=rows))
    bucket = chart.spec["layer"][0]["data"]["values"][0]
    assert bucket["time"] == "2026-10-03T10:00:00Z"
    assert datetime(2026, 10, 3, 14, tzinfo=UTC) == START


def test_quiet_hours_between_busy_ones_are_drawn_as_zero():
    """A line never runs straight across hours in which nothing happened.

    The chart's data gains a zero row per series for each quiet bucket
    between the first and the last busy one; the exact-values table still
    lists only the busy buckets.
    """
    later = replace(
        ROWS[2], link_at=START + timedelta(hours=3), family_id=uuid4(), family_duid=9
    )
    with using("us_long"):
        chart = activity_chart(metrics(families=(ROWS[0], later)))
    values = chart.spec["layer"][0]["data"]["values"]
    times = sorted({row["time"] for row in values})
    assert times == [f"2026-10-03T{hour}:00:00Z" for hour in (10, 11, 12, 13)]
    quiet = [row for row in values if row["time"] == "2026-10-03T11:00:00Z"]
    assert [row["families"] for row in quiet] == [0, 0, 0]
    assert quiet[0]["label"] == "Oct 3, 2026 11:00 AM"
    assert [row[0] for row in chart.table.rows] == [
        "Oct 3, 2026 10:00 AM",
        "Oct 3, 2026 1:00 PM",
    ]
    # Rows are in time order, so both renderers draw the same polyline.
    assert [row["time"] for row in values] == sorted(row["time"] for row in values)


def test_quiet_slots_skip_the_missing_spring_hour_and_fill_days():
    """The hour the clocks skip is not a slot; quiet days are slots."""
    zone = ZoneInfo(NEW_YORK)
    # 2027-03-14: New York skips from 02:00 to 03:00 local time.
    busy = (
        ActivityBucket(datetime(2027, 3, 14, 1, tzinfo=zone), 1, 0, 0),
        ActivityBucket(datetime(2027, 3, 14, 4, tzinfo=zone), 1, 0, 0),
    )
    hours = [slot.hour for slot in quiet_slots(busy, zone, "hour")]
    assert hours == [3]
    days = (
        ActivityBucket(datetime(2026, 10, 3, tzinfo=zone), 1, 0, 0),
        ActivityBucket(datetime(2026, 10, 6, tzinfo=zone), 1, 0, 0),
    )
    assert [slot.day for slot in quiet_slots(days, zone, "day")] == [4, 5]
    assert quiet_slots((), zone, "hour") == ()
    assert quiet_slots(busy[:1], zone, "hour") == ()


def test_quiet_slots_keep_the_repeated_autumn_hour_once():
    """On 2026-11-01 New York repeats 01:00; on the wall clock it is one slot."""
    zone = ZoneInfo(NEW_YORK)
    busy = (
        ActivityBucket(datetime(2026, 11, 1, 0, tzinfo=zone), 1, 0, 0),
        ActivityBucket(datetime(2026, 11, 1, 3, tzinfo=zone), 1, 0, 0),
    )
    assert [slot.hour for slot in quiet_slots(busy, zone, "hour")] == [1, 2]
    # A busy second reading of 01:00 fills that wall hour.
    repeated = (
        busy[0],
        ActivityBucket(datetime(2026, 11, 1, 1, fold=1, tzinfo=zone), 1, 0, 0),
        busy[1],
    )
    assert [slot.hour for slot in quiet_slots(repeated, zone, "hour")] == [2]


def test_quiet_days_count_even_where_midnight_is_skipped():
    """A zone that changes its clocks at midnight still has the date as a slot."""
    # Santiago skipped 2026-09-06 00:00 (to 01:00) at the spring change.
    zone = ZoneInfo("America/Santiago")
    days = (
        ActivityBucket(datetime(2026, 9, 5, tzinfo=zone), 1, 0, 0),
        ActivityBucket(datetime(2026, 9, 7, tzinfo=zone), 1, 0, 0),
    )
    assert [slot.day for slot in quiet_slots(days, zone, "day")] == [6]


def test_empty_activity_layers_stay_out_of_the_accessibility_tree():
    """A layer with no data is hidden from assistive technology; others are not."""
    with using("us_long"):
        empty = activity_chart(metrics(families=(), sends=False))
        markers = activity_chart(metrics(families=()))
        full = activity_chart(metrics())
    lines, rules, labels = empty.spec["layer"]
    assert lines["mark"]["aria"] is False and lines["mark"]["point"] == {"aria": False}
    assert rules["mark"]["aria"] is False and labels["mark"]["aria"] is False
    lines, rules, _labels = markers.spec["layer"]
    assert lines["mark"]["aria"] is False and "aria" not in rules["mark"]
    assert all("aria" not in layer["mark"] for layer in full.spec["layer"])
