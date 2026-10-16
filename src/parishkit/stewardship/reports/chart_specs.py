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

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.web.dates import format_date, format_instant
from parishkit.stewardship.web.presentation import number, out_of, percentage
from parishkit.stewardship.web.report_markup import TRACK

from .chart_assets import VEGA_LITE_SCHEMA
from .charts import axis_top
from .response_metrics import STAGES, ActivityBucket

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
# How the activity summary names each series.
SERIES_PHRASES = {
    "links": "link follows",
    "forms": "form opens",
    "submissions": "submissions",
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


# Each bar's band height in pixels in the response funnel and the count bars
# (``count_bars``). A chart with these bars has a fixed height of this times
# its bar count, rather than Vega-Lite's {"step": 36}: a step height beside
# the page's "container" width makes Vega-Lite warn on every draw that it
# drops fit-y (#1325).
BAR_STEP = 36


def stage_label(stage, labels=STAGE_LABELS):
    """The stage's display label, with its note in parentheses when it has one."""
    label = labels[stage.key]
    return f"{label} ({stage.note[0].lower()}{stage.note[1:]})" if stage.note else label


def share(count, total):
    """``count`` as a whole percentage of ``total`` ("41%"), or a dash for none."""
    if total <= 0:
        return "—"
    return f"{(200 * count + total) // (2 * total)}%"


def funnel_summary(stages, phrases=STAGE_PHRASES):
    """One sentence with every stage's count, in funnel order."""
    parts = []
    for stage in stages:
        phrase = phrases[stage.key]
        if stage.note:
            phrase += f" ({stage.note[0].lower()}{stage.note[1:]})"
        parts.append(f"{number(stage.count)} {phrase}")
    return "Families by stage: " + ", ".join(parts) + "."


def funnel_chart(metrics, *, keys=STAGES, labels=STAGE_LABELS, phrases=STAGE_PHRASES):
    """The response funnel: one bar per stage, labelled with its count.

    ``keys`` selects the stages drawn, in funnel order, and ``labels`` and
    ``phrases`` name them in the bars and the summary: the dashboard draws
    all five with their own names, the daily digest only its four
    (``digest_funnel.DIGEST_STAGES``, #1016) under its own names. Shares
    are always of the first stage, Invited.
    """
    if tuple(stage.key for stage in metrics.stages) != STAGES or not (
        keys[0] == "invited" and set(keys) <= set(STAGES)
    ):
        raise ValueError("A funnel chart needs the stages in funnel order.")
    invited = metrics.stage("invited")
    stages = tuple(stage for stage in metrics.stages if stage.key in keys)
    rows = [
        {
            "stage": stage_label(stage, labels),
            "families": stage.count,
            "share": share(stage.count, invited),
        }
        for stage in stages
    ]
    summary = funnel_summary(stages, phrases)
    compared = f"Compared with {labels['invited'].lower()}"
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "title": {"text": "Response funnel", "anchor": "start"},
        "width": WIDTH,
        # A fixed height, BAR_STEP a stage (see BAR_STEP).
        "height": BAR_STEP * len(rows),
        "data": {"values": rows},
        "encoding": {
            "y": {
                "field": "stage",
                "type": "nominal",
                # The stages in funnel order by name: a {"field": "order"}
                # sort conflicts between the bar layer's stacked data and
                # the text layer's, and Vega-Lite warns on every draw.
                "sort": [row["stage"] for row in rows],
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
                        {"field": "share", "title": compared},
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
        ("Stage", "Families", compared),
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


def quiet_slots(activity, zone, grain):
    """Wall times between the first and last bucket in which nothing happened.

    The series leaves empty buckets out, and a line drawn straight between
    two busy hours would suggest activity in the quiet ones, so the chart
    plots these slots as zero (the table still lists only busy buckets). On
    the wall-clock axis every hour or day is one equal step; an hour that
    does not exist (the spring change) is not a slot, and a repeated autumn
    hour is one slot.
    """
    if not activity:
        return ()
    step = timedelta(hours=1) if grain == "hour" else timedelta(days=1)
    busy = {bucket.start.astimezone(zone).replace(tzinfo=None) for bucket in activity}
    slots, at, last = [], min(busy), max(busy)
    while (at := at + step) < last:
        aware = at.replace(tzinfo=zone)
        # An hour that does not survive a round trip through UTC is the
        # skipped spring hour. A day is a slot even where its midnight is
        # skipped (zones that change at midnight): the date still exists.
        skipped = (
            grain == "hour"
            and aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != at
        )
        if at not in busy and not skipped:
            slots.append(aware)
    return tuple(slots)


def send_note(send, zone):
    """One line about a send marker for the notes under the table."""
    scheduled = format_instant(send.scheduled, zone)
    return (
        f"{send.name}: scheduled {scheduled}; {number(send.delivered)} "
        f"{'email' if send.delivered == 1 else 'emails'} delivered by the cutoff."
    )


def series_phrase(series, conjunction):
    """The series' plain names joined as a list: "form opens and submissions"."""
    names = [SERIES_PHRASES[field] for field, *_ in series]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" {conjunction} " + names[-1]


def activity_summary(metrics, labels, series=SERIES):
    """One sentence about the series' span and the marked sends."""
    grain = GRAIN_WORDS[metrics.grain]
    if not labels:
        return f"No {series_phrase(series, 'or')} yet."
    phrase = series_phrase(series, "and")
    text = (
        f"{phrase[0].upper()}{phrase[1:]} per {grain} in "
        f"{metrics.timezone}, from {labels[0]} to {labels[-1]}"
    )
    if metrics.sends:
        names = ", ".join(send.name for send in metrics.sends)
        text += f"; {number(len(metrics.sends))} "
        text += f"{'send' if len(metrics.sends) == 1 else 'sends'} marked ({names})"
    return text + "."


# Up to this peak the activity chart's y axis lists every whole number.
# Vega draws about one tick per 40 pixels, so on a 0-to-1 or 0-to-2 domain
# it steps by halves, which the whole-number format shows as "1, 1" (and
# tickMinStep does not stop it); from 3 up its own ticks are whole, and up
# to 5 they are exactly these (#1327).
SMALL_PEAK = 5


def empty_scales(spec, metrics, markers):
    """Give an activity chart with no activity explicit scale domains.

    A scale's domain is otherwise the union of every layer's extent, and
    Vega warns "Infinite extent" for each empty one (#1327). The x axis spans
    the send markers, as their own extent drew it, or with none (or one) a
    day (by hour) or a week (by day) of campaign wall time up to ``as_of``.
    The y axis is 0 to 1, so its 0 baseline sits at the bottom, and labels
    only that 0 (``activity_chart``): a "1" would read as a real count.
    """
    zone = ZoneInfo(metrics.timezone)
    instants = [datetime.fromisoformat(row["time"]) for row in markers]
    if len(set(instants)) < 2:
        # A single instant is no span to draw: add the trailing window. The
        # axis is the campaign's wall clock, so the window is subtracted
        # there and is a whole day or week across a daylight-saving change.
        window = timedelta(days=1 if metrics.grain == "hour" else 7)
        end = datetime.fromisoformat(wall_time(metrics.as_of, zone))
        instants += [end - window, end]
    spec["encoding"]["x"]["scale"]["domain"] = [
        date_time(min(instants)),
        date_time(max(instants)),
    ]
    spec["layer"][0]["encoding"]["y"]["scale"] = {"domain": [0, 1]}


def date_time(instant):
    """A UTC ``instant`` as a Vega-Lite DateTime object, to the minute.

    Vega-Lite compiles a number in a temporal domain to a one-argument
    ``datetime(...)``, which Vega reads as a year (an invalid date, so an
    infinite extent again); a DateTime object compiles to a full ``utc(...)``.
    Dropping the seconds moves a domain end by under a minute, which no
    axis shows.
    """
    return {
        "year": instant.year,
        "month": instant.month,
        "date": instant.day,
        "hours": instant.hour,
        "minutes": instant.minute,
        "utc": True,
    }


def activity_chart(metrics, *, series=SERIES):
    """The response activity series: one line per series, with send markers.

    ``series`` selects the lines (a subset of ``SERIES``, in its order): the
    dashboard draws all three, the daily digest leaves out link follows
    (#1016), so its buckets are only those with a form open or submission.
    """
    if metrics.grain not in GRAIN_WORDS:
        raise ValueError("Activity is charted by hour or day.")
    if not series or any(line not in SERIES for line in series):
        raise ValueError("Activity is charted from the known series.")
    zone = ZoneInfo(metrics.timezone)
    activity = tuple(
        bucket
        for bucket in metrics.activity
        if any(getattr(bucket, field) for field, *_ in series)
    )
    labels = tuple(bucket_label(bucket, zone, metrics.grain) for bucket in activity)
    rows = [
        {
            "time": wall_time(bucket.start, zone),
            "label": label,
            "series": name,
            "families": getattr(bucket, field),
        }
        for bucket, label in zip(activity, labels, strict=True)
        for field, name, _color, _dash in series
    ]
    rows += [
        {
            "time": wall_time(slot, zone),
            "label": bucket_label(ActivityBucket(slot, 0, 0, 0), zone, metrics.grain),
            "series": name,
            "families": 0,
        }
        for slot in quiet_slots(activity, zone, metrics.grain)
        for _field, name, _color, _dash in series
    ]
    rows.sort(key=lambda row: row["time"])
    markers = [
        {
            "time": wall_time(send.scheduled, zone),
            "name": send.name,
            "label": format_instant(send.scheduled, zone, compact=True),
            "delivered": send.delivered,
        }
        for send in metrics.sends
    ]
    series_names = [name for _field, name, _color, _dash in series]
    summary = activity_summary(metrics, labels, series)
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
                            "range": [color for _f, _s, color, _d in series],
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
                            "range": [dash for _f, _s, _c, dash in series],
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
    if not rows:
        empty_scales(spec, metrics, markers)
    peak = max((row["families"] for row in rows), default=0)
    if peak <= SMALL_PEAK:
        y = spec["layer"][0]["encoding"]["y"]
        y["axis"]["values"] = list(range(peak + 1))
    # Vega leaves an empty layer's mark container in the accessibility tree
    # with a graphics role and no name, which assistive technology announces
    # as an unnamed image. A layer with no data shows nothing (the summary
    # and the table say so), so it is hidden from that tree.
    for layer in spec["layer"]:
        if not layer["data"]["values"]:
            layer["mark"]["aria"] = False
            # The line's point overlay is a mark of its own.
            if layer["mark"].get("point"):
                layer["mark"]["point"] = {"aria": False}
    table = ChartTable(
        ("Campaign time", *series_names),
        tuple(
            (label, *(number(getattr(bucket, field)) for field, *_ in series))
            for bucket, label in zip(activity, labels, strict=True)
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


# A bar's value label: the text mark's font size (Vega's default) and a
# generous width per character at that size, so the room kept after the
# longest bar is never too small for its label (#1266).
LABEL_FONT = 11
LABEL_CHAR = 6.6
LABEL_GAP = 4


def label_room(texts):
    """Pixels to keep after the longest bar for the widest value label."""
    return round(LABEL_GAP + LABEL_CHAR * max((len(text) for text in texts), default=0))


# Joins a progress bar's label and value in its axis label, which the axis
# splits into two lines; the y axis's description reads it as a pause.
TICK = " — "


def count_bars(key, title, bars, *, total, compared, noun="Families", progress=False):
    """A labelled horizontal bar per ``(label, count)`` pair, in the given order.

    The Financial stewardship summary's charts (#720) draw with this. Each
    bar is labelled after its end with its count and its share of ``total``
    ("4 (50%)", one decimal as every report percentage), so the chart shows
    every value without a table (#1266); its tooltip has the same count and
    share (headed ``compared``), as the funnel's bars do, and its accessible
    name says both. The summary sentence lists every bar. The table has the
    same three columns for a rendering that shows one; the page leaves it
    out (the chart engine's labelled-values rule).

    The value axis runs past the longest bar by exactly the room its label
    needs, whatever the chart's width (``label_room``, in pixels, read
    against Vega's ``width`` signal), so at phone width the label after a
    full-length bar still ends inside the chart instead of being cut off.

    With ``progress`` each bar is a progress bar against ``total`` instead
    (the Participation statistics, #720): the axis runs from zero to
    ``total`` and a grey track behind each bar shows the remainder. Each
    bar's "463 out of 1,240 (37.3%)" (the wording the statistics cards
    always used) is the second line of its axis label rather than text
    after the bar: beside a bar it would need more room than a phone-width
    plot has, while the axis column always fits it.
    """
    rows = []
    for index, (label, count) in enumerate(bars):
        part = Percentage(count, total)
        shown = percentage(part)
        row = {"label": label, "count": count, "order": index, "share": shown}
        if progress:
            row |= {"text": out_of(part), "total": total}
            # The axis label: the bar's label, then its value on a second
            # line (split at TICK by the axis's labelExpr).
            row["tick"] = f"{label}{TICK}{row['text']}"
            row["name"] = f"{label}: {row['text']}"
        else:
            # No matching Families: the share is a dash, so the label is
            # the count alone.
            row["text"] = (
                number(count) if shown == "—" else f"{number(count)} ({shown})"
            )
            # The bar's accessible name, in place of Vega's field-by-field
            # default ("Families: 3; label: Weekly; …").
            row["name"] = (
                f"{label}: {number(count)} {noun.lower()}, "
                f"{shown} {compared[0].lower()}{compared[1:]}"
            )
        rows.append(row)
    parts = ", ".join(f"{row['label']} {row['text']}" for row in rows)
    summary = f"{title}: {parts}." if rows else f"{title}: none."
    tooltip = [
        {"field": "label", "title": title},
        {"field": "count", "title": noun, "format": ","},
        {"field": "share", "title": compared},
    ]
    y = {
        "field": "label",
        "type": "nominal",
        "sort": {"field": "order"},
        "title": None,
        "axis": {"labelLimit": 300},
    }
    x = {
        "field": "count",
        "type": "quantitative",
        "title": noun,
        "axis": {"format": ",d", "tickMinStep": 1},
    }
    bar = {
        "mark": {"type": "bar", "color": BAR},
        "encoding": {"description": {"field": "name"}, "tooltip": tooltip},
    }
    if progress:
        x["scale"] = {"domain": [0, max(total, 1)], "nice": False}
        # At phone width the centred axis title sits under the middle and
        # end ticks' labels; Vega's default 4-pixel gap leaves Firefox's
        # taller text boxes overlapping them, so give the title more room.
        x["axis"] |= {"titlePadding": 8}
        # Every layer reads the same rows untransformed, so the y domain is
        # not a union and keeps the "order" sort (a filtered layer would
        # make Vega-Lite drop it and sort the labels alphabetically).
        y |= {
            "field": "tick",
            "axis": {"labelLimit": 300, "labelExpr": f"split(datum.label, '{TICK}')"},
        }
        layers = [
            {
                "mark": {"type": "bar", "color": TRACK, "aria": False},
                "encoding": {
                    "x": {"field": "total", "type": "quantitative"},
                    "tooltip": tooltip,
                },
            },
            bar,
        ]
    else:
        # The bars in the given order by name, as the funnel's: the text
        # layer's data differs from the bar layer's stacked data, so an
        # "order" sort conflicts and Vega-Lite warns on every draw.
        y["sort"] = [row["label"] for row in rows]
        largest = max((row["count"] for row in rows), default=0)
        room = label_room(row["text"] for row in rows)
        # The longest bar ends ``room`` pixels before the plot's right edge:
        # the domain grows by the share of the plot's width that the label
        # needs. No floor short of the whole room, so a long share-method
        # name on a phone still leaves the label its room; only a plot
        # narrower than the label itself (a 1-pixel floor) cannot fit it.
        x["scale"] = {
            "domainMin": 0,
            "domainMax": {
                "expr": f"{max(largest, 1)} * width / max(width - {room}, 1)"
            },
            "nice": False,
        }
        # The stretched domain rarely ends on a round tick, so a tick just
        # before the right edge would have its centred label cut off there;
        # labelBound hides such a label.
        x["axis"] |= {"labelBound": True}
        layers = [
            bar,
            # The value drawn after each bar repeats the bar's name, so
            # assistive technology hears it once, from the bar.
            {
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "middle",
                    "dx": LABEL_GAP,
                    "fontSize": LABEL_FONT,
                    "aria": False,
                },
                "encoding": {"text": {"field": "text"}},
            },
        ]
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "width": WIDTH,
        # A fixed height, BAR_STEP a bar (see BAR_STEP).
        "height": BAR_STEP * max(len(rows), 1),
        "autosize": {"type": "fit-x", "contains": "padding"},
        "data": {"values": rows},
        "encoding": {"y": y, "x": x},
        "layer": layers,
    }
    table = ChartTable(
        (title, noun, compared),
        tuple((row["label"], number(row["count"]), row["share"]) for row in rows),
    )
    return ChartDocument(key, title, summary, spec, table)


def financial_charts(summary, choices):
    """The Financial stewardship summary's two charts (#720).

    ``summary`` is the shaped summary of ``financial.shape_result``: its
    ``frequencies`` are ``(label, count)`` pairs in display order and its
    ``share_counts`` the share-method counts by option identity.
    ``choices`` is that result's ``share_choices``: the offered methods'
    ``(identity, label)`` pairs in their configured order. Every offered
    method gets a bar, even one nobody chose, so the chart shows what is
    offered; methods no longer offered are counted together, as the
    summary counts them. Shares are of the matching Families; a Family can
    choose more than one share method, so those shares can add up past
    100%.
    """
    families = summary["families"]
    compared = "Compared with matching Families"
    counts = summary["share_counts"]
    offered = dict(choices)
    shares = [(label, counts.get(key, 0)) for key, label in choices]
    retired = sum(count for key, count in counts.items() if key not in offered)
    if retired:
        shares.append(("Unavailable share method", retired))
    shares.append(("No share method chosen", summary["no_share"]))
    return (
        count_bars(
            "pledge-frequency",
            "Pledges by frequency",
            summary["frequencies"],
            total=families,
            compared=compared,
        ),
        count_bars(
            "share-methods",
            "Share methods chosen",
            shares,
            total=families,
            compared=compared,
        ),
    )


# The Additional information page's follow-up states (#720), in drawing
# order: (bar label, key of the selection's ``counts``). Every request is in
# exactly one, so the bars add up to the matching requests.
INFORMATION_STATES = (
    ("Completed", "completed"),
    ("Needed, not completed", "needed_open"),
    ("Not needed, not completed", "not_needed_open"),
)


def information_followup(counts, total):
    """The Additional information page's "Follow-up status" chart (#720).

    ``counts`` and ``total`` are the information selection's, over every
    request the filters match rather than the table's page: each follow-up
    state is a progress bar against ``total``. With no matching request
    there is nothing to draw, so ``None`` is returned.
    """
    if not total:
        return None
    return count_bars(
        "information-followup",
        "Follow-up status",
        [(label, counts[key]) for label, key in INFORMATION_STATES],
        total=total,
        compared="Share of matching requests",
        noun="Requests",
        progress=True,
    )


# The statistics drawn as progress against active Families (#720), in the
# order the cards listed them: (card label, PopulationStatistics field).
PROGRESS = (
    ("Families that have responded", "responses"),
    ("Families with eligible email", "eligible_email"),
    ("Families with deliverable email", "deliverable_email"),
)


def population_progress(active):
    """The Participation statistics' progress bars against active Families.

    ``active`` is ``CampaignStatistics.active``, the current active
    population's counts. Without an observation there is nothing to draw:
    ``None`` is returned and the cards say Unavailable, never zero.
    """
    if active is None:
        return None
    return count_bars(
        "active-population",
        "Responses and email reach",
        [(label, getattr(active, field)) for label, field in PROGRESS],
        total=active.families,
        compared="Share of active Families",
        progress=True,
    )


# Ministry requests' follow-up bars (#720), in drawing order: (bar label,
# count in ``ministry_followup.request_totals``).
FOLLOW_UP = (
    ("Resolved or closed", "completed"),
    ("Unresolved", "unresolved"),
)


def ministry_followup(totals):
    """Ministry requests' "Follow-up progress" chart (#720, #1133).

    ``totals`` are ``ministry_followup.request_totals`` of the applied queue
    view, whatever its Status and Outcome: each follow-up state is a
    progress bar against all those current requests, which the two states
    always add up to.

    The chart is always drawn, so the page keeps its height and By Ministry
    below it never moves (no layout shift): with no request, and for the
    Members no longer in ParishSoft view, which has no progress
    (``requests`` None), its bars read zero out of zero.
    """
    return count_bars(
        "ministry-followup",
        "Follow-up progress",
        [(label, totals.get(field) or 0) for label, field in FOLLOW_UP],
        total=totals["requests"] or 0,
        compared="Share of all requests",
        noun="Requests",
        progress=True,
    )


# The Participation page's daily chart (#720 slice 2c): each panel's plot
# height in CSS pixels, and the fixed width of every panel's value-axis
# column (tick labels and title), so the stacked plots line up on the shared
# date axis whatever their tick labels' widths ("$1.6M" against "12").
PANEL_HEIGHT = 120
AXIS_EXTENT = 64
# The participation drawing's colors (charts.py), so the page matches the
# exports and the daily email.
PANEL_COLORS = {"bars": ("#9ecae1", "#26658b"), "line": "#173d70", "money": "#8a4f00"}
# Short dollar ticks, as charts.short_dollars draws them ("$250K", "$1.6M"):
# d3's SI format writes "k" and "G", which read "K" and "B" here.
SHORT_DOLLARS = "replace(replace(format(datum.value, '$~s'), 'k', 'K'), 'G', 'B')"


def whole_dollars(amount):
    """An exact pledge total rounded to whole dollars, halves up: "$760,410"."""
    return f"${amount.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"


def participation_rows(document):
    """One row per campaign date: the plotted values and the tooltip's text.

    A value is ``None`` (a gap, never zero) where the day is unavailable; its
    tooltip text says Unavailable. Pledges are whole dollars in the tooltip,
    as the page's chart always showed them (#575); the table keeps cents.
    """
    rows = []
    for day in document.days:
        known = day.population_available
        pledged = document.financial_enabled and day.pledge_available
        row = {
            # A campaign date, not an instant: one band on the date axis,
            # so no time zone can shift it.
            "day": day.local_date.isoformat(),
            "label": format_date(day.local_date),
            # The date axis's label, in the parish's compact date style, as
            # the static drawing's ticks and the daily table write it.
            "tick": format_date(day.local_date, compact=True),
            "first": day.first_responses if known else None,
            "families": day.cumulative_responses if known else None,
            "pledges": float(day.pledge_total) if pledged else None,
            "first_text": number(day.first_responses) if known else "Unavailable",
            "participation": day.participation,
            "pledge_text": whole_dollars(day.pledge_total)
            if pledged
            else "Unavailable",
        }
        # vega-tooltip shows ``title`` as the tooltip's heading and every
        # other key as a row, in order, escaping each (as Campaign
        # comparison's day-by-day tooltips do).
        row["tip"] = {
            "title": row["label"],
            "First submissions": row["first_text"],
            "Families that have responded": row["participation"],
        }
        if document.financial_enabled:
            row["tip"]["Effective annual pledges"] = row["pledge_text"]
        rows.append(row)
    return rows


def round_ticks(top):
    """A panel's value ticks from zero: whole steps of 1, 2 or 5 times a power
    of ten, the smallest giving at most five ticks up to ``top``.

    Vega's own tick choice can land between whole numbers on a small axis
    (0 to 1.1), which a whole-number label would show as "1" twice.
    """
    power = 10 ** max(0, math.floor(math.log10(max(top, 1))) - 1)
    step = next(
        factor * scale
        for scale in (power, power * 10, power * 100)
        for factor in (1, 2, 5)
        if top // (factor * scale) <= 4
    )
    return [step * index for index in range(int(top // step) + 1)]


def participation_panel(title, field, unit, values, mark, *, tooltip, x, axis=None):
    """One panel of the daily chart: a hover column per date under one mark.

    The panel's value axis starts at zero and tops out at its own largest
    value plus 10% (``charts.axis_top``), as every participation drawing's
    panels do; ``axis`` overrides its axis properties (``None`` removes
    one), and its ticks step by round 1, 2 or 5 multiples (``round_ticks``).
    An invisible full-height bar per date carries the tooltip, so a pointer
    anywhere in a date's column, not only on its small mark, shows that
    date's figures. A line's points sit in the middle of their date's
    column, and an unavailable day breaks the line (a gap, never a joined or
    zero point).

    The marks are hidden from assistive technology: Vega would name each
    one from its raw fields (an ISO date, an unrounded dollar float), which
    differs from the page's daily table. The view's name and summary, and
    that table, are the chart's accessible path (reports spec, "Chart
    engine").
    """
    top = axis_top([value for value in values if value is not None])
    settings = {
        "values": round_ticks(top),
        "format": ",d",
        "minExtent": AXIS_EXTENT,
        "maxExtent": AXIS_EXTENT,
        # Its range and ticks would be read as raw numbers ("0 to 3.3").
        "aria": False,
    } | (axis or {})
    y = {
        "field": field,
        "type": "quantitative",
        "title": unit,
        "scale": {"domain": [0, top], "nice": False},
        "axis": {key: value for key, value in settings.items() if value is not None},
    }
    return {
        "title": {"text": title, "anchor": "start", "fontSize": 12},
        "width": WIDTH,
        "height": PANEL_HEIGHT,
        "encoding": {"x": x},
        "layer": [
            {"mark": mark | {"aria": False}, "encoding": {"y": y}},
            # On top, so the pointer always reaches a whole date's column:
            # a line's path would otherwise catch it, and Vega gives a path
            # one tooltip, its first date's.
            {
                "mark": {"type": "bar", "opacity": 0, "aria": False},
                "encoding": {
                    "y": {"value": 0},
                    "y2": {"value": PANEL_HEIGHT},
                    "tooltip": tooltip,
                },
            },
        ],
    }


def participation_chart(document):
    """The Participation page's daily chart, drawn by the chart engine (#720).

    It is the same design as the static drawing the exports and the daily
    email keep (``charts._draw``): stacked panels on one shared date axis,
    first submissions as bars, cumulative participating Families as a line
    and, with financial, effective annual pledges as a dashed line with short
    dollar ticks. Each panel is named by its own title, so there is no legend,
    and there is never a second y-axis. Dates are labelled once, under the
    last panel. The page's daily table is the chart's exact values, so the
    page shows no table of its own here (``table`` is kept for completeness).
    """
    rows = participation_rows(document)
    tooltip = {"field": "tip"}
    # Each campaign date is one band, named by its compact parish-style
    # label (unique, since it carries the year) and in date order (an
    # explicit domain, whatever the style's text order): a bar fills most of
    # it, a line's point sits in its middle and its hover column fills all
    # of it. Only the last panel labels it.
    x = {
        "field": "tick",
        "type": "ordinal",
        "scale": {
            "domain": [row["tick"] for row in rows],
            "paddingInner": 0,
            "paddingOuter": 0,
        },
        "title": None,
        "axis": {"labels": False, "ticks": False, "domain": False, "aria": False},
    }
    fill, stroke = PANEL_COLORS["bars"]
    # (title, field, unit, mark, axis overrides), top to bottom.
    shown = [
        (
            "First submissions that day",
            "first",
            "Families",
            {
                "type": "bar",
                "color": fill,
                "stroke": stroke,
                "strokeWidth": 0.5,
                "width": {"band": 0.8},
            },
            None,
        ),
        (
            "Cumulative participating Families",
            "families",
            "Families",
            {
                "type": "line",
                "color": PANEL_COLORS["line"],
                "invalid": "break-paths-show-domains",
                "point": {
                    "filled": True,
                    "size": 12,
                    "color": PANEL_COLORS["line"],
                    "aria": False,
                },
            },
            None,
        ),
    ]
    if document.financial_enabled:
        shown.append(
            (
                "Effective annual pledges",
                "pledges",
                "USD",
                {
                    "type": "line",
                    "color": PANEL_COLORS["money"],
                    "invalid": "break-paths-show-domains",
                    "strokeDash": [6, 3],
                    "point": {
                        "filled": True,
                        "shape": "square",
                        "size": 12,
                        "color": PANEL_COLORS["money"],
                        "aria": False,
                    },
                },
                {"format": None, "labelExpr": SHORT_DOLLARS},
            )
        )
    # Only the last panel labels the dates, in the parish's compact date
    # style, as many as fit without overlapping and none past the plot's
    # ends. The title names the campaign's time zone, as the static page
    # drawing did, on its own line so it fits a phone-width plot. The axis
    # is hidden from assistive technology, which would otherwise read every
    # date of the campaign; the daily table lists them. Vega's default
    # 4-pixel title gap leaves Firefox's taller text boxes overlapping the
    # end date labels at phone width, so the title gets more room, as the
    # progress chart's does.
    labelled = x | {
        "title": ["Campaign date", f"({document.campaign_timezone})"],
        "axis": {
            "aria": False,
            "ticks": False,
            "labelAngle": 0,
            "labelOverlap": True,
            "labelBound": True,
            "titlePadding": 8,
        },
    }
    panels = [
        participation_panel(
            title,
            field,
            unit,
            [row[field] for row in rows],
            mark,
            tooltip=tooltip,
            x=labelled if index == len(shown) - 1 else x,
            axis=axis,
        )
        for index, (title, field, unit, mark, axis) in enumerate(shown)
    ]
    summary = participation_summary(document)
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "data": {"values": rows},
        # The panels share one date scale, and the whole figure (the axis
        # columns included) fits the panel's width (chart-v1.js gives each
        # panel the container's width).
        "autosize": {"type": "fit-x", "contains": "padding"},
        "resolve": {"scale": {"x": "shared"}},
        "spacing": 12,
        "vconcat": panels,
    }
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    cells = [(row["label"], row["first_text"], row["participation"]) for row in rows]
    if document.financial_enabled:
        headings.append("Effective annual pledges")
        cells = [
            (*cell, row["pledge_text"]) for cell, row in zip(cells, rows, strict=True)
        ]
    title = f"Daily participation — {document.scope_label}"
    return ChartDocument(
        "daily-participation",
        title,
        summary,
        spec,
        ChartTable(tuple(headings), tuple(cells)),
    )


def participation_summary(document):
    """One sentence for the daily chart: its date range and the last day's figures.

    It is the view's accessible description; the page does not show it, since
    the daily table under the chart has every date's figures (#720).
    """
    days = document.days
    if not days:
        return f"Daily participation, {document.scope_label}: no campaign dates yet."
    last = days[-1]
    text = (
        f"Daily participation, {document.scope_label}, from "
        f"{format_date(days[0].local_date)} to {format_date(last.local_date)}"
    )
    if not last.population_available:
        return text + "; the last date's figures are unavailable."
    text += (
        f"; by the last date {last.participation} Families had responded, "
        f"{number(last.first_responses)} of them for the first time that day"
    )
    if document.financial_enabled and last.pledge_available:
        text += f", with {whole_dollars(last.pledge_total)} in effective annual pledges"
    return text + "."
