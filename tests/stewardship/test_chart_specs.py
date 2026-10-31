"""The funnel and activity chart specifications, from synthetic metrics (#477).

The builders are pure functions of ``ResponseMetrics``: the specs are plain
JSON that both renderers accept, the summaries and tables carry the exact
counts, and times are the campaign's wall clock whatever zone renders them.
"""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import vl_convert
from matplotlib.colors import CSS4_COLORS

from parishkit.stewardship.archive.workbook import BAND_EDGES
from parishkit.stewardship.jobs.send_history import SendKey
from parishkit.stewardship.reports import chart_specs
from parishkit.stewardship.reports import comparison_charts as charts
from parishkit.stewardship.reports.chart_assets import VEGA_LITE_SCHEMA
from parishkit.stewardship.reports.chart_specs import (
    WIDTH,
    ChartDocument,
    activity_chart,
    financial_charts,
    funnel_chart,
    label_room,
    participation_chart,
    quiet_slots,
    round_ticks,
    share,
    stage_label,
    whole_dollars,
)
from parishkit.stewardship.reports.participation import ParticipationDay
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
from parishkit.stewardship.web.design_tokens import (
    CHART_COLORS,
    CHART_PORTAL_TOKENS,
    COLORS,
    hex6,
)

from .test_comparison_charts import comparison
from .test_participation_rendering import document as participation
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


def test_activity_chart_refuses_unknown_series():
    """Only the known lines can be drawn, and at least one of them."""
    for series in ((), (("other", "Other", "#000000", [1, 0]),)):
        with pytest.raises(ValueError):
            activity_chart(metrics(), series=series)


def financial_summary():
    """A shaped Financial stewardship summary, as financial.shape_result leaves it."""
    return dict(
        families=8,
        frequencies=[
            ("Weekly", 1),
            ("Monthly", 4),
            ("Quarterly", 0),
            ("Annual", 2),
            ("No frequency", 1),
        ],
        share_counts={"online": 6, "envelopes": 3},
        no_share=1,
    )


# The offered share methods, as financial.shape_result lists them.
CHOICES = [("online", "Online <giving>"), ("envelopes", "Envelopes")]


def test_financial_charts_carry_each_count_and_its_share_everywhere():
    """Labels, tooltips, names, summary and table share the counts (#720, #1266).

    Shares are of the matching Families, to one decimal; share methods can
    add up past them, since a Family can choose more than one.
    """
    frequency, shares = financial_charts(financial_summary(), CHOICES)
    assert (frequency.key, frequency.title) == (
        "pledge-frequency",
        "Pledges by frequency",
    )
    assert (shares.key, shares.title) == ("share-methods", "Share methods chosen")
    spec = json.loads(json.dumps(frequency.spec))
    assert spec["$schema"] == VEGA_LITE_SCHEMA and spec["width"] == WIDTH
    assert spec["description"] == frequency.summary
    assert [
        (row["label"], row["count"], row["share"], row["text"])
        for row in spec["data"]["values"]
    ] == [
        ("Weekly", 1, "12.5%", "1 (12.5%)"),
        ("Monthly", 4, "50%", "4 (50%)"),
        ("Quarterly", 0, "0%", "0 (0%)"),
        ("Annual", 2, "25%", "2 (25%)"),
        ("No frequency", 1, "12.5%", "1 (12.5%)"),
    ]
    assert [layer["mark"]["type"] for layer in spec["layer"]] == ["bar", "text"]
    # Each bar is named in one phrase; its drawn count stays out of the
    # accessibility tree, so it is not announced twice.
    assert spec["layer"][0]["encoding"]["description"] == {"field": "name"}
    assert spec["data"]["values"][1]["name"] == (
        "Monthly: 4 families, 50% compared with matching Families"
    )
    # Each bar's count and share are drawn after it, hidden from assistive
    # technology since the bar's name says them.
    label = spec["layer"][1]
    assert label["mark"]["aria"] is False and label["mark"]["align"] == "left"
    assert label["encoding"]["text"] == {"field": "text"}
    # The axis runs past the longest bar (4) by the widest label's room in
    # pixels, at whatever width the chart is drawn.
    room = label_room(["1 (12.5%)"])
    assert spec["encoding"]["x"]["scale"]["domainMax"] == {
        "expr": f"4 * width / max(width - {room}, 1)"
    }
    # A tick label cut off at the stretched domain's right edge is hidden.
    assert spec["encoding"]["x"]["axis"]["labelBound"] is True
    tooltip = spec["layer"][0]["encoding"]["tooltip"]
    assert [field["title"] for field in tooltip] == [
        "Pledges by frequency",
        "Families",
        "Compared with matching Families",
    ]
    assert frequency.table.headings == (
        "Pledges by frequency",
        "Families",
        "Compared with matching Families",
    )
    assert frequency.summary == (
        "Pledges by frequency: Weekly 1 (12.5%), Monthly 4 (50%), Quarterly 0 "
        "(0%), Annual 2 (25%), No frequency 1 (12.5%)."
    )
    # No share method chosen comes last, after the offered methods.
    assert shares.table.rows == (
        ("Online <giving>", "6", "75%"),
        ("Envelopes", "3", "37.5%"),
        ("No share method chosen", "1", "12.5%"),
    )


def test_financial_charts_with_no_matching_families_dash_every_share():
    """No matching Families draws zero-length bars and no percentages."""
    summary = financial_summary() | dict(
        families=0,
        frequencies=[
            (label, 0) for label, _count in financial_summary()["frequencies"]
        ],
        share_counts={},
        no_share=0,
    )
    frequency, shares = financial_charts(summary, CHOICES)
    assert {row[2] for row in frequency.table.rows} == {"—"}
    # The label is the count alone, with no share to give.
    assert {row["text"] for row in frequency.spec["data"]["values"]} == {"0"}
    assert shares.table.rows == (
        ("Online <giving>", "0", "—"),
        ("Envelopes", "0", "—"),
        ("No share method chosen", "0", "—"),
    )


def test_financial_share_chart_draws_every_offered_method_and_groups_retired():
    """Every offered method has a bar, in configured order, even at zero.

    Methods no longer offered share one bar, before No share method chosen.
    """
    summary = financial_summary() | dict(
        share_counts={"envelopes": 3, "gone": 2, "older": 1}
    )
    _frequency, shares = financial_charts(summary, CHOICES)
    assert shares.table.rows == (
        ("Online <giving>", "0", "0%"),
        ("Envelopes", "3", "37.5%"),
        ("Unavailable share method", "3", "37.5%"),
        ("No share method chosen", "1", "12.5%"),
    )


def test_participation_chart_is_panels_on_one_date_axis():
    """The Participation page's daily chart (#720 slice 2c) is the static
    drawing's design in the chart engine: titled panels on one shared date
    axis, each with its own value axis from zero, no legend, no second
    y-axis, and the dates labelled only under the last panel."""
    chart = participation_chart(participation())
    spec = chart.spec
    assert spec["$schema"] == VEGA_LITE_SCHEMA
    assert chart.key == "daily-participation"
    assert chart.title == "Daily participation — Including Families that left"
    assert spec["resolve"] == {"scale": {"x": "shared"}}
    assert spec["autosize"] == {"type": "fit-x", "contains": "padding"}
    panels = spec["vconcat"]
    assert [panel["title"]["text"] for panel in panels] == [
        "First submissions that day",
        "Cumulative participating Families",
        "Effective annual pledges",
    ]
    marks = [panel["layer"][0]["mark"] for panel in panels]
    assert [mark["type"] for mark in marks] == ["bar", "line", "line"]
    assert marks[2]["strokeDash"] == [6, 3] and "strokeDash" not in marks[1]
    ys = [panel["layer"][0]["encoding"]["y"] for panel in panels]
    assert [y["field"] for y in ys] == ["first", "families", "pledges"]
    assert [y["title"] for y in ys] == ["Families", "Families", "USD"]
    # Each panel is scaled to its own values plus 10%, with whole round ticks.
    assert [y["scale"]["domain"][0] for y in ys] == [0, 0, 0]
    assert ys[1]["scale"]["domain"][1] == pytest.approx(3.3)
    assert ys[1]["axis"]["values"] == [0, 1, 2, 3]
    assert ys[2]["axis"]["values"] == [0, 1000, 2000, 3000]
    assert "format" not in ys[2]["axis"] and "$~s" in ys[2]["axis"]["labelExpr"]
    # Never a second y-axis or a legend: no layer has its own y axis or a
    # color or dash encoded by a field.
    for panel in panels:
        assert all(
            "color" not in layer["encoding"] and "strokeDash" not in layer["encoding"]
            for layer in panel["layer"]
        )
    xs = [panel["encoding"]["x"] for panel in panels]
    assert all(x["field"] == "tick" and x["type"] == "ordinal" for x in xs)
    # Dates in order, whatever their text order, by an explicit domain.
    assert xs[0]["scale"]["domain"] == [row["tick"] for row in spec["data"]["values"]]
    assert [x["axis"].get("labels", True) for x in xs] == [False, False, True]
    assert xs[-1]["title"] == ["Campaign date", "(America/New_York)"]
    # Room between the end date labels and the two-line title.
    assert xs[-1]["axis"]["titlePadding"] == 8
    assert xs[-1]["axis"]["labelOverlap"] and "labelExpr" not in xs[-1]["axis"]
    # Vega's own accessible names would read raw fields (ISO dates,
    # unrounded dollars), so the marks and axes are hidden; the view's name,
    # summary and the daily table are the accessible path.
    for panel in panels:
        drawn = panel["layer"][0]
        assert drawn["mark"]["aria"] is False
        assert drawn["mark"].get("point", {"aria": False})["aria"] is False
        assert drawn["encoding"]["y"]["axis"]["aria"] is False
        assert panel["encoding"]["x"]["axis"]["aria"] is False
    # The hover column per date, on top and hidden from assistive technology.
    for panel in panels:
        hover = panel["layer"][-1]
        assert hover["mark"] == {"type": "bar", "opacity": 0, "aria": False}
        assert hover["encoding"]["tooltip"] == {"field": "tip"}
    # Each column selects its date (one selection per panel, since a chart
    # names each once), and under it every panel's guide line follows any of
    # them, so one line crosses all the panels (#1536).
    names = ["hover0", "hover1", "hover2"]
    for panel, name in zip(panels, names, strict=True):
        guide, hover = panel["layer"][-2:]
        assert hover["params"] == [chart_specs.hover_param("tick", name)]
        assert hover["params"][0]["select"]["fields"] == ["tick"]
        assert guide["mark"] == {"type": "rule", "color": COLORS["ink"], "aria": False}
        # On the panel's own date axis, which it inherits.
        assert guide["encoding"] == {
            "opacity": {
                "condition": {
                    "test": {"or": [{"param": n, "empty": False} for n in names]},
                    "value": 0.5,
                },
                "value": 0,
            }
        }
    assert json.loads(json.dumps(spec)) == spec


@pytest.mark.parametrize(
    ("style", "first", "heading"),
    [
        ("us_long", "Oct 31, 2026", "November 2, 2026"),
        ("eu_dot", "31.10.26", "02.11.2026"),
        ("iso", "2026-10-31", "2026-11-02"),
    ],
)
def test_participation_chart_dates_follow_the_parish_style(style, first, heading):
    """The date axis and the tooltips write dates in the parish's style, as
    the static drawing, the daily table and the exports do."""
    with using(style):
        chart = participation_chart(participation())
    rows = chart.spec["data"]["values"]
    assert rows[0]["tick"] == first
    assert rows[-1]["tip"]["title"] == heading
    assert chart.spec["vconcat"][0]["encoding"]["x"]["scale"]["domain"][0] == first


def test_participation_chart_rows_tooltips_and_summary():
    """Each date's tooltip has its date as the heading and its figures in
    whole dollars; the summary gives the range and the last date's figures."""
    chart = participation_chart(participation())
    rows = chart.spec["data"]["values"]
    assert [row["day"] for row in rows] == ["2026-10-31", "2026-11-01", "2026-11-02"]
    assert rows[-1]["tip"] == {
        "title": "November 2, 2026",
        "First submissions": "1",
        "Families that have responded": "3 out of 1,234 (0.2%)",
        "Effective annual pledges": "$3,235",
    }
    assert rows[0]["pledges"] == pytest.approx(1234.56)
    assert chart.summary == (
        "Daily participation, Including Families that left, from October 31, 2026 to "
        "November 2, 2026; by the last date 3 out of 1,234 (0.2%) Families had "
        "responded, 1 of them for the first time that day, with $3,235 in "
        "effective annual pledges."
    )
    assert chart.spec["description"] == chart.summary
    assert chart.table.headings[-1] == "Effective annual pledges"
    assert chart.table.rows[0] == (
        "October 31, 2026",
        "1",
        "1 out of 1,234 (0.1%)",
        "$1,235",
    )


def test_participation_chart_gaps_and_without_financial():
    """An unavailable date is a gap (None) with Unavailable in its tooltip,
    never zero; without financial there is no pledges panel or figure."""
    value = participation()
    gap = ParticipationDay(
        value.days[1].local_date, 0, 0, 0, None, None, False, False, None
    )
    chart = participation_chart(
        replace(value, days=(value.days[0], gap, value.days[2]))
    )
    row = chart.spec["data"]["values"][1]
    assert (row["first"], row["families"], row["pledges"]) == (None, None, None)
    assert row["tip"]["Families that have responded"] == "Unavailable"
    assert row["tip"]["Effective annual pledges"] == "Unavailable"
    for panel in chart.spec["vconcat"][1:]:
        assert panel["layer"][0]["mark"]["invalid"] == "break-paths-show-domains"
    plain = participation_chart(replace(value, financial_enabled=False))
    assert len(plain.spec["vconcat"]) == 2
    assert "Effective annual pledges" not in plain.spec["data"]["values"][0]["tip"]
    assert "pledges" not in plain.summary
    assert len(plain.table.headings) == 3
    assert plain.spec["vconcat"][-1]["encoding"]["x"]["axis"]["labelOverlap"]
    last = replace(
        value, days=(*value.days[:2], replace(gap, local_date=value.days[2].local_date))
    )
    assert participation_chart(last).summary.endswith(
        "; the last date's figures are unavailable."
    )


def page_warnings(spec, capfd):
    """Vega-Lite's compile warnings for ``spec`` as the page draws it.

    chart-v1.js gives the spec (or each stacked panel) the container's
    width, so that is compiled here; vl-convert prints the warnings, which
    ``capfd`` reads from the process's output.
    """
    spec = json.loads(json.dumps(spec))
    for part in spec.get("vconcat", [spec]):
        part["width"] = "container"
    capfd.readouterr()
    vl_convert.vegalite_to_vega(spec, show_warnings=True)
    captured = capfd.readouterr()
    return [line for line in (captured.out + captured.err).splitlines() if line]


def page_charts():
    """Every chart the app builds, as its page draws it.

    The response dashboard's funnel and activity (by hour and by day), the
    Financial stewardship summary's count bars,
    the Response dashboard's daily chart, and the
    Campaign comparison's day-by-day, final-figures and pledge-band charts.
    """
    built = comparison()
    bands = (
        charts.distribution_chart(found, BAND_EDGES)
        for found in (built.current, *built.imported)
    )
    return [
        funnel_chart(metrics()),
        activity_chart(metrics()),
        activity_chart(metrics(grain="day")),
        *financial_charts(financial_summary(), CHOICES),
        participation_chart(participation()),
        *charts.day_charts(built),
        *charts.final_charts(built),
        *(chart for chart in bands if chart is not None),
    ]


def colours_in(value):
    """Every hex colour string anywhere in a specification (marks, scales,
    config, and data fields a mark reads its colour from)."""
    if isinstance(value, str):
        return {value} if re.fullmatch(r"#(?:[0-9a-fA-F]{3}){1,2}", value) else set()
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(set(), *(colours_in(part) for part in value))
    return set()


def test_every_chart_colour_is_a_token():
    """Each colour a chart names is a token's light value (#1264 slice 2).

    chart-v1.js draws a colour in the page's scheme by finding the token
    whose light value it is, so a literal colour no token has would stay
    light on a dark page. The email keeps exactly these light values.
    """
    names = (*CHART_COLORS, *CHART_PORTAL_TOKENS)
    light = {hex6((COLORS | CHART_COLORS)[name]) for name in names}
    documents = [
        *page_charts(),
    ]
    found = set().union(*(colours_in(document.spec) for document in documents))
    assert found and {hex6(colour) for colour in found} <= light
    # The chart modules spell no colour of their own: no string that is a
    # hex colour (3, 4, 6 or 8 digits), an rgb()/rgba()/hsl()/hsla()
    # colour, or a CSS colour name. (Issue numbers such as #720 are not
    # strings.)
    source = Path(chart_specs.__file__).parent
    for name in ("chart_specs.py", "comparison_charts.py"):
        text = (source / name).read_text()
        hexes = r"[\"']#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})[\"']"
        assert not re.findall(hexes, text), name
        assert not re.findall(r"[\"']\s*(?:rgba?|hsla?)\(", text, re.I), name
        strings = {word.lower() for word in re.findall(r"[\"']([A-Za-z]+)[\"']", text)}
        # A token's name ("gold") is how a colour should be named.
        assert not (strings - set(COLORS)) & set(CSS4_COLORS), name


def test_every_page_chart_compiles_without_warnings(capfd):
    """Every chart compiles, as the page draws it, without a Vega-Lite
    warning: the browser logs each one on every draw (a step height beside
    the container width warned that fit-y was dropped, #1325). A step
    height still warns, so the check can fail."""
    documents = page_charts()
    # Every kind of chart is here: 2 + 1 + 2 + 1 + 3 + 2 + 3.
    assert len(documents) == 14
    for document in documents:
        assert page_warnings(document.spec, capfd) == [], document.key
    # A bar chart (here the funnel and the final figures) given back a step
    # height warns again. The loop above covers the other three fixes: the
    # name sorts, the line width's sort and the merged series legend.
    finals = next(d for d in documents if d.key == "comparison-final-families")
    for document in (documents[0], finals):
        stepped = document.spec | {"height": {"step": 36}}
        assert any("fit-y" in line for line in page_warnings(stepped, capfd))


def chart_views(spec):
    """Every view of a chart: the spec itself, its panels and their layers."""
    yield spec
    for part in (*spec.get("vconcat", ()), *spec.get("layer", ())):
        yield from chart_views(part)


def hover_guides(spec):
    """The chart's hover guide lines: rules shown only while a selection is set."""
    return [
        view
        for view in chart_views(spec)
        if view.get("mark", {}).get("type") == "rule"
        and "condition" in view.get("encoding", {}).get("opacity", {})
    ]


# The one date chart without a guide line: the Response dashboard's activity
# chart, which #1537 retires (reports spec, "Response activity over time").
GUIDELESS = {"response-activity"}


def test_every_date_chart_with_tooltips_has_the_hover_guide():
    """A chart on a date, time or campaign-day axis that shows tooltips draws
    the shared hover guide line in each panel, driven by its own hover
    bands' selections (ui-conventions, "Charts"; #1536), so a new chart
    cannot leave it out. A category chart (a nominal or ordinal y axis: rows
    of bars that react as a whole) has none."""
    guided = set()
    for document in page_charts():
        spec = document.spec
        views = list(chart_views(spec))
        assert any("tooltip" in view.get("encoding", {}) for view in views)
        # Keyed on y, not x: a participation chart's x is an ordinal date
        # band, so only a category chart's y axis tells the two apart.
        categories = {
            view["encoding"]["y"].get("type")
            for view in views
            if isinstance(view.get("encoding", {}).get("y"), dict)
        } & {"nominal", "ordinal"}
        if categories or document.key in GUIDELESS:
            assert hover_guides(spec) == [], document.key
            continue
        guided.add(document.key)
        params = {param["name"] for view in views for param in view.get("params", ())}
        for panel in spec.get("vconcat", [spec]):
            (guide,) = hover_guides(panel)
            assert guide["mark"]["color"] == chart_specs.GUIDE, document.key
            assert guide["mark"]["aria"] is False
            condition = guide["encoding"]["opacity"]["condition"]
            tests = condition.get("test", {}).get("or", [condition])
            assert {test["param"] for test in tests} <= params, document.key
            assert all(test["empty"] is False for test in tests)
    assert guided == {
        "daily-participation",
        "comparison-families",
        "comparison-percent",
        "comparison-pledged",
    }


def test_hover_guide_shows_only_while_hovered():
    """The guide is a rule in the shared colour at half opacity while its
    selection is set and invisible otherwise, so a print or an image never
    shows it; given rows, it draws from them."""
    x = {"field": "day", "type": "quantitative"}
    assert chart_specs.hover_guide(x, data=[{"day": 1}]) == {
        "data": {"values": [{"day": 1}]},
        "mark": {"type": "rule", "color": COLORS["ink"], "aria": False},
        "encoding": {
            "x": x,
            "opacity": {
                "condition": {"param": "hover", "empty": False, "value": 0.5},
                "value": 0,
            },
        },
    }
    assert chart_specs.hovered(1, ("a", "b")) == {
        "condition": {
            "test": {
                "or": [{"param": "a", "empty": False}, {"param": "b", "empty": False}]
            },
            "value": 1,
        },
        "value": 0,
    }


def empty_activity_charts():
    """The activity charts of a campaign with no activity, with and without
    sends, as the dashboard draws them (by hour and by day)."""
    return [
        activity_chart(found)
        for sends in (False, True)
        for found in (metrics((), sends=sends), metrics((), grain="day", sends=sends))
    ]


def data_domains(spec):
    """The scales of ``spec``'s compiled Vega whose domain reads a dataset.

    A literal domain compiles to a list, or for a temporal scale to a
    ``fields`` list of constant signals; a data-derived one names its
    dataset (``{"data": ..., "field": ...}``, alone or in ``fields``).
    """
    found = set()
    for scale in vl_convert.vegalite_to_vega(spec)["scales"]:
        domain = scale["domain"]
        if isinstance(domain, dict):
            refs = domain.get("fields", [domain])
            if any(isinstance(ref, dict) and "data" in ref for ref in refs):
                found.add(scale["name"])
    return found


def axis_labels(spec):
    """The tick labels of each axis of ``spec``'s drawn SVG (x, then y)."""
    svg = vl_convert.vegalite_to_svg(spec)
    groups = re.findall(r'<g class="mark-text role-axis-label"[^>]*>(.*?)</g>', svg)
    return [re.findall(r">([^<]*)</text>", group) for group in groups]


def test_empty_activity_charts_draw_without_warnings(capfd):
    """An empty activity chart compiles without a warning, no scale of its
    compiled Vega takes its domain from data (an empty layer's extent is
    infinite, which Vega warns about on every draw, #1327), and both of its
    axes draw tick labels (the y axis only its "0" baseline: a "1" would
    read as a real count). A chart with
    activity still takes its domains from its rows, so the check can fail.
    (Vega's runtime warnings are caught by the browser tests' console
    watch: vl-convert does not report them, and an epoch-number x domain,
    which warned in the browser, drew here.)"""
    for document in empty_activity_charts():
        assert page_warnings(document.spec, capfd) == []
        assert data_domains(document.spec) == set(), document.summary
        x, y = axis_labels(document.spec)
        assert x and y == ["0"], (document.summary, x, y)
    assert data_domains(activity_chart(metrics()).spec) == {"x", "y"}


def test_empty_activity_scales_span_the_sends_or_a_trailing_window():
    """The x axis spans the sends as their markers drew it, or with fewer
    than two a day (by hour) or a week (by day) up to ``as_of``; the y axis
    is 0 to 1, so its axis keeps the ticks of a count (#1327)."""

    def domains(found):
        spec = activity_chart(found).spec
        x = spec["encoding"]["x"]["scale"]["domain"]
        return [
            datetime(
                at["year"],
                at["month"],
                at["date"],
                at["hours"],
                at["minutes"],
                tzinfo=UTC,
            )
            for at in x
        ], spec["layer"][0]["encoding"]["y"]["scale"]["domain"]

    wall = START.astimezone(ZoneInfo(NEW_YORK)).replace(tzinfo=UTC)
    x, y = domains(metrics(()))
    assert x == [wall - timedelta(minutes=5), wall + timedelta(hours=20)]
    assert y == [0, 1]
    as_of = wall + timedelta(days=1)
    assert domains(metrics((), sends=False))[0] == [as_of - timedelta(days=1), as_of]
    week = domains(metrics((), grain="day", sends=False))[0]
    assert week == [as_of - timedelta(days=7), as_of]
    assert "domain" not in activity_chart(metrics()).spec["encoding"]["x"]["scale"]


def test_activity_counts_are_labelled_once_each():
    """The activity chart's Families axis labels whole numbers, each once:
    Vega's own ticks step by halves up to a peak of 2, which the whole-number
    format showed as "0, 1, 1" (#1327). Up to a peak of 5 every whole number
    is labelled; above it Vega's own whole steps stay."""
    for peak in (1, 2, 3, 4, 5, 7, 12, 37):
        activity = (
            ActivityBucket(START, peak, 0, 0),
            ActivityBucket(START + timedelta(hours=1), 0, 1, 0),
        )
        document = activity_chart(replace(metrics(), activity=activity))
        _x, y = axis_labels(document.spec)
        assert len(set(y)) == len(y), (peak, y)
        if peak <= 5:
            assert y == [str(count) for count in range(peak + 1)], (peak, y)
        assert y[0] == "0" and int(y[-1]) >= peak, (peak, y)


def test_empty_activity_window_is_campaign_wall_time():
    """The trailing window is a day of the campaign's wall clock, so across
    the autumn change it is 24 axis hours, not the 25 an absolute day spans."""
    as_of = datetime(2026, 11, 1, 12, tzinfo=UTC)  # 7 AM EST, after the change
    found = replace(metrics((), sends=False), as_of=as_of)
    start, end = activity_chart(found).spec["encoding"]["x"]["scale"]["domain"]
    assert (start["date"], start["hours"]) == (31, 7)
    assert (end["date"], end["hours"]) == (1, 7)


def test_bar_chart_heights_match_their_old_step_heights():
    """Each bar chart's fixed height is its bar count times the step height
    it had (#1325), so every bar and label keeps its room."""
    steps = {"response-funnel": 36, "comparison-final-": 26, "comparison-bands": 40}
    checked = []
    for document in page_charts():
        step = next(
            (step for key, step in steps.items() if document.key.startswith(key)),
            None,
        )
        if step is None:
            continue
        bars = {
            row.get("stage", row.get("name")) for row in document.spec["data"]["values"]
        }
        assert document.spec["height"] == step * len(bars), document.key
        checked.append(document.key)
    # The dashboard's funnel, two final-figure charts and
    # three years' pledge bands.
    assert len(checked) == 6


@pytest.mark.parametrize(
    ("top", "ticks"),
    [
        (1.1, [0, 1]),
        (3.3, [0, 1, 2, 3]),
        (9.9, [0, 2, 4, 6, 8]),
        (55, [0, 20, 40]),
        (3557.4, [0, 1000, 2000, 3000]),
        (1_760_000, [0, 500_000, 1_000_000, 1_500_000]),
    ],
)
def test_round_ticks(top, ticks):
    """Whole 1, 2 or 5 steps from zero, at most five ticks up to the top."""
    assert round_ticks(top) == ticks


def test_whole_dollars_rounds_halves_up():
    """Tooltip pledges are whole dollars, a half rounding up."""
    assert whole_dollars(Decimal("760409.50")) == "$760,410"
    assert whole_dollars(Decimal("0.49")) == "$0"


def closed_participation():
    """The daily chart's document after a response entered after the close.

    The fixture's three days are October 31 to November 2; with an end date
    of November 1 the last day holds a response Staff entered after the
    campaign closed (#2298).
    """
    return replace(participation(), end_date=participation().first_date + timedelta(1))


def test_participation_chart_marks_the_campaign_closed():
    """Each panel draws the dashed Campaign closed line at the start of the
    first late day's band, the top panel names it, and the description
    says what the later dates hold (#2298). It compiles without a warning,
    in token colours, and draws its label once."""
    chart = participation_chart(closed_participation())
    late = chart.spec["data"]["values"][2]["tick"]
    panels = chart.spec["vconcat"]
    for index, panel in enumerate(panels):
        rules = [
            layer
            for layer in panel["layer"]
            if layer.get("data", {}).get("values", [{}])[0].get("tick") == late
        ]
        assert [layer["mark"]["type"] for layer in rules] == (
            ["rule", "text", "text"] if index == 0 else ["rule"]
        )
        rule = rules[0]
        assert rule["mark"]["color"] == chart_specs.MARKER
        assert rule["mark"]["strokeDash"] == [4, 4]
        assert rule["mark"]["aria"] is False
        # The panel's own date encoding, at the band's start.
        assert rule["encoding"]["x"] == panel["encoding"]["x"] | {"bandPosition": 0}
    # The label over a paper halo drawn first (Vega strokes text over its
    # fill), so it keeps its contrast over a bar.
    halo, label = (layer["mark"] for layer in panels[0]["layer"][2:4])
    assert (halo["color"], halo["stroke"]) == (chart_specs.HALO,) * 2
    assert halo["strokeWidth"] == 3
    assert label["color"] == chart_specs.MARKER and "stroke" not in label
    assert {key: halo[key] for key in ("align", "dx")} == {"align": "right", "dx": -3}
    assert chart.summary.endswith(
        " The campaign closed at the end of November 1, 2026; the dates after "
        "it hold responses Staff entered after the campaign closed."
    )
    assert chart.spec["description"] == chart.summary
    svg = vl_convert.vegalite_to_svg(chart.spec)
    assert svg.count(">Campaign closed<") == 2
    assert hover_guides(chart.spec) and all(
        len(hover_guides(panel)) == 1 for panel in panels
    )


def test_participation_chart_closed_compiles_without_warnings(capfd):
    """The closed line adds no Vega-Lite warning (the page logs each one)."""
    chart = participation_chart(closed_participation())
    assert page_warnings(chart.spec, capfd) == []


def test_participation_chart_marks_nothing_up_to_the_end_date():
    """A series that stops at or before the end date draws no line: the chart
    is exactly what it was before #2298."""
    plain = participation_chart(participation())
    ended = participation_chart(
        replace(participation(), end_date=participation().last_date)
    )
    assert plain.spec == ended.spec
    assert "Campaign closed" not in json.dumps(plain.spec)
    assert "closed" not in plain.summary
