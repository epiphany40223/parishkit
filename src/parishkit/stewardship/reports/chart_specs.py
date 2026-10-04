"""Vega-Lite specifications for the response funnel and activity charts (#477).

Each chart is one ``ChartDocument``: the Vega-Lite spec the browser draws
(``chart-v1.js``) and the server renders to PNG or SVG (``chart_rendering``),
together with the plain-language summary and the exact data table that
accompany it wherever it appears (the dashboard, the digest emails). Both
renderers get the same JSON, so the page and the email cannot disagree.
Everything here is a pure function of ``response_metrics.ResponseMetrics``;
nothing reads the database or the clock, and the specs are plain JSON values.

Times are campaign-local. A bucket's wall time is written as an ISO instant
with a ``Z`` suffix and plotted on a UTC scale, so neither the browser's nor
the server's own time zone shifts the axis; the axis title names the
campaign's zone instead. The two readings of a repeated autumn hour land on
one tick, as they do on a wall clock; the table and the tooltips carry the
parish-formatted label.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.dates import format_date, format_instant
from parishkit.stewardship.web.presentation import number

from .chart_assets import VEGA_LITE_SCHEMA
from .response_metrics import STAGES

# The email image's CSS width; the browser replaces it with the panel's width
# and the PNG is rendered at twice this many pixels.
WIDTH = 720
# The participation chart's palette (charts.py), so the reports look alike.
BAR = "#26658b"
SERIES = (
    ("links", "Link followed", "#26658b", [1, 0]),
    ("forms", "Form opened", "#8a4f00", [6, 3]),
    ("submissions", "Submitted", "#173d70", [2, 2]),
)
STAGE_LABELS = {
    "invited": "Invited",
    "link_followed": "Link followed",
    "form_opened": "Form opened",
    "progressed": "Progressed past the first step",
    "submitted": "Submitted",
}
# How the summary sentence reads each stage's count.
STAGE_PHRASES = {
    "invited": "invited",
    "link_followed": "followed their link",
    "form_opened": "opened the form",
    "progressed": "progressed past the first step",
    "submitted": "submitted",
}
GRAIN_WORDS = {"hour": "hour", "day": "day"}


@dataclass(frozen=True)
class ChartTable:
    """The chart's exact values as display text: headings and rows."""

    headings: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ChartDocument:
    """One chart as every rendering shows it.

    ``key`` names the chart's elements on a page (``[a-z-]`` only); ``title``
    is its accessible name and the spec's title; ``summary`` is the sentence
    that stands in for the picture (the image's alt text, the figure's
    description); ``spec`` the Vega-Lite JSON; ``table`` the exact values;
    ``notes`` lines shown under the table (the send markers, for example).
    """

    key: str
    title: str
    summary: str
    spec: dict
    table: ChartTable
    notes: tuple[str, ...] = ()


def stage_label(stage):
    """The stage's display label, with its note in parentheses when it has one."""
    label = STAGE_LABELS[stage.key]
    return f"{label} ({stage.note[0].lower()}{stage.note[1:]})" if stage.note else label


def share(count, total):
    """``count`` as a whole percentage of ``total`` ("41%"), or a dash for none."""
    if total <= 0:
        return "—"
    return f"{(200 * count + total) // (2 * total)}%"


def funnel_summary(stages):
    """One sentence with every stage's count, in funnel order."""
    parts = []
    for stage in stages:
        phrase = STAGE_PHRASES[stage.key]
        if stage.note:
            phrase += f" ({stage.note[0].lower()}{stage.note[1:]})"
        parts.append(f"{number(stage.count)} {phrase}")
    return "Families by stage: " + ", ".join(parts) + "."


def funnel_chart(metrics):
    """The response funnel: one bar per stage, labelled with its count."""
    if tuple(stage.key for stage in metrics.stages) != STAGES:
        raise ValueError("A funnel chart needs the stages in funnel order.")
    invited = metrics.stage("invited")
    rows = [
        {
            "stage": stage_label(stage),
            "families": stage.count,
            "order": index,
            "share": share(stage.count, invited),
        }
        for index, stage in enumerate(metrics.stages)
    ]
    summary = funnel_summary(metrics.stages)
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "title": {"text": "Response funnel", "anchor": "start"},
        "width": WIDTH,
        "height": {"step": 36},
        "data": {"values": rows},
        "encoding": {
            "y": {
                "field": "stage",
                "type": "nominal",
                "sort": {"field": "order"},
                "title": None,
                "axis": {"labelLimit": 300},
            },
            "x": {
                "field": "families",
                "type": "quantitative",
                "title": "Families",
                "axis": {"format": ",d", "tickMinStep": 1},
            },
        },
        "layer": [
            {
                "mark": {"type": "bar", "color": BAR},
                "encoding": {
                    "tooltip": [
                        {"field": "stage", "title": "Stage"},
                        {"field": "families", "title": "Families", "format": ","},
                        {"field": "share", "title": "Compared with invited"},
                    ]
                },
            },
            {
                "mark": {"type": "text", "align": "left", "dx": 4},
                "encoding": {
                    "text": {
                        "field": "families",
                        "type": "quantitative",
                        "format": ",",
                    }
                },
            },
        ],
    }
    table = ChartTable(
        ("Stage", "Families", "Compared with invited"),
        tuple((row["stage"], number(row["families"]), row["share"]) for row in rows),
    )
    return ChartDocument("response-funnel", "Response funnel", summary, spec, table)


def wall_time(instant, zone):
    """The campaign-local wall time of ``instant`` as a ``Z`` instant for a UTC axis."""
    return instant.astimezone(zone).replace(tzinfo=None).isoformat() + "Z"


def bucket_label(bucket, zone, grain):
    """The bucket's parish-formatted label: a date for a day, a time for an hour."""
    if grain == "day":
        return format_date(bucket.start.astimezone(zone).date(), compact=True)
    return format_instant(bucket.start, zone, compact=True)


def send_note(send, zone):
    """One line about a send marker for the notes under the table."""
    scheduled = format_instant(send.scheduled, zone)
    return (
        f"{send.name}: scheduled {scheduled}; {number(send.delivered)} "
        f"{'email' if send.delivered == 1 else 'emails'} delivered by the cutoff."
    )


def activity_summary(metrics, labels):
    """One sentence about the series' span and the marked sends."""
    grain = GRAIN_WORDS[metrics.grain]
    if not labels:
        return "No link follows, form opens or submissions yet."
    text = (
        f"Link follows, form opens and submissions per {grain} in "
        f"{metrics.timezone}, from {labels[0]} to {labels[-1]}"
    )
    if metrics.sends:
        names = ", ".join(send.name for send in metrics.sends)
        text += f"; {number(len(metrics.sends))} "
        text += f"{'send' if len(metrics.sends) == 1 else 'sends'} marked ({names})"
    return text + "."


def activity_chart(metrics):
    """The response activity series: three lines per bucket, with send markers."""
    if metrics.grain not in GRAIN_WORDS:
        raise ValueError("Activity is charted by hour or day.")
    zone = ZoneInfo(metrics.timezone)
    labels = tuple(
        bucket_label(bucket, zone, metrics.grain) for bucket in metrics.activity
    )
    rows = [
        {
            "time": wall_time(bucket.start, zone),
            "label": label,
            "series": series,
            "families": getattr(bucket, field),
        }
        for bucket, label in zip(metrics.activity, labels, strict=True)
        for field, series, _color, _dash in SERIES
    ]
    markers = [
        {
            "time": wall_time(send.scheduled, zone),
            "name": send.name,
            "label": format_instant(send.scheduled, zone, compact=True),
            "delivered": send.delivered,
        }
        for send in metrics.sends
    ]
    series_names = [series for _field, series, _color, _dash in SERIES]
    summary = activity_summary(metrics, labels)
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "title": {
            "text": f"Response activity by {GRAIN_WORDS[metrics.grain]}",
            "anchor": "start",
        },
        "width": WIDTH,
        "height": 260,
        "encoding": {
            "x": {
                "field": "time",
                "type": "temporal",
                "scale": {"type": "utc"},
                "title": f"Campaign time ({metrics.timezone})",
                "axis": {"labelOverlap": True},
            }
        },
        "layer": [
            {
                "data": {"values": rows},
                "mark": {"type": "line", "point": True},
                "encoding": {
                    "y": {
                        "field": "families",
                        "type": "quantitative",
                        "title": "Families (first time)",
                        "axis": {"format": ",d", "tickMinStep": 1},
                    },
                    "color": {
                        "field": "series",
                        "type": "nominal",
                        "title": None,
                        "scale": {
                            "domain": series_names,
                            "range": [color for _f, _s, color, _d in SERIES],
                        },
                        "legend": {"orient": "top"},
                    },
                    # A dash pattern per series, so the lines are told apart
                    # without colour.
                    "strokeDash": {
                        "field": "series",
                        "type": "nominal",
                        "title": None,
                        "scale": {
                            "domain": series_names,
                            "range": [dash for _f, _s, _c, dash in SERIES],
                        },
                    },
                    "tooltip": [
                        {"field": "label", "title": "Time"},
                        {"field": "series", "title": "Series"},
                        {"field": "families", "title": "Families", "format": ","},
                    ],
                },
            },
            {
                "data": {"values": markers},
                "mark": {"type": "rule", "color": "#555555", "strokeDash": [4, 4]},
                "encoding": {
                    "tooltip": [
                        {"field": "name", "title": "Send"},
                        {"field": "label", "title": "Scheduled"},
                        {"field": "delivered", "title": "Delivered", "format": ","},
                    ]
                },
            },
            {
                "data": {"values": markers},
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "top",
                    "dx": 3,
                    "dy": 2,
                    "color": "#555555",
                },
                "encoding": {"text": {"field": "name"}, "y": {"value": 0}},
            },
        ],
    }
    table = ChartTable(
        ("Campaign time", *series_names),
        tuple(
            (label, *(number(getattr(bucket, field)) for field, *_ in SERIES))
            for bucket, label in zip(metrics.activity, labels, strict=True)
        ),
    )
    return ChartDocument(
        "response-activity",
        spec["title"]["text"],
        summary,
        spec,
        table,
        tuple(send_note(send, zone) for send in metrics.sends),
    )
