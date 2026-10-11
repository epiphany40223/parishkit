"""Charts of the Campaign comparison report (#1018, slice 5).

The campaign-history spec ("Page layout") asks for three groups of charts,
each through the chart engine (reports spec, "Chart engine"): a
``chart_specs.ChartDocument`` whose Vega-Lite spec the browser draws, with
the summary and exact-values table that stand in for the picture.

- **Day by day:** cumulative Families responded, percent of active Families
  and (with Financial stewardship) cumulative annual pledges, by campaign
  day. The current campaign is the accent color, thick and solid; last year
  the second (gold) color, dashed; every other imported year thin and grey,
  its year labelled at the line's end; the prior-years average dark and
  dash-dotted. A vertical rule marks the comparison day.
- **Final results by year:** Families responded and annual pledges, one
  horizontal bar per year, the current campaign lighter and labelled
  "so far".
- **Pledge distribution:** 100% stacked bars per year of the share of
  pledging Families, and of the dollars pledged, in each pledge band.

Everything here is a pure function of ``comparison.Comparison``: nothing
reads the database or the clock. These charts appear only on the Admin
portal page, so they show the current campaign's distribution (spec,
"The current campaign stays inside the portal"); the downloads and digest
(slices 6 and 7) apply the small-count protection to what they draw.

**Phone widths.** The published infographic showed two failures at phone
width: labels drawn over each other and a page that scrolled sideways. So
the y scale of a day-by-day chart has a fixed domain and a fixed height,
which lets the end labels be spaced in pixels here (``spread``); bars are
horizontal, with each value inside a long bar's end or after a short one;
the "Day N" label sits on the side of its rule away from the end labels;
and a legend's column count follows the panel's width
(``legend_columns``). A browser test checks both widths for overlapping
or cut-off text.
"""

import math
from dataclasses import dataclass
from decimal import Decimal, localcontext

from parishkit.stewardship.web.dates import format_date
from parishkit.stewardship.web.design_tokens import COLORS
from parishkit.stewardship.web.presentation import number

from .chart_assets import VEGA_LITE_SCHEMA
from .chart_specs import WIDTH, ChartDocument, ChartTable
from .comparison import band_label, share_text, value_text, years_label

# The day-by-day charts' plot height in CSS pixels. Fixed, so the end
# labels can be spaced in pixels (``spread``); only the width follows the
# panel.
HEIGHT = 260
# The least vertical distance between two end labels' centers, a little
# more than one line of the 11-pixel label font.
LABEL_GAP = 13
LABEL_FONT = 11
# The "Day N" label hangs from the plot's top (2 pixels down, one line of
# the label font); end labels are centered at least half a gap below it.
DAY_LABEL_FLOOR = 2 + LABEL_FONT + 2 + LABEL_GAP / 2
# The average line's name in the legend and the table. The years it
# averages vary by day (a partial year leaves it after its last day), so
# the name names none; the summary names the years of the comparison day.
AVERAGE_NAME = "Prior-years average"
# Each line's role: its color, width and dash pattern. The dash patterns
# tell the lines apart without color (spec, "Chart engine").
CURRENT, LAST, AVERAGE, OTHER = "current", "last", "average", "other"
STYLES = {
    CURRENT: (COLORS["accent"], 3, [1, 0]),
    LAST: (COLORS["gold"], 2, [6, 3]),
    AVERAGE: (COLORS["ink"], 2, [8, 3, 2, 3]),
    OTHER: (COLORS["control-border"], 1, [1, 0]),
}
# The comparison day's rule and every chart's own text.
RULE = COLORS["muted"]
# The pledge bands, lightest (smallest pledges) to darkest: lightness orders
# them as well as hue, and white edges separate neighbouring segments.
BAND_COLORS = (
    "#bfe0d9",
    "#a1d1c7",
    "#7ebfb2",
    "#55a898",
    "#33907f",
    "#1d7667",
    "#115e56",
    "#0a3f3a",
)
# Theme tokens for the chart's own text, so axes and legends read with the
# page's contrast rather than Vega's light grey defaults.
CONFIG = {
    "axis": {
        "labelColor": COLORS["muted"],
        "titleColor": COLORS["ink"],
        "labelFontSize": LABEL_FONT,
        "titleFontSize": 12,
    },
    "legend": {
        "labelColor": COLORS["ink"],
        "titleColor": COLORS["ink"],
        "labelFontSize": LABEL_FONT,
        "titleFontSize": 12,
    },
    "title": {"color": COLORS["ink"]},
    "view": {"stroke": None},
}
NOMINAL_DOLLARS = "Amounts are nominal dollars, with no adjustment for inflation."
# The width follows the panel (chart-v1.js sets "container"), and the view
# is measured again after every update: a legend's column count follows the
# plot's width (``legend_columns``), and without ``resize`` Vega would keep
# the height it measured first, cutting off a legend that gained rows.
AUTOSIZE = {"type": "fit-x", "contains": "padding", "resize": True}


@dataclass(frozen=True)
class Measure:
    """One day-by-day chart: what it plots and how its numbers read."""

    key: str
    title: str
    axis: str
    kind: str  # "count", "percent" or "money", as ``comparison.value_text``
    financial: bool = False

    def value(self, day):
        """This measure of one Day as a Decimal or int, or None."""
        if self.key == "families":
            return day.families
        if self.key == "percent":
            return day.percent.value
        return day.pledged


MEASURES = (
    Measure(
        "families",
        "Families responded by campaign day",
        "Families responded (cumulative)",
        "count",
    ),
    Measure(
        "percent",
        "Percent of active Families by campaign day",
        "Percent of active Families",
        "percent",
    ),
    Measure(
        "pledged",
        "Annual pledges by campaign day",
        "Annual pledges (US dollars, cumulative)",
        "money",
        financial=True,
    ),
)
# The day axis's title. The spec's explanation, "day 1 = first invitation
# day", is longer than a phone's plot is wide (Vega does not wrap an axis
# title, and one wider than its view is cut off), so it is in each chart's
# summary and in the help line above the charts instead.
DAY_AXIS = "Campaign day"


# The pixels a legend gives up to the left of its first column: it lines
# up with the plot, after the y axis's labels and title.
LEGEND_INSET = 90


def legend_columns(width):
    """A legend's column count as a Vega expression of the chart's width.

    A legend does not wrap by itself: a fixed count that fits a laptop runs
    off a phone's chart, and one that fits a phone wastes a laptop's. Each
    column needs about ``width`` pixels (symbol, label and padding), so the
    count follows the space the panel gives the chart, from one to four.

    It reads the container's width, not the plot's: the plot's width is
    what is left after the legend and axes, so a count computed from it
    would change the layout it was computed from, and Vega settles that
    only once, leaving a legend that gained rows cut off. The server
    renderer has no container, so it takes the email image's width. The
    ``width`` term only makes the count follow a resize.
    """
    available = (
        f"(isFinite(containerSize()[0]) && containerSize()[0] > 0 "
        f"? containerSize()[0] : {WIDTH})"
    )
    return {
        "expr": f"max(1, min(4, floor(({available} - {LEGEND_INSET}) / {width})))"
        " + 0 * width"
    }


def short_label(year):
    """A year's name in a legend, an axis or a table heading: "2026"."""
    if year.current:
        return f"{year.year or year.name} (so far)"
    return str(year.year) if year.final else f"{year.year} (partial)"


def plotted(measure, value):
    """A measure's value as the chart's float (a percentage as a fraction).

    Rounded well past what a pixel can show, so the embedded JSON stays
    short; the tooltips and tables carry the exact text.
    """
    if value is None:
        return None  # A gap: Vega breaks the line there.
    if measure.kind == "percent":
        return round(float(value) / 100, 6)
    return round(float(value), 4)


def nice_top(largest):
    """A round top for an axis that starts at zero, at least ``largest``.

    The step is 1, 2, 2.5 or 5 times a power of ten, the smallest that
    reaches ``largest`` in at most five steps; zero still gets a top of one
    step, so the axis is drawable.
    """
    if largest <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(largest / 5))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * power
        if largest / step <= 5:
            return math.ceil(round(largest / step, 9)) * step
    return largest  # pragma: no cover - the loop always returns


def spread(positions, height=HEIGHT, gap=LABEL_GAP, floor=None):
    """Pixel positions moved apart so no two are closer than ``gap``.

    ``positions`` are label centers measured down from the plot's top. Each
    label keeps its order; a label too close to the one above it moves
    down, and when the last one then falls below the plot, the stack moves
    up again, staying inside ``[floor, height]`` while it fits there.
    ``floor`` defaults to half a gap (a label just inside the top); a chart
    with a "Day N" label passes ``DAY_LABEL_FLOOR``, so no end label meets it.
    Returns the new positions in the input's order.
    """
    order = sorted(range(len(positions)), key=lambda index: positions[index])
    placed = [positions[index] for index in order]
    for index in range(1, len(placed)):
        placed[index] = max(placed[index], placed[index - 1] + gap)
    if placed and placed[-1] > height:
        placed[-1] = height
        for index in range(len(placed) - 2, -1, -1):
            placed[index] = min(placed[index], placed[index + 1] - gap)
    floor = gap / 2 if floor is None else floor
    if placed and placed[0] < floor:
        placed[0] = floor
        for index in range(1, len(placed)):
            placed[index] = max(placed[index], placed[index - 1] + gap)
    result = [0.0] * len(positions)
    for index, value in zip(order, placed, strict=True):
        result[index] = value
    return result


# ---------------------------------------------------------- day by day


@dataclass(frozen=True)
class Line:
    """One drawn line: its role, names and points (day, value, carried)."""

    role: str
    key: str
    legend: str
    name: str
    points: tuple
    dates: dict


def year_points(measure, year, last_day):
    """A year's (day, value, carried) points for one measure.

    A day with no value between two that have one is (day, None, False).
    A final imported year whose daily rows end before ``last_day`` is
    carried flat to it, since its final figures stand from then on (spec,
    "Alignment by campaign day"); a partial year and the current campaign
    end at their last known day.
    """
    known = {
        day.day: value
        for day in year.series
        if (value := measure.value(day)) is not None
    }
    if not known:
        return []
    # A day without a figure between two that have one (the current
    # campaign's day whose population was unavailable) is a null point, so
    # the line breaks there rather than drawing a straight line across it,
    # and the table shows "—" for it.
    points = [(day, known.get(day), False) for day in range(min(known), max(known) + 1)]
    if not year.current and year.final and points[-1][0] < last_day:
        points.append((last_day, points[-1][1], True))
    return points


def average_points(measure, imported, last_day):
    """The prior-years average for each day 1 to ``last_day`` that has one.

    It is the mean over every imported year with a value that day, a final
    year's last value counting after its last day, as the tiles' averages
    count (``comparison.average``).
    """
    known = []
    for year in imported:
        values = {
            day.day: value
            for day in year.series
            if (value := measure.value(day)) is not None
        }
        known.append((year, values, max(values) if values else 0))
    points = []
    for day in range(1, last_day + 1):
        values = []
        for year, found, end in known:
            if day in found:
                values.append(Decimal(found[day]))
            elif year.final and found and day > end:
                values.append(Decimal(found[end]))
        if values:
            with localcontext(prec=40):
                points.append((day, sum(values) / len(values), False))
    return points


def covered(measure, imported, day):
    """The imported years with a value on ``day`` (a final year's carried)."""
    found = []
    for year in imported:
        days = [d.day for d in year.series if measure.value(d) is not None]
        if days and (day in days or (year.final and day > max(days))):
            found.append(year)
    return found


def lines(measure, comparison):
    """The chart's lines, drawn bottom to top: others, average, last, current."""
    years = ([comparison.current] if comparison.current else []) + list(
        comparison.imported
    )
    last_day = max((day.day for year in years for day in year.series), default=0)
    others, highlighted = [], []
    for year in comparison.imported:
        if year is comparison.last_year:
            continue
        others.append(
            Line(
                OTHER,
                f"year-{year.year}",
                "Other years",
                short_label(year),
                tuple(year_points(measure, year, last_day)),
                {day.day: day.local_date for day in year.series},
            )
        )
    averaged = average_points(measure, comparison.imported, last_day)
    if averaged:
        highlighted.append(
            Line(AVERAGE, "average", AVERAGE_NAME, AVERAGE_NAME, tuple(averaged), {})
        )
    if comparison.last_year is not None:
        year = comparison.last_year
        highlighted.append(
            Line(
                LAST,
                f"year-{year.year}",
                f"{year.year} (last year)",
                short_label(year),
                tuple(year_points(measure, year, last_day)),
                {day.day: day.local_date for day in year.series},
            )
        )
    if comparison.current is not None and comparison.current.series:
        year = comparison.current
        highlighted.append(
            Line(
                CURRENT,
                "current",
                short_label(year),
                year.label,
                tuple(year_points(measure, year, last_day)),
                {day.day: day.local_date for day in year.series},
            )
        )
    drawn = [line for line in others + highlighted if line.points]
    return drawn, last_day


def point_text(measure, value, carried):
    """A point's value as the tooltip and table show it."""
    text = value_text(measure.kind, value)
    return text + " (final)" if carried else text


def line_rows(measure, line):
    """One line's data rows for the spec (floats for Vega, text for people)."""
    return [
        {
            "day": day,
            "value": plotted(measure, value),
            "role": line.role,
            "line": line.key,
            "series": line.legend,
            "name": line.name,
            "date": "—"
            if carried or day not in line.dates
            else format_date(line.dates[day], compact=True),
            "shown": point_text(measure, value, carried),
        }
        for day, value, carried in line.points
    ]


def legend_scale(drawn, index):
    """One of the series scales (color, width or dash), legend order."""
    seen = {}
    for line in reversed(drawn):  # current first, others last
        seen.setdefault(line.legend, STYLES[line.role][index])
    return {"domain": list(seen), "range": list(seen.values())}


def end_labels(measure, drawn, top, *, day_label=False):
    """The grey years' labels at their lines' ends, spaced so none overlap.

    With a "Day N" label (``day_label``) they stay below it as well: a
    partial year's label sits inside the plot, where the rule may be.
    """
    ends = [line for line in drawn if line.role == OTHER]
    wanted = [
        HEIGHT * (1 - plotted(measure, line.points[-1][1]) / top) for line in ends
    ]
    placed = spread(wanted, floor=DAY_LABEL_FLOOR if day_label else None)
    return [
        {
            "day": line.points[-1][0],
            "value": top * (1 - position / HEIGHT),
            # The year alone ("2024", not "2024 (partial)"): a short label
            # needs the least room beside the plot.
            "text": line.key.removeprefix("year-"),
        }
        for line, position in zip(ends, placed, strict=True)
    ]


def value_on(line, day):
    """A line's (value, carried) on ``day``, or None when it has none.

    A final year carried flat has only two points past its last row (that
    row and the chart's last day), so a day between them takes the carried
    value too.
    """
    for point_day, value, carried in line.points:
        if point_day == day:
            return value, carried
    carried = len(line.points) > 1 and line.points[-1][2]
    if carried and line.points[-2][0] < day < line.points[-1][0]:
        return line.points[-1][1], True
    return None


def day_summary(measure, comparison, drawn):
    """The sentence that stands in for a day-by-day chart."""
    day = comparison.day
    text = f"{measure.title}, day 1 being each campaign's first invitation day"
    if day is None:
        return text + ". This campaign has no complete day yet."
    parts = []
    for line in reversed(drawn):  # current first, as the legend
        found = value_on(line, day) if line.role != OTHER else None
        if found is None or found[0] is None:
            continue
        name = line.legend
        if line.role == AVERAGE:
            # Named like the tiles: the years with a value on this day.
            name = f"{years_label(covered(measure, comparison.imported, day))} average"
        parts.append(f"{name} {point_text(measure, *found)}")
    if not parts:
        return text + "."
    return f"{text}. On day {number(day)}: " + "; ".join(parts) + "."


def day_table(measure, drawn, last_day):
    """The exact values: one row per day, one column per line."""
    ordered = list(reversed(drawn))  # current first, as the legend
    rows = []
    for day in range(1, last_day + 1):
        cells = [number(day)]
        for line in ordered:
            found = value_on(line, day)
            cells.append("—" if found is None else point_text(measure, *found))
        rows.append(tuple(cells))
    headings = (
        "Day",
        *(line.name if line.role == OTHER else line.legend for line in ordered),
    )
    return ChartTable(headings, tuple(rows))


def day_chart(measure, comparison):
    """One day-by-day line chart, or None when no year has a value."""
    drawn, last_day = lines(measure, comparison)
    if not drawn:
        return None
    # Every point once, at the top level: each line layer filters its role,
    # and the tooltip points take them all.
    rows = [row for line in drawn for row in line_rows(measure, line)]
    largest = max(row["value"] for row in rows if row["value"] is not None)
    top = nice_top(largest)
    y = {
        "field": "value",
        "type": "quantitative",
        "title": measure.axis,
        "scale": {"domain": [0, top], "nice": False, "zero": True},
        "axis": {
            "format": {"count": ",d", "percent": ".0%", "money": "$,.0f"}[measure.kind],
            "tickCount": 5,
            "labelOverlap": True,
        },
    }
    series = {
        "color": ("color", 0),
        "strokeWidth": ("width", 1),
        "strokeDash": ("dash", 2),
    }
    encoding_series = {
        channel: {
            "field": "series",
            "type": "nominal",
            "title": None,
            "scale": legend_scale(drawn, index),
            **({} if channel == "color" else {"legend": None}),
        }
        for channel, (_name, index) in series.items()
    }
    # A nominal width would get a point scale, which spreads its range
    # between the first and last value (and has none for one line); each
    # role's own width needs an ordinal scale.
    encoding_series["strokeWidth"]["scale"]["type"] = "ordinal"
    encoding_series["color"]["legend"] = {
        "orient": "top",
        "columns": legend_columns(150),
        "symbolType": "stroke",
        "symbolStrokeWidth": 3,
        "labelLimit": 160,
    }
    layers = []
    # The grey lines first, so every highlighted line is drawn over them.
    for role in (OTHER, AVERAGE, LAST, CURRENT):
        if not any(line.role == role for line in drawn):
            continue
        layers.append(
            {
                "transform": [{"filter": {"field": "role", "equal": role}}],
                # A null value breaks the line instead of being skipped.
                "mark": {
                    "type": "line",
                    "interpolate": "linear",
                    "invalid": "break-paths-filter-domains",
                },
                "encoding": {
                    "x": {"field": "day", "type": "quantitative"},
                    "y": y,
                    "detail": {"field": "line", "type": "nominal"},
                    **encoding_series,
                },
            }
        )
    labels = end_labels(measure, drawn, top, day_label=comparison.day is not None)
    if labels:
        layers.append(
            {
                "data": {"values": labels},
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "middle",
                    "dx": 4,
                    "fontSize": LABEL_FONT,
                    "color": COLORS["muted"],
                },
                "encoding": {
                    "x": {"field": "day", "type": "quantitative"},
                    "y": {"field": "value", "type": "quantitative"},
                    "text": {"field": "text"},
                },
            }
        )
    # Invisible points carry the tooltips: a line is one shape, so pointing
    # at it could name only one of its days. They stay out of the
    # accessibility tree; the table holds every value.
    layers.append(
        {
            "mark": {
                "type": "point",
                "filled": True,
                "size": 70,
                "opacity": 0,
                "aria": False,
            },
            "encoding": {
                "x": {"field": "day", "type": "quantitative"},
                "y": {"field": "value", "type": "quantitative"},
                "tooltip": [
                    {"field": "name", "title": "Year"},
                    {"field": "day", "title": "Day", "format": ",d"},
                    {"field": "date", "title": "Date"},
                    {"field": "shown", "title": measure.axis.split(" (")[0]},
                ],
            },
        }
    )
    day = comparison.day
    if day is not None:
        marker = [{"day": day, "text": f"Day {number(day)}"}]
        # The label sits on the side of the rule away from the end labels
        # (right of the last day), so the two never meet.
        left = day <= max(last_day, 2) / 2
        layers += [
            {
                "data": {"values": marker},
                "mark": {"type": "rule", "color": RULE, "strokeDash": [4, 4]},
                "encoding": {"x": {"field": "day", "type": "quantitative"}},
            },
            {
                "data": {"values": marker},
                "mark": {
                    "type": "text",
                    "align": "left" if left else "right",
                    "dx": 4 if left else -4,
                    "baseline": "top",
                    "dy": 2,
                    "fontSize": LABEL_FONT,
                    "color": RULE,
                },
                "encoding": {
                    "x": {"field": "day", "type": "quantitative"},
                    "y": {"value": 0},
                    "text": {"field": "text"},
                },
            },
        ]
    summary = day_summary(measure, comparison, drawn)
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "config": CONFIG,
        "autosize": AUTOSIZE,
        "width": WIDTH,
        "height": HEIGHT,
        "data": {"values": rows},
        "encoding": {
            "x": {
                "field": "day",
                "type": "quantitative",
                "title": DAY_AXIS,
                "scale": {"domain": [1, max(last_day, 2)], "nice": False},
                "axis": {"format": ",d", "tickMinStep": 1, "labelOverlap": True},
            }
        },
        "layer": layers,
    }
    return ChartDocument(
        f"comparison-{measure.key}",
        measure.title,
        summary,
        spec,
        day_table(measure, drawn, last_day),
        (NOMINAL_DOLLARS,) if measure.kind == "money" else (),
    )


def day_charts(comparison):
    """The day-by-day charts the comparison has figures for."""
    charts = [
        day_chart(measure, comparison)
        for measure in MEASURES
        if comparison.financial or not measure.financial
    ]
    return [chart for chart in charts if chart is not None]


# -------------------------------------------------------- final results


FINALS = (
    ("families", "Families responded, final and so far", "Families responded", "count"),
    (
        "pledged",
        "Annual pledges, final and so far",
        "Annual pledges (US dollars)",
        "money",
    ),
)


# A bar at least this share of its axis carries its value inside its end;
# a shorter one, just after it. Either way the label has more room than
# "$1,300,000.00" needs on a 320-pixel phone (about 120 pixels of plot), so
# no label runs past the plot or over another.
INSIDE = 0.55


def final_chart(key, title, axis, kind, comparison):
    """One final-results bar chart, or None when no year has the figure."""
    years = sorted(comparison.imported, key=lambda year: year.year)
    if comparison.current is not None:
        years.append(comparison.current)
    found = [
        (year, year.end, getattr(year.end, key))
        for year in years
        if year.end is not None and getattr(year.end, key) is not None
    ]
    if not found:
        return None
    top = nice_top(max(float(value) for _, _, value in found))
    rows = []
    for year, end, value in found:
        inside = float(value) >= INSIDE * top
        rows.append(
            {
                "name": short_label(year),
                "value": float(value),
                "shown": value_text(kind, value),
                "when": f"So far (day {number(end.day)})"
                if year.current
                else ("Final" if year.final else f"Partial (day {number(end.day)})"),
                # The current campaign is drawn lighter; a label inside a
                # dark bar is white, anywhere else the ink color.
                "opacity": 0.35 if year.current else 1,
                "inside": inside,
                "ink": "#ffffff" if inside and not year.current else COLORS["ink"],
            }
        )
    names = [row["name"] for row in rows]
    summary = (
        f"{title}: " + "; ".join(f"{row['name']} {row['shown']}" for row in rows) + "."
    )
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "config": CONFIG,
        "autosize": AUTOSIZE,
        "width": WIDTH,
        "height": {"step": 26},
        "data": {"values": rows},
        "encoding": {
            "y": {
                "field": "name",
                "type": "nominal",
                "sort": names,
                "title": None,
                "axis": {"labelLimit": 120},
            },
            "x": {
                "field": "value",
                "type": "quantitative",
                "title": axis,
                "scale": {"domain": [0, top], "nice": False},
                "axis": {
                    "format": ",d" if kind == "count" else "$,.0f",
                    "tickCount": 4,
                    "labelOverlap": True,
                },
            },
        },
        "layer": [
            {
                # The current campaign is drawn lighter, with an outline so
                # it still reads as a bar; its name says "so far" as well.
                "mark": {
                    "type": "bar",
                    "color": COLORS["accent"],
                    "stroke": COLORS["accent"],
                    "strokeWidth": 1.5,
                },
                "encoding": {
                    "fillOpacity": {
                        "field": "opacity",
                        "type": "quantitative",
                        "scale": None,
                    },
                    "tooltip": [
                        {"field": "name", "title": "Year"},
                        {"field": "when", "title": "Figures"},
                        {"field": "shown", "title": axis.split(" (")[0]},
                    ],
                },
            },
            *(
                {
                    "transform": [{"filter": {"field": "inside", "equal": inside}}],
                    "mark": {
                        "type": "text",
                        "align": "right" if inside else "left",
                        "baseline": "middle",
                        "dx": -4 if inside else 4,
                        "fontSize": LABEL_FONT,
                    },
                    "encoding": {
                        "text": {"field": "shown"},
                        "color": {"field": "ink", "type": "nominal", "scale": None},
                    },
                }
                for inside in (True, False)
                if any(row["inside"] is inside for row in rows)
            ),
        ],
    }
    table = ChartTable(
        ("Year", "Figures", axis.split(" (")[0]),
        tuple((row["name"], row["when"], row["shown"]) for row in rows),
    )
    notes = (NOMINAL_DOLLARS,) if kind == "money" else ()
    return ChartDocument(f"comparison-final-{key}", title, summary, spec, table, notes)


def final_charts(comparison):
    """The final-results bar charts the comparison has figures for."""
    charts = [
        final_chart(*entry, comparison)
        for entry in FINALS
        if comparison.financial or entry[0] != "pledged"
    ]
    return [chart for chart in charts if chart is not None]


# ---------------------------------------------------- pledge distribution


def short_band_label(band, edges):
    """A band's legend label, short enough for two legend columns."""

    def whole(value):
        """A whole-dollar edge with US grouping, without the dollar sign."""
        return number(int(value))

    if band == 1:
        return f"Under ${whole(edges[0])}"
    if band > len(edges):
        return f"${whole(edges[-1])} or more"
    return f"${whole(edges[band - 2])}–{whole(edges[band - 1])}"


DISTRIBUTIONS = (
    (
        "families",
        "Share of pledging Families by pledge band",
        "Share of pledging Families",
    ),
    ("dollars", "Share of dollars pledged by pledge band", "Share of dollars pledged"),
)


def distribution_chart(key, title, axis, comparison, edges):
    """One 100% stacked bar chart of the pledge bands, or None."""
    years = sorted(comparison.imported, key=lambda year: year.year)
    if comparison.current is not None:
        years.append(comparison.current)
    shown = [
        year
        for year in years
        if year.distribution is not None
        and year.distribution.reason is None
        and year.distribution.pledging
        and year.distribution.bands
    ]
    if not shown:
        return None
    labels = [short_band_label(band, edges) for band in range(1, len(edges) + 2)]
    rows, table = [], []
    for year in shown:
        found = year.distribution
        name = short_label(year)
        cells = [name]
        by_band = {band.band: band for band in found.bands}
        for band in range(1, len(edges) + 2):
            entry = by_band.get(band)
            families = entry.families if entry else 0
            total = entry.total if entry else Decimal(0)
            part, whole = (
                (families, found.pledging)
                if key == "families"
                else (total, found.total)
            )
            text = share_text(part, whole)
            cells.append(text)
            rows.append(
                {
                    "name": name,
                    "band": band,
                    "label": labels[band - 1],
                    "range": band_label(band, edges),
                    "share": float(part) / float(whole) if whole else 0.0,
                    "shown": text,
                    "families": number(families),
                }
            )
        table.append(tuple(cells))
    names = [short_label(year) for year in shown]
    summary = (
        f"{title}, {number(len(labels))} bands from the smallest pledges, for "
        f"{names[0]}"
        + (f" to {names[-1]}" if len(names) > 1 else "")
        + ". The exact values table lists every share."
    )
    tooltip = [
        {"field": "name", "title": "Year"},
        {"field": "range", "title": "Annual pledge"},
        {"field": "shown", "title": axis},
    ]
    if key == "families":
        tooltip.append({"field": "families", "title": "Families"})
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "config": CONFIG,
        "autosize": AUTOSIZE,
        "width": WIDTH,
        "height": {"step": 26},
        "data": {"values": rows},
        "mark": {"type": "bar", "stroke": "#ffffff", "strokeWidth": 1},
        "encoding": {
            "y": {
                "field": "name",
                "type": "nominal",
                "sort": names,
                "title": None,
                "axis": {"labelLimit": 120},
            },
            "x": {
                "field": "share",
                "type": "quantitative",
                "stack": "zero",
                "title": axis,
                "scale": {"domain": [0, 1], "nice": False},
                "axis": {"format": ".0%", "tickCount": 5, "labelOverlap": True},
            },
            "color": {
                "field": "label",
                "type": "nominal",
                "title": "Annual pledge",
                "sort": labels,
                "scale": {"domain": labels, "range": list(BAND_COLORS)},
                "legend": {
                    "orient": "top",
                    "columns": legend_columns(110),
                    "labelLimit": 120,
                },
            },
            "order": {"field": "band", "type": "quantitative", "sort": "ascending"},
            "tooltip": tooltip,
        },
    }
    return ChartDocument(
        f"comparison-bands-{key}",
        title,
        summary,
        spec,
        ChartTable(
            ("Year", *(band_label(b, edges) for b in range(1, len(edges) + 2))),
            tuple(table),
        ),
        (NOMINAL_DOLLARS,),
    )


def distribution_charts(comparison, edges):
    """The two pledge-band charts, when Financial stewardship has bands."""
    if not comparison.financial:
        return []
    charts = [
        distribution_chart(key, title, axis, comparison, edges)
        for key, title, axis in DISTRIBUTIONS
    ]
    return [chart for chart in charts if chart is not None]
