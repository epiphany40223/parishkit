"""The Campaign comparison download files: CSV, XLSX and the PDF summary.

Built by the export worker from the retained capture
(``comparison_exports.capture``), which already holds only what may leave
the Admin portal. Every format carries the page's **Every number** table
with the page's columns, names and order (UX conventions, "Downloaded
files"):

- CSV is the table alone; XLSX is the table plus its "Report information"
  sheet, both written by the shared renderer
  (``information_rendering``): counts are numbers, money is an exact
  amount (a dollar-formatted number in XLSX), the day 1 dates are dates,
  and a figure the page shows as "—" is a blank cell.
- The PDF is the summary the Administrator sends leadership, in the shared
  page frame (``pdf_design``) on landscape pages: page 1 holds the
  headline tiles, then the day-by-day charts, each as wide as the page
  body as on the page, stacked and flowing onto the next page (two to a
  page) when they no longer fit; the Every number table follows (one more
  page for any ordinary number of years), every cell in the page's own
  words ("—" for an unknown figure).

Report details that only a file needs (report, parish, request time, time
zone, notes on the withheld figures) are the report information; none is
an archive's free-text note.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.presentation import number

from .comparison import PAGE_ONLY, WITHHELD
from .money import MoneyAmount

TITLE = "Campaign comparison"
PRIVACY = "Parish giving figures, aggregates only. Share only with parish leadership."
# The page's permanent data note on the two kinds of denominator, a fixed
# text (never an archive's note), shortened for the files.
DENOMINATORS = (
    "This system's percentage counts every Family eligible for the portal at "
    "any time through the day; each imported year's uses the active-Family "
    "count its archive file names."
)
NOMINAL = "Amounts are in dollars of each year, with no adjustment for inflation."
WITHHELD_NOTE = (
    "Withheld: a figure left out of this file so that no group of fewer than "
    "5 pledging Families can be worked out from it. The Campaign comparison "
    "page shows it."
)
PAGE_ONLY_NOTE = (
    "On the page only: this campaign's and a partial year's quartiles change "
    "as pledges arrive, so they are shown only on the Campaign comparison page."
)


@dataclass(frozen=True, repr=False)
class ComparisonDocument:
    """All captured values, with no live queries or mutable rows.

    ``headings`` and ``rows`` are the Every number table (cells are ints,
    ``MoneyAmount``, dates, text or None); ``tiles`` and ``charts`` are the
    PDF summary's, as captured; ``data_line`` is the page's "Day N of …"
    line and ``day`` the comparison day the charts mark (None before day 1
    has ended).
    """

    metadata: tuple[tuple[str, object], ...]
    headings: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    item_count: int
    requested_at: datetime
    data_line: str
    day: int | None = None
    tiles: tuple = ()
    charts: tuple = ()
    notes: tuple[str, ...] = field(default=())
    title: str = TITLE
    sheet_name: str = "Every number"


def cell(value):
    """One captured cell as the renderer's value (see ``ComparisonDocument``)."""
    if isinstance(value, dict):
        if "money" in value:
            amount = Decimal(value["money"])
            return MoneyAmount(int(amount * 100))
        return date.fromisoformat(value["date"])
    if value is not None and type(value) not in (int, str):
        raise ValueError("Unexpected comparison export cell.")
    return value


def data_line(payload):
    """The page's data line: "Day 12 of Stewardship 2027 · through …".

    Its date is in the parish's date format (``dates.using`` around the
    worker's load), as the page shows it.
    """
    if payload["day"] is None:
        return (
            f"{payload['campaign']} · Day 1 has not ended yet, so the past "
            "campaigns' final results are shown."
        )
    through = dates.format_date(date.fromisoformat(payload["report_date"]))
    line = f"Day {number(payload['day'])} of {payload['campaign']} · through {through}"
    if payload["compared_with"]:
        line += f" · compared with {payload['compared_with']}"
    return line


def comparison_document(payload, *, parish_name, requested_at, timezone):
    """The file's metadata, table and summary from one retained capture."""
    rows = tuple(tuple(cell(value) for value in row) for row in payload["rows"])
    shown = {value for row in rows for value in row if isinstance(value, str)}
    notes = [DENOMINATORS]
    if payload["financial"]:
        notes.append(NOMINAL)
    if WITHHELD in shown or any(
        WITHHELD in line for tile in payload["tiles"] for line in tile["lines"]
    ):
        notes.append(WITHHELD_NOTE)
    if PAGE_ONLY in shown:
        notes.append(PAGE_ONLY_NOTE)
    line = data_line(payload)
    metadata = (
        ("Report", TITLE),
        ("Parish", parish_name),
        ("Campaign", payload["campaign"]),
        ("Compared", line),
        *(
            (
                (
                    "Figures",
                    "Updating: the latest complete figures, which may not yet "
                    "include the newest responses",
                ),
            )
            if payload["updating"]
            else ()
        ),
        ("Requested at", requested_at.astimezone(ZoneInfo(timezone))),
        ("Display timezone", timezone),
        ("Years", f"{len(rows):,}"),
        *(("Note", note) for note in notes),
        ("Privacy", PRIVACY),
    )
    return ComparisonDocument(
        metadata=metadata,
        headings=tuple(payload["columns"]),
        rows=rows,
        item_count=len(rows),
        requested_at=requested_at,
        data_line=line,
        day=payload["day"],
        tiles=tuple(payload["tiles"]),
        charts=tuple(payload["charts"]),
        notes=tuple(notes),
    )


def load_comparison(request):
    """The worker's document for a comparison export, from its capture."""
    return comparison_document(
        request.comparison_snapshot.document,
        parish_name=request.configuration.parish.name,
        requested_at=request.created_at,
        timezone=request.browser_timezone,
    )


# -------------------------------------------------------------------- PDF


def page_text(value):
    """A cell in the page's own words: "—" for unknown, as the page shows it."""
    if value is None:
        return "—"
    if isinstance(value, MoneyAmount):
        return value.display
    if type(value) is int:
        return number(value)
    if isinstance(value, date):
        return dates.format_date(value, compact=True)
    return value


# Layout of the summary page, in points (pdf_design's units).
TILE_GAP = 8.0
TILE_PAD = 7.0
TILE_VALUE = 14.0
TILE_LABEL = 7.5
TILE_LINE = 7.0
TILE_PITCH = 8.5
CHART_GAP = 18.0
# From the top of a page's chart area to its first chart's box: the legend
# row, then room for the chart's title (drawn just above its box).
LEGEND_ROW = 24.0
# Full-width charts on a page of their own; the summary page holds as many
# as fit below its tiles at the same height.
CHARTS_PER_PAGE = 2
NOTE_SIZE = 7.0
NOTE_PITCH = 9.0
# Each line role's drawing, as on the page (comparison_charts.STYLES): the
# dash patterns tell the lines apart without color.
LINE_STYLES = {
    "current": ("accent", 2.4, "-"),
    "last": ("gold", 1.6, (0, (6, 3))),
    "average": ("ink", 1.6, (0, (8, 3, 2, 3))),
    "other": ("control-border", 0.8, "-"),
}
# The legend's order, and the names of the roles drawn for several years.
LEGEND = ("current", "last", "average", "other")
OTHER_YEARS = "Other years"


def tile_lines(tile, width):
    """A tile's label, value and comparison lines, each wrapped to ``width``."""
    from .pdf_design import safe, wrap_text

    return (
        wrap_text(safe(tile["label"]), width, TILE_LABEL, "bold"),
        wrap_text(safe(tile["value"]), width, TILE_VALUE, "bold"),
        [
            piece
            for line in tile["lines"]
            for piece in wrap_text(safe(line), width, TILE_LINE)
        ],
    )


def tiles_height(tiles, width):
    """The tile row's height: room for the tallest tile's lines."""
    if not tiles:
        return 0.0
    tallest = 0
    for tile in tiles:
        labels, values, lines = tile_lines(tile, width)
        tallest = max(tallest, len(labels) + len(values) * 2 + len(lines))
    return tallest * TILE_PITCH + 2 * TILE_PAD + 4


def tile_width(tiles):
    """Each tile's width: the row shares the body width, with gaps."""
    from .pdf_design import BODY_WIDTH

    return (BODY_WIDTH - TILE_GAP * (len(tiles) - 1)) / max(len(tiles), 1)


def draw_tiles(canvas, tiles, top):
    """The headline tiles in one row: label, large figure, comparison lines."""
    from .pdf_design import ACCENT_STRONG, BORDER, INK, MARGIN, MUTED

    if not tiles:
        return 0.0
    width = tile_width(tiles)
    inner = width - 2 * TILE_PAD
    height = tiles_height(tiles, inner)
    for index, tile in enumerate(tiles):
        x = MARGIN + index * (width + TILE_GAP)
        canvas.rect(x, top, width, height, "white", BORDER)
        canvas.rect(x, top, 3, height, ACCENT_STRONG)
        y = top + TILE_PAD + TILE_LABEL
        labels, values, lines = tile_lines(tile, inner)
        for text in labels:
            canvas.text(x + TILE_PAD, y, text, TILE_LABEL, "bold", MUTED)
            y += TILE_PITCH
        y += TILE_VALUE - TILE_PITCH + 4
        for text in values:
            canvas.text(x + TILE_PAD, y, text, TILE_VALUE, "bold", ACCENT_STRONG)
            y += TILE_PITCH * 2
        y -= TILE_PITCH - 2
        for text in lines:
            canvas.text(x + TILE_PAD, y, text, TILE_LINE, "regular", INK)
            y += TILE_PITCH
    return height


def axis_formatter(kind):
    """The value axis's tick labels: counts, percentages or compact dollars."""
    from matplotlib.ticker import FuncFormatter

    def money(value, _):
        """$800, $25K or $1.2M: compact, since the table has every cent."""
        size = abs(value)
        if size >= 1_000_000:
            return f"${value / 1_000_000:,.1f}M".replace(".0M", "M")
        if size >= 1_000:
            return f"${value / 1_000:,.0f}K"
        return f"${value:,.0f}"

    if kind == "percent":
        return FuncFormatter(lambda value, _: f"{value * 100:,.0f}%")
    if kind == "money":
        return FuncFormatter(money)
    return FuncFormatter(lambda value, _: f"{value:,.0f}")


def draw_chart(canvas, chart, day, box):
    """One day-by-day chart in ``box`` (x, y, width, height in points).

    Lines are drawn bottom to top as captured (other years, average, last
    year, this campaign), each in its role's color, width and dash pattern;
    a gap (None) breaks a line. A dotted rule marks the comparison day.
    """
    from matplotlib.ticker import MaxNLocator

    from parishkit.stewardship.web.design_tokens import COLORS

    from .pdf_design import INK, MUTED, PAGE_HEIGHT, PAGE_WIDTH, safe

    x, y, width, height = box
    canvas.text(x, y, safe(chart["title"]), 8.5, "bold", INK)
    # Room above the plot for the title, below for the day axis and its
    # title, and on the left for the value labels.
    # The right inset leaves room for the last day's tick label, centered
    # on the plot's right edge.
    left, right, top, bottom = 38.0, 8.0, 10.0, 24.0
    axes = canvas.figure.add_axes(
        [
            (x + left) / PAGE_WIDTH,
            1 - (y + height) / PAGE_HEIGHT + bottom / PAGE_HEIGHT,
            (width - left - right) / PAGE_WIDTH,
            (height - top - bottom) / PAGE_HEIGHT,
        ]
    )
    for line in chart["lines"]:
        color, weight, dash = LINE_STYLES[line["role"]]
        days = [point[0] for point in line["points"]]
        values = [
            float("nan") if point[1] is None else point[1] for point in line["points"]
        ]
        axes.plot(
            days,
            values,
            color=COLORS[color],
            linewidth=weight,
            linestyle=dash,
            solid_capstyle="butt",
        )
    if day:
        axes.axvline(day, color=COLORS["muted"], linewidth=0.8, linestyle=":")
        # "Day N" at the rule's top, on its right like the page's, in the
        # plot's own coordinates: the day on x, the top edge on y.
        axes.text(
            day,
            1,
            f" Day {day:,}",
            transform=axes.get_xaxis_transform(),
            fontsize=6.5,
            color=COLORS["muted"],
            ha="left",
            va="top",
        )
    axes.set_xlim(1, max(chart["last_day"], 2))
    axes.set_ylim(bottom=0)
    # More day ticks than a third-width chart had room for: the full-width
    # plot spreads them out, as the page's chart does.
    axes.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=12))
    axes.yaxis.set_major_locator(MaxNLocator(nbins=5))
    axes.yaxis.set_major_formatter(axis_formatter(chart["kind"]))
    axes.tick_params(labelsize=6.5, colors=MUTED, length=2)
    axes.set_xlabel("Campaign day", fontsize=7, color=INK, labelpad=1)
    axes.grid(axis="y", color=COLORS["border"], linewidth=0.5)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(COLORS["border"])


def draw_legend(canvas, charts, top):
    """One legend row for every chart: each role drawn, with its name.

    This campaign and last year are named as their lines are ("2027 (so
    far)", "2026 (last year)"); the average and the other years by role.
    """
    from matplotlib.lines import Line2D

    from parishkit.stewardship.web.design_tokens import COLORS

    from .pdf_design import INK, MARGIN, PAGE_HEIGHT, PAGE_WIDTH, safe, text_width

    names = {}
    for chart in charts:
        for line in chart["lines"]:
            if line["role"] == "last":
                names.setdefault("last", f"{line['name']} (last year)")
            elif line["role"] == "other":
                names.setdefault("other", OTHER_YEARS)
            else:
                names.setdefault(line["role"], line["name"])
    x = MARGIN
    for role in LEGEND:
        if role not in names:
            continue
        name = safe(names[role])
        color, weight, dash = LINE_STYLES[role]
        canvas.figure.add_artist(
            Line2D(
                (x / PAGE_WIDTH, (x + 22) / PAGE_WIDTH),
                (1 - (top - 2.5) / PAGE_HEIGHT,) * 2,
                transform=canvas.figure.transFigure,
                color=COLORS[color],
                linewidth=weight,
                linestyle=dash,
            )
        )
        canvas.text(x + 26, top, name, 7.5, "regular", INK)
        x += 26 + text_width(name, 7.5) + 16


def notes_lines(document):
    """The summary page's notes, wrapped to the body width."""
    from .pdf_design import BODY_WIDTH, safe, wrap_text

    return [
        line
        for note in document.notes
        for line in wrap_text(safe(note), BODY_WIDTH, NOTE_SIZE)
    ]


def chart_height(frame):
    """Each chart's height: ``CHARTS_PER_PAGE`` charts fill a page's body."""
    return (
        frame.body_height - LEGEND_ROW - CHART_GAP * (CHARTS_PER_PAGE - 1) - 4
    ) / CHARTS_PER_PAGE


def chart_layout(document, frame):
    """Where the tiles, notes and each full-width chart go, page by page.

    Returns ``(tiles_top, notes_top, pages)``: ``pages`` has one entry per
    page that draws charts, the summary page first, each ``(legend_top,
    [(chart, box), ...])`` with ``box`` as ``draw_chart`` takes it. Every
    chart is ``BODY_WIDTH`` wide and the same height; the summary page
    takes as many as fit between its tiles and its notes (possibly none),
    and the rest follow ``CHARTS_PER_PAGE`` to a page. A page with no chart
    has no legend, so the summary page's entry may be empty.
    """
    from .pdf_design import BODY_WIDTH, MARGIN

    tiles_top = frame.body_top + 4
    tiles = tiles_height(document.tiles, tile_width(document.tiles) - 2 * TILE_PAD)
    notes_top = frame.body_bottom - NOTE_PITCH * len(notes_lines(document)) - 4
    height = chart_height(frame)
    top, bottom = tiles_top + tiles + 14, notes_top
    pages, placed = [], []
    for chart in document.charts:
        y = top + LEGEND_ROW + len(placed) * (height + CHART_GAP)
        # ``chart_height`` makes ``CHARTS_PER_PAGE`` charts end exactly at
        # the body's bottom; the tolerance keeps float rounding from
        # pushing the last of them onto a page of its own.
        if y + height > bottom + 0.01:
            # Next page: the whole body, below the frame's header.
            pages.append((top, placed))
            top, bottom, placed = frame.body_top + 4, frame.body_bottom, []
            y = top + LEGEND_ROW
        placed.append((chart, (MARGIN, y, BODY_WIDTH, height)))
    pages.append((top, placed))
    return tiles_top, notes_top, pages


def draw_charts(canvas, document, page):
    """One page's chart area: the shared legend, then its stacked charts."""
    legend_top, placed = page
    if not placed:
        return
    draw_legend(canvas, document.charts, legend_top + 6)
    for chart, box in placed:
        draw_chart(canvas, chart, document.day, box)


def draw_summary(canvas, document, frame, layout):
    """Page 1: the tiles, then the charts that fit below them, then notes."""
    from .pdf_design import MARGIN, MUTED

    tiles_top, notes_top, pages = layout
    draw_tiles(canvas, document.tiles, tiles_top)
    draw_charts(canvas, document, pages[0])
    y = notes_top + NOTE_PITCH
    for line in notes_lines(document):
        canvas.text(MARGIN, y, line, NOTE_SIZE, "regular", MUTED)
        y += NOTE_PITCH


# The Every number table's type, a little smaller than body text: fifteen
# columns of figures fit the landscape page with no amount broken.
TABLE_SCALE = 0.8


def fitted_table(headings, rows):
    """The Every number table with each column as wide as its longest word.

    A column needs its widest word (a heading word, or a whole amount such
    as "$1,234,567.89") plus its padding, so no amount breaks between its
    digits; the body width left over is shared in proportion to those needs,
    so the table spans the page. Were the needs ever wider than the page,
    every column shrinks in that proportion and the widest words break, as
    every report table's do.
    """
    from .pdf_design import (
        BODY_SIZE,
        BODY_WIDTH,
        CELL_PAD,
        LABEL_SIZE,
        Table,
        safe,
        text_width,
    )

    def widest(text, size, face):
        """The widest word of ``text`` in points."""
        return max(
            (text_width(word, size, face) for word in safe(text).split()), default=0
        )

    needs = [
        max(
            [widest(heading, LABEL_SIZE * TABLE_SCALE, "bold")]
            + [widest(row[index], BODY_SIZE * TABLE_SCALE, "regular") for row in rows]
        )
        + 2 * CELL_PAD
        + 1
        for index, heading in enumerate(headings)
    ]
    total = sum(needs)
    widths = [BODY_WIDTH * need / total for need in needs]
    if total < BODY_WIDTH:
        spare = BODY_WIDTH - total
        widths = [need + spare * need / total for need in needs]
    return Table(tuple(headings), tuple(widths), TABLE_SCALE)


def comparison_pdf(document, output):
    """The summary page, any further chart pages, then the table's pages.

    Lays out the charts and paginates the table first so every page states
    its position.
    """
    from . import pdf_design

    # The data line, and the Updating notice when the page showed it, head
    # every page, as they head the report on the page.
    details = (document.data_line,) + tuple(
        value for label, value in document.metadata if label == "Figures"
    )
    frame = pdf_design.PdfFrame.for_document(document, details=details)
    rows = [[page_text(value) for value in row] for row in document.rows]
    table = fitted_table(document.headings, rows)
    layout = chart_layout(document, frame)
    pages = (
        [("summary", None)]
        + [("charts", page) for page in layout[2][1:]]
        + [("table", page) for page in pdf_design.table_pages(table, rows, frame)]
    )
    draw_table = pdf_design.draw_table(table, frame)

    def draw(canvas, page):
        """Dispatch one page to its body."""
        kind, content = page
        if kind == "summary":
            draw_summary(canvas, document, frame, layout)
        elif kind == "charts":
            draw_charts(canvas, document, content)
        else:
            draw_table(canvas, content)

    return frame.write(
        output, pages, len(pages), draw, requested_at=document.requested_at
    )


def render_comparison(document, output, *, format):
    """Write one format: CSV and XLSX through the shared renderer, or the PDF."""
    from .information_rendering import information_csv, information_xlsx

    renderers = {
        "csv": information_csv,
        "xlsx": information_xlsx,
        "pdf": comparison_pdf,
    }
    try:
        renderer = renderers[format]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported comparison export format.") from error
    return renderer(document, output)
