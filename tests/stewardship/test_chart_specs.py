"""The funnel and activity chart specifications, from synthetic metrics (#477).

The builders are pure functions of ``ResponseMetrics``: the specs are plain
JSON that both renderers accept, the summaries and tables carry the exact
counts, and times are the campaign's wall clock whatever zone renders them.
"""

import json
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
    share,
    stage_label,
)
from parishkit.stewardship.reports.response_metrics import (
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
