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
- **Pledge distribution:** per year, two 100% stacked bars: the share of
  its pledging Families, and of its dollars pledged, in each pledge band,
  each band labelled with its figures where it fits and every band's
  figures in the legend.

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
from .comparison import band_label, money_text, share_text, value_text, years_label

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
# them as well as hue, and white edges separate neighbouring segments. Each
# has the ink its segment's label is drawn in, the one of ink and white with
# at least 4.5:1 contrast on it (WCAG AA for the 11-pixel labels); the
# fifth band is a shade darker than an even step so white reaches 4.5:1.
BAND_COLORS = (
    ("#bfe0d9", COLORS["ink"]),
    ("#a1d1c7", COLORS["ink"]),
    ("#7ebfb2", COLORS["ink"]),
    ("#55a898", COLORS["ink"]),
    ("#2b8273", "#ffffff"),
    ("#1d7667", "#ffffff"),
    ("#115e56", "#ffffff"),
    ("#0a3f3a", "#ffffff"),
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
# The bar charts' band height in pixels per bar: a year's final figure, and
# a pledge-band bar (tall enough for a segment's two-line label). A bar
# chart's height is fixed at this times its bar count, rather than
# Vega-Lite's {"step": ...}: a step height beside the page's "container"
# width makes Vega-Lite warn on every draw that it drops fit-y (#1325).
FINAL_STEP = 26
BAND_STEP = 40
# The horizontal bar charts' value-axis title ends at the plot's right
# edge instead of being centered under the plot. At phone width the year
# labels take the left of the chart, so the plot is narrower than a title
# such as "Annual pledges (US dollars)" in some fonts (WebKit with DejaVu
# Sans), and a centered title then runs past the chart's right edge and
# is cut off. Anchored at the end, it extends left under the empty corner
# below the year labels.
VALUE_AXIS_TITLE = {"titleAnchor": "end"}


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
    headings = ("Day", *(column_name(line) for line in ordered))
    return ChartTable(headings, tuple(rows))


def column_name(line):
    """A line's name in the table and the tooltip: a grey year by its year
    ("2024 (partial)"), any other line as its legend names it."""
    return line.name if line.role == OTHER else line.legend


def day_tips(measure, drawn, last_day):
    """One hover row per day: every line's value (and date) on that day.

    Pointing anywhere over a day-by-day chart picks a day (its band, from
    ``from`` to ``to``, in ``day_chart``), and its tooltip lists every line on
    that day, as the exact-values table's row does, so no one has to find a
    thin line's point to read it. ``tip`` is an object: vega-tooltip shows
    its ``title`` as the heading and every other key as a row, in order,
    escaping each. A line with no value that day is left out.
    """
    ordered = list(reversed(drawn))  # current first, as the legend
    rows = []
    for day in range(1, last_day + 1):
        tip = {"title": f"Day {number(day)}"}
        for line in ordered:
            found = value_on(line, day)
            if found is None or found[0] is None:
                continue
            text = point_text(measure, *found)
            # A carried final value has no date of its own; the average none.
            if not found[1] and day in line.dates:
                text += f" · {format_date(line.dates[day], compact=True)}"
            # A JavaScript object lists integer-like keys ("2024") before
            # every other, whatever their order here; a trailing no-break
            # space (invisible) keeps a grey year in the legend's order.
            name = column_name(line)
            tip[name + "\u00a0" if name.isdigit() else name] = text
        rows.append({"day": day, "from": day - 0.5, "to": day + 0.5, "tip": tip})
    return rows


def day_chart(measure, comparison):
    """One day-by-day line chart, or None when no year has a value."""
    drawn, last_day = lines(measure, comparison)
    if not drawn:
        return None
    # Every point once, at the top level: each line layer filters its role,
    # and the hovered day's points take them all.
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
    # Vega-Lite merges the three channels' legends, which share the field,
    # into the one color legend (stroke symbols in each line's width and
    # dash). None of them sets "legend": None, which the merge ignores
    # anyway while warning on every draw that the merged legends conflict.
    encoding_series = {
        channel: {
            "field": "series",
            "type": "nominal",
            "title": None,
            "scale": legend_scale(drawn, index),
        }
        for channel, (_name, index) in series.items()
    }
    # A nominal width would get a point scale, which spreads its range
    # between the first and last value (and has none for one line); each
    # role's own width needs an ordinal scale. Its sort, the scale's own
    # domain order, changes nothing drawn; without one Vega-Lite warns on
    # every draw that strokeWidth should not encode an unsorted nominal
    # field (#1325).
    encoding_series["strokeWidth"]["scale"]["type"] = "ordinal"
    encoding_series["strokeWidth"]["sort"] = list(
        encoding_series["strokeWidth"]["scale"]["domain"]
    )
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
    # Each line's point on the hovered day, shown only while a day is
    # hovered (the ``hover`` selection below), so the reader sees which
    # values the tooltip lists. They stay out of the accessibility tree;
    # the table holds every value.
    layers.append(
        {
            "transform": [{"filter": "isValid(datum.value)"}],
            "mark": {"type": "point", "filled": True, "size": 50, "aria": False},
            "encoding": {
                "x": {"field": "day", "type": "quantitative"},
                "y": {"field": "value", "type": "quantitative"},
                "color": encoding_series["color"],
                "opacity": {
                    "condition": {"param": "hover", "empty": False, "value": 1},
                    "value": 0,
                },
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
    # Hovering: an invisible band one day wide and the plot's full height
    # for each day, drawn last so it lies over every mark, selects its day
    # (``hover``) and carries that day's tooltip listing every line
    # (``day_tips``); the day's rule and points then show. Anywhere over the
    # plot is some day's band, so no one has to find a thin line or a small
    # point, and a line, being one shape, could name just one of its days.
    # Bands, not Vega-Lite's "nearest" Voronoi cells: a cell's tooltip reads
    # the cell's own item, not the day's row, so an object tooltip there is
    # lost.
    tips = day_tips(measure, drawn, last_day)
    hidden_until_hovered = {
        "condition": {"param": "hover", "empty": False, "value": 0.5},
        "value": 0,
    }
    layers += [
        {
            "data": {"values": tips},
            "mark": {"type": "rule", "color": COLORS["ink"], "aria": False},
            "encoding": {
                "x": {"field": "day", "type": "quantitative"},
                "opacity": hidden_until_hovered,
            },
        },
        {
            "data": {"values": tips},
            "params": [
                {
                    "name": "hover",
                    "select": {
                        "type": "point",
                        "fields": ["day"],
                        "on": "pointerover",
                        "clear": "pointerout",
                    },
                }
            ],
            # Clipped: the first and last days' half bands past the axis.
            "mark": {"type": "rect", "opacity": 0, "clip": True, "aria": False},
            "encoding": {
                "x": {"field": "from", "type": "quantitative"},
                "x2": {"field": "to"},
                "y": {"datum": 0},
                "y2": {"datum": top},
                "tooltip": {"field": "tip"},
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
    tooltip = [
        {"field": "name", "title": "Year"},
        {"field": "when", "title": "Figures"},
        {"field": "shown", "title": axis.split(" (")[0]},
    ]
    summary = (
        f"{title}: " + "; ".join(f"{row['name']} {row['shown']}" for row in rows) + "."
    )
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "config": CONFIG,
        "autosize": AUTOSIZE,
        "width": WIDTH,
        "height": FINAL_STEP * len(rows),
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
                    **VALUE_AXIS_TITLE,
                },
            },
        },
        "layer": [
            {
                # An invisible bar across each year's whole row, under the
                # drawn bar, carries the same tooltip: pointing anywhere on
                # the row reads it, even beside a bar too short to find
                # (this campaign's "so far" one early on).
                "mark": {"type": "bar", "opacity": 0, "aria": False},
                "encoding": {
                    "x": {"datum": 0},
                    "x2": {"datum": top},
                    "tooltip": tooltip,
                },
            },
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
                    "tooltip": tooltip,
                },
            },
            *(
                {
                    "transform": [{"filter": {"field": "inside", "equal": inside}}],
                    # The bar's figure, which its name (from its tooltip)
                    # already says, so assistive technology hears it once,
                    # from the bar (#1266).
                    "mark": {
                        "type": "text",
                        "align": "right" if inside else "left",
                        "baseline": "middle",
                        "dx": -4 if inside else 4,
                        "fontSize": LABEL_FONT,
                        "aria": False,
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


# Each year's chart: one 100% stacked bar of its pledging Families by band,
# and one of its dollars pledged, so the two shares sit side by side (the
# Administrator's preferred banded style, #1221).
DISTRIBUTIONS = (("families", "Families"), ("dollars", "Dollars pledged"))
# Joins a band's name and its two figures in its legend key, which the
# legend splits into three lines: a line each, rather than one, keeps the
# legend narrow enough for a phone-width plot.
KEY_BREAK = " — "
# A segment's label is two lines of the 11-pixel label font, its value over
# its share; this much width per character (generous) plus a little padding
# is the room the segment needs before the label is drawn on it.
SEGMENT_CHAR = 6.6
SEGMENT_PAD = 6


def distribution_chart(year, edges):
    """One year's pledge bands as two 100% stacked bars, or None.

    The bars are the share of the year's pledging Families, and of its
    dollars pledged, in each band, from the smallest. Each band's segment
    is labelled with its Families or dollars over its share when the
    segment is wide enough at the width the chart is drawn (a Vega test
    against the ``width`` signal, so a phone draws fewer labels than a
    laptop and none overlaps); every band's legend entry has its figures
    for both bars, so each value is in the chart at any width (#1266). The
    tooltips and each segment's accessible name carry the same figures.

    The page shows one year's chart at a time, chosen by the Pledge
    distribution section's year selector (campaign-history spec, "Page
    layout"), so the chart is per year; its key names the year so each
    one's elements are distinct. The band table under it lists every
    figure, so the page shows no exact-values table of the chart's own. A
    year with no bands to draw (a reason, an older band set or no pledging
    Family) has none.
    """
    found = year.distribution
    if found is None or found.reason is not None:
        return None
    if not (found.pledging and found.bands):
        return None
    name = short_label(year)
    count = len(edges) + 1
    labels = [short_band_label(band, edges) for band in range(1, count + 1)]
    by_band = {band.band: band for band in found.bands}
    figures = []
    for band in range(1, count + 1):
        entry = by_band.get(band)
        families = entry.families if entry else 0
        total = entry.total if entry else Decimal(0)
        figures.append(
            {
                "families": (
                    number(families),
                    share_text(families, found.pledging),
                    families / found.pledging,
                ),
                "dollars": (
                    money_text(total),
                    share_text(total, found.total),
                    float(total) / float(found.total) if found.total else 0.0,
                ),
            }
        )
    # Each band's legend key: its name, then its Families and its dollars.
    keys = []
    for band, (label, f) in enumerate(zip(labels, figures, strict=True), start=1):
        entry = by_band.get(band)
        noun = "Family" if entry and entry.families == 1 else "Families"
        keys.append(
            KEY_BREAK.join(
                (
                    label,
                    f"{f['families'][0]} {noun} ({f['families'][1]})",
                    f"{f['dollars'][0]} ({f['dollars'][1]})",
                )
            )
        )
    rows = []
    for key, bar in DISTRIBUTIONS:
        for band in range(1, count + 1):
            shown, percent, part = figures[band - 1][key]
            of = "pledging Families" if key == "families" else "the total pledged"
            rows.append(
                {
                    "name": bar,
                    "band": band,
                    "key": keys[band - 1],
                    "range": band_label(band, edges),
                    "share": part,
                    "shown": percent,
                    # What the bar's share is of: the band's Families on
                    # the Families bar, its dollars on the dollars bar.
                    "amount": f"{shown} Families"
                    if key == "families"
                    else f"{shown} pledged",
                    "lines": [shown, percent],
                    "need": round(
                        SEGMENT_PAD + SEGMENT_CHAR * max(len(shown), len(percent))
                    ),
                    "ink": BAND_COLORS[band - 1][1],
                    "description": (
                        f"{bar}, {band_label(band, edges)}: {shown}, {percent} of {of}"
                    ),
                }
            )
    title = f"Pledge bands, {name}"
    summary = (
        f"{title}: the share of pledging Families and of dollars pledged in "
        f"each of {number(count)} bands from the smallest pledges. Each band "
        "is labelled with its Families or dollars where it fits, the legend "
        "gives every band's figures, and the table below lists them all."
    )
    tooltip = [
        {"field": "range", "title": "Annual pledge"},
        {"field": "name", "title": "Share of"},
        {"field": "shown", "title": "Share"},
        {"field": "amount", "title": "In this band"},
    ]
    spec = {
        "$schema": VEGA_LITE_SCHEMA,
        "description": summary,
        "config": CONFIG,
        "autosize": AUTOSIZE,
        "width": WIDTH,
        "height": BAND_STEP * len(DISTRIBUTIONS),
        "data": {"values": rows},
        # Each segment's ends, stacked from the smallest band, so the
        # labels can sit at a segment's middle and test its width.
        "transform": [
            {
                "stack": "share",
                "groupby": ["name"],
                "sort": [{"field": "band"}],
                "as": ["x0", "x1"],
            },
            {"calculate": "(datum.x0 + datum.x1) / 2", "as": "middle"},
        ],
        "encoding": {
            "y": {
                "field": "name",
                "type": "nominal",
                "sort": [bar for _, bar in DISTRIBUTIONS],
                "title": None,
                "axis": {"labelLimit": 120},
            },
        },
        "layer": [
            {
                "mark": {"type": "bar", "stroke": "#ffffff", "strokeWidth": 1},
                "encoding": {
                    "x": {
                        "field": "x0",
                        "type": "quantitative",
                        "title": "Share",
                        "scale": {"domain": [0, 1], "nice": False},
                        "axis": {
                            "format": ".0%",
                            "tickCount": 5,
                            "labelOverlap": True,
                            **VALUE_AXIS_TITLE,
                        },
                    },
                    "x2": {"field": "x1"},
                    "color": {
                        "field": "key",
                        "type": "nominal",
                        "title": "Annual pledge",
                        "sort": keys,
                        "scale": {
                            "domain": keys,
                            "range": [color for color, _ in BAND_COLORS[:count]],
                        },
                        "legend": {
                            "orient": "top",
                            "columns": legend_columns(140),
                            "labelLimit": 130,
                            "labelExpr": f"split(datum.label, '{KEY_BREAK}')",
                        },
                    },
                    "description": {"field": "description"},
                    "tooltip": tooltip,
                },
            },
            {
                # The segment's label, drawn only when the segment is wide
                # enough for it at the drawn width; its values are in the
                # segment's name, so assistive technology skips it.
                "mark": {
                    "type": "text",
                    "baseline": "middle",
                    "lineHeight": LABEL_FONT + 2,
                    "fontSize": LABEL_FONT,
                    "aria": False,
                },
                "encoding": {
                    "x": {"field": "middle", "type": "quantitative"},
                    "text": {
                        "condition": {
                            "test": "(datum.x1 - datum.x0) * width >= datum.need",
                            "field": "lines",
                        },
                        "value": "",
                    },
                    "color": {"field": "ink", "type": "nominal", "scale": None},
                    "tooltip": tooltip,
                },
            },
        ],
    }
    # The record a rendering without the page's band table would show; the
    # page itself shows that table instead (campaign-comparison-bands.html).
    table = ChartTable(
        ("Annual pledge", "Families", "Total pledged"),
        tuple(
            (band_label(band, edges), f["families"][0], f["dollars"][0])
            for band, f in enumerate(figures, start=1)
        ),
    )
    return ChartDocument(
        "comparison-bands" + ("" if year.current else f"-{year.year}"),
        title,
        summary,
        spec,
        table,
        (NOMINAL_DOLLARS,),
    )
