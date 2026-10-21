"""Non-interactive PNG/PDF from the same exact participation document as tables.

Matplotlib access is serialized process-locally, uses no pyplot figure registry,
and resets style under that lock. Rendering is deterministic for the pinned
runtime/fonts and input document, not promised byte-identical across upgrades.

The plot is the same in every file: stacked panels on one shared date axis,
one per series, and never a second value axis (#720). Its page furniture
differs: the page PNG draws its own titles and footer, the daily email's PNG
has none (#720), and the PDF is a US Letter page in the shared report frame
(#926).
"""

from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import NamedTuple

from parishkit.stewardship.web.dates import format_date

from . import pdf_design
from .participation import ParticipationDocument

_RENDER_LOCK = RLock()
# Version 3 draws separate panels instead of a second dollar axis (#720).
RENDERER_VERSION = "participation-v3"
# The daily email's drawing (#720), named in its PNG's Creator text so the
# saved report page can tell which drawing a retained digest holds.
EMAIL_RENDERER_VERSION = "participation-email-v2"
# The PDF's drawing: the plot inside the shared report page frame (#926),
# among the Response dashboard's sections since version 3 (#1557).
PDF_RENDERER_VERSION = "participation-pdf-v3"
# The PDF section the chart page and the daily table hold (#1557).
DAILY_SECTION = "Daily participation"
# The plot box (figure fractions) spans every panel, from the top of the
# first to the bottom of the last; the page's hover and date slider map the
# box's left and right edges to the first and last dates. With no right-hand
# dollar axis the box runs nearly to the right edge.
PLOT_LAYOUT = {"left": 0.09, "right": 0.97, "top": 0.80, "bottom": 0.17}
# The daily email's drawing has no page title or footer, so its plot fills
# the image (#720). The saved report page hit-tests whichever one it shows.
EMAIL_PLOT_LAYOUT = {"left": 0.09, "right": 0.97, "top": 0.92, "bottom": 0.17}
# The boxes of earlier drawings, which retained digests still hold and are
# never redrawn: the page drawing before #720 ("participation-v2") and the
# email drawing with a second dollar axis ("participation-email-v1").
LEGACY_PLOT_LAYOUT = {"left": 0.09, "right": 0.89, "top": 0.74, "bottom": 0.29}
DIGEST_PLOT_LAYOUTS = {
    EMAIL_RENDERER_VERSION: EMAIL_PLOT_LAYOUT,
    "participation-email-v1": {
        "left": 0.09,
        "right": 0.89,
        "top": 0.90,
        "bottom": 0.17,
    },
}


class ChartLayout(NamedTuple):
    """Where one drawing puts its plot, panel titles and note (figure fractions).

    ``title_size`` is the panel titles' font size. ``furniture`` draws the
    page PNG's own parish, campaign and title lines and its renderer footer;
    the email has none, and the PDF's shared frame draws its own.
    ``zone_label`` names the campaign time zone on the date axis (the
    email's as-of line names it instead).
    """

    plot: dict
    title_size: float
    note: tuple[float, float]
    furniture: bool
    zone_label: bool


PAGE_LAYOUT = ChartLayout(PLOT_LAYOUT, 10, (0.09, 0.055), True, True)
EMAIL_LAYOUT = ChartLayout(EMAIL_PLOT_LAYOUT, 11, (0.09, 0.055), False, False)
# The vertical gap between panels, as a share of one panel's height: room
# for the next panel's title above it.
PANEL_GAP = 0.35


def pdf_layout(frame):
    """The PDF's chart layout inside the shared page frame's body (#926).

    The first panel's title sits at the top of the body. Under the plot
    there is room for the rotated date ticks, the axis label and the gap
    note, which sits on the body's last line. The left and right sides are
    only a first guess: ``_fit_to_margins`` then moves them so the value
    axes' tick labels and titles and the first rotated date end exactly at
    the page margins, however wide the numbers are.
    """

    def down(points):
        """A figure fraction for a distance in points from the page top."""
        return 1 - points / pdf_design.PAGE_HEIGHT

    width = pdf_design.PAGE_WIDTH
    return ChartLayout(
        plot={
            "left": (pdf_design.MARGIN + 54) / width,
            "right": (width - pdf_design.MARGIN - 20) / width,
            "top": down(frame.body_top + 16),
            "bottom": down(frame.body_bottom - 78),
        },
        title_size=9,
        note=(pdf_design.MARGIN / width, down(frame.body_bottom - 2)),
        furniture=False,
        zone_label=True,
    )


def _fit_to_margins(figure):
    """Move the PDF plot's sides so everything it draws meets the margins.

    The widths of the value axes' tick labels (a $10,000,000 pledge total,
    a 4-digit Family count) and of the first rotated date label are only
    known once drawn, so measure the axes' full extents and shift the plot
    box by the overflow or slack on each side. The figure is at 72 dpi, so
    pixels are PDF points. A second pass settles the small change in the
    first date label's offset when the plot width changes.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    renderer = FigureCanvasAgg(figure).get_renderer()
    width = pdf_design.PAGE_WIDTH
    for _ in range(2):
        left = min(axes.get_tightbbox(renderer).x0 for axes in figure.axes)
        right = max(axes.get_tightbbox(renderer).x1 for axes in figure.axes)
        params = figure.subplotpars
        figure.subplots_adjust(
            left=params.left + (pdf_design.MARGIN - left) / width,
            right=params.right + (width - pdf_design.MARGIN - right) / width,
        )


def pdf_frame(document, section=None):
    """The shared report page frame for the participation PDF (#926).

    It names the parish, campaign and population scope like the page PNG,
    and ``section``, the Response dashboard section the page holds (#1557),
    under the title. Unlike the other report PDFs it states no capture or
    request time: the chart's provenance travels in the file's metadata
    instead (#575).
    """
    return pdf_design.PdfFrame(
        title=f"Family participation — {document.scope_label}",
        eyebrow=f"{document.parish_name} · {document.campaign_name}",
        details=(section,) if section else (),
    )


def axis_top(values):
    """Return a panel's value-axis top: its largest value plus 10% headroom.

    Each panel is scaled to its own series, never to the eligible population
    (#575), which would flatten it. Unavailable days (NaN gaps) do not
    count; an empty or all-zero series still gets a top of 1.1 so the axis
    is drawable.
    """
    return max([1, *(value for value in values if value == value)]) * 1.1


def short_dollars(value):
    """A dollar tick label in thousands, millions or billions: "$250K", "$1.6M".

    A parish's pledge total can reach millions, and full "$1,600,000" labels
    would push the axis title off the image's left edge; the exact amounts
    are in the table and the exports. The unit is chosen after rounding, from
    the smallest up: a value that rounds up to 1,000 of one unit is promoted
    to the next ($999,999 reads "$1M", not "$1000K"). A negative reads
    "-$250K".
    """
    sign, amount = ("-" if value < 0 else ""), abs(value)
    scaled, suffix = round(amount), ""
    for unit in ("K", "M", "B"):
        if scaled < 1000:
            break
        amount /= 1000
        scaled, suffix = round(amount, 1), unit
    if suffix:
        return f"{sign}${scaled:,.1f}".removesuffix(".0") + suffix
    return f"{sign}${scaled:,}"


class Panel(NamedTuple):
    """One series of the participation chart, drawn in its own panel (#720).

    ``values`` are plot coordinates (NaN for an unavailable day); ``unit``
    labels the value axis and ``money`` formats its ticks as dollars.
    """

    title: str
    unit: str
    values: list
    bars: bool = False
    money: bool = False


def participation_panels(document):
    """The chart's panels, top to bottom, sharing one date axis.

    Daily first submissions and the cumulative Families are separate panels
    so the daily bars are not dwarfed by the running total, and pledges get
    their own panel (when financial is enabled) instead of a second y-axis
    whose independent scale made the two lines look alike (#720).
    """
    nan = float("nan")
    panels = [
        Panel(
            "First submissions that day",
            "Families",
            [
                day.first_responses if day.population_available else nan
                for day in document.days
            ],
            bars=True,
        ),
        Panel(
            "Cumulative participating Families",
            "Families",
            [
                day.cumulative_responses if day.population_available else nan
                for day in document.days
            ],
        ),
    ]
    if document.financial_enabled:
        panels.append(
            Panel(
                "Effective annual pledges",
                "USD",
                [
                    float(day.pledge_total) if day.pledge_available else nan
                    for day in document.days
                ],
                money=True,
            )
        )
    return panels


def participation_limits(day_count):
    """Share image hit-test coordinates with the exact static chart renderer."""
    return -0.6, max(0.6, day_count - 0.4)


@contextmanager
def rendering_style():
    """Serialize global plotting state for both charts and paginated text PDFs."""
    with _RENDER_LOCK:
        import matplotlib as mpl

        # Ignore host matplotlibrc (including external TeX and custom fonts).
        # Keep the backend selection local; there is no GUI/pyplot dependency.
        style = dict(mpl.rcParamsDefault)
        style.update(
            {
                "font.family": "DejaVu Sans",
                "text.usetex": False,
                "text.parse_math": False,
                "timezone": "UTC",
            }
        )
        with mpl.rc_context(style):
            yield


@contextmanager
def participation_figure(document, *, email=False, pdf=False, page=(1, 1)):
    """Own plotting state until consumption ends; callers cannot leak a figure.

    ``email`` drops the page furniture (parish and campaign names, the scope
    title and the renderer footer) that the daily email already states in its
    text (#720), and lets the plot fill the image (``EMAIL_PLOT_LAYOUT``).
    ``pdf`` draws the PDF's chart page: the shared report frame, naming the
    Daily participation section, around the plot; ``page`` is its
    ``(number, count)`` in the file.
    """
    if not isinstance(document, ParticipationDocument):
        raise TypeError("Rendering requires an immutable participation document.")
    from matplotlib.figure import Figure

    with rendering_style():
        if pdf:
            # 72 dpi, the PDF's own resolution, so measured extents are
            # points and match the saved page.
            figure = Figure(
                figsize=(pdf_design.PAGE_WIDTH / 72, pdf_design.PAGE_HEIGHT / 72),
                dpi=72,
                facecolor="white",
            )
        else:
            figure = Figure(figsize=(12, 7), dpi=120, facecolor="white")
        try:
            if pdf:
                frame = pdf_frame(document, DAILY_SECTION)
                frame.draw(pdf_design.Canvas(figure), *page)
                layout = pdf_layout(frame)
            else:
                layout = EMAIL_LAYOUT if email else PAGE_LAYOUT
            _draw(figure, document, layout)
            if pdf:
                _fit_to_margins(figure)
            yield figure
        finally:
            figure.clear()


def _draw(figure, document, layout):
    """Draw one panel per series on a shared date axis; floats are coordinates.

    The panels fill the layout's plot box together. Each is named by its own
    title, so there is no legend; dates are labelled once, under the last.
    """
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    panels = participation_panels(document)
    grid = figure.subplots(
        len(panels), 1, sharex=True, squeeze=False, gridspec_kw={"hspace": PANEL_GAP}
    )
    figure.subplots_adjust(**layout.plot)
    if layout.furniture:
        figure.text(0.5, 0.96, document.parish_name, ha="center", fontsize=14)
        figure.text(0.5, 0.915, document.campaign_name, ha="center", fontsize=12)
        figure.text(
            0.5,
            0.86,
            f"Family participation — {document.scope_label}",
            ha="center",
            fontsize=12,
        )
    dates = list(range(len(document.days)))
    for axes, panel in zip((row[0] for row in grid), panels, strict=True):
        axes.set_title(panel.title, loc="left", fontsize=layout.title_size, pad=4)
        axes.set_ylabel(panel.unit)
        # Round 1/2/5 steps (10, 20, 50, ...) read more easily than 30 or 25.
        axes.yaxis.set_major_locator(
            MaxNLocator(integer=True, min_n_ticks=2, steps=[1, 2, 5, 10])
        )
        axes.yaxis.set_major_formatter(
            FuncFormatter(
                (lambda value, pos: short_dollars(value))
                if panel.money
                else (lambda value, pos: f"{value:,.0f}")
            )
        )
        axes.grid(axis="y", color="#dddddd", linewidth=0.5)
        if panel.bars:
            axes.bar(
                dates,
                panel.values,
                color="#9ecae1",
                edgecolor="#26658b",
                hatch="//",
                zorder=2,
            )
        elif panel.money:
            axes.plot(
                dates,
                panel.values,
                color="#8a4f00",
                linestyle="--",
                marker="s",
                markersize=3,
                zorder=3,
            )
        else:
            axes.plot(
                dates,
                panel.values,
                color="#173d70",
                linestyle="-",
                marker="o",
                markersize=3,
                zorder=3,
            )
        axes.set_ylim(bottom=0, top=axis_top(panel.values))
    first, last = grid[0][0], grid[-1][0]
    # The email's as-of line names the zone once (#720); the page names it here.
    last.set_xlabel(
        f"Campaign date ({document.campaign_timezone})"
        if layout.zone_label
        else "Campaign date"
    )
    if dates:
        ticks = sorted(
            {0, len(dates) - 1} | set(range(0, len(dates), max(1, len(dates) // 8)))
        )
        last.set_xticks(
            ticks,
            [
                format_date(document.days[index].local_date, compact=True)
                for index in ticks
            ],
            rotation=30,
            ha="right",
        )
        last.set_xlim(*participation_limits(len(dates)))
    else:
        last.set_xticks([])
        first.text(
            0.5,
            0.5,
            "No campaign days in this report",
            transform=first.transAxes,
            ha="center",
        )
    unavailable = any(
        not day.population_available
        or (document.financial_enabled and not day.pledge_available)
        for day in document.days
    )
    note = "Missing observations are gaps, not zero." if unavailable else ""
    # Line the panels' value-axis titles up in one column, whatever the
    # widths of their tick labels.
    figure.align_ylabels([row[0] for row in grid])
    figure.text(*layout.note, note, fontsize=8)
    if layout.furniture:
        figure.text(0.97, 0.035, f"{RENDERER_VERSION} · Page 1", fontsize=7, ha="right")


def provenance_text(document):
    """Return the chart's source and request provenance as one line of text."""
    return " ".join(document.as_of_label.split("\n"))


def pdf_cell(value):
    """One PDF table cell in the page's words; "Unavailable" for an unknown value.

    Dates and times are left to ``pdf_design.safe``, which writes them in
    the parish's date format.
    """
    from parishkit.stewardship.web.presentation import number

    from .money import MoneyAmount

    if value is None:
        return "Unavailable"
    if isinstance(value, MoneyAmount):
        return value.display
    if type(value) is int:
        return number(value)
    return value


def summary_pdf_rows(document):
    """The Summary section's PDF rows: every column of its CSV and XLSX."""
    from zoneinfo import ZoneInfo

    from .participation_summary import share_text

    zone = ZoneInfo(document.browser_timezone)
    return [
        [
            group,
            figure,
            pdf_cell(value.astimezone(zone) if isinstance(value, datetime) else value),
            share_text(ratio),
            note,
        ]
        for group, figure, value, ratio, note in document.summary.rows()
    ]


def daily_pdf_rows(document):
    """The daily table's PDF rows: every column of the CSV and XLSX, in order."""
    from zoneinfo import ZoneInfo

    from .money import MoneyAmount, source_cents
    from .participation import participation_columns, participation_table

    zone = ZoneInfo(document.browser_timezone)
    keys = [key for key, _ in participation_columns(document)]
    rows = []
    for day, row in zip(document.days, participation_table(document), strict=True):
        values = row | {"date": day.local_date}
        if row["source_as_of"] is not None:
            values["source_as_of"] = day.source_as_of.astimezone(zone)
        if "pledge_usd" in row and day.pledge_available:
            values["pledge_usd"] = MoneyAmount(source_cents(row["pledge_usd"]))
        rows.append([pdf_cell(values[key]) for key in keys])
    return rows


def participation_pdf(document, output):
    """The Response dashboard's export as a PDF: its sections in page order.

    The Summary table (when the export captured one), then the chart on a
    page of its own, then every daily row as a table under the CSV's
    columns (#1557). Every page is in the shared report frame and names its
    section under the title. Like the chart image it states no request or
    capture time; the provenance is the file's ``Subject`` (#575), and the
    creation time is the request's, so a retried rendering is the same.
    """
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    from .comparison_documents import fitted_table
    from .participation import participation_columns
    from .participation_summary import COLUMNS as SUMMARY_COLUMNS
    from .participation_summary import SUMMARY

    def tables(section, headings, rows):
        """One section's table pages: ``(frame, table, page)`` each."""
        frame = pdf_frame(document, section)
        table = fitted_table(headings, rows)
        return [
            (frame, table, page) for page in pdf_design.table_pages(table, rows, frame)
        ]

    pages = []
    if document.summary is not None:
        pages += tables(SUMMARY, SUMMARY_COLUMNS, summary_pdf_rows(document))
    pages.append(None)  # the chart
    pages += tables(
        DAILY_SECTION,
        [heading for _, heading in participation_columns(document)],
        daily_pdf_rows(document),
    )
    metadata = {
        "Title": "Family participation",
        "Creator": PDF_RENDERER_VERSION,
        "Subject": provenance_text(document),
        # The logical document originates at its immutable request, not at
        # a retry's wall-clock render time.
        "CreationDate": document.requested_at,
        "ModDate": document.requested_at,
        "Producer": "ParishKit",
    }
    with rendering_style(), PdfPages(output, metadata=metadata) as pdf:
        for number, page in enumerate(pages, 1):
            if page is None:
                with participation_figure(
                    document, pdf=True, page=(number, len(pages))
                ) as figure:
                    pdf.savefig(figure)
                continue
            frame, table, rows = page
            figure = Figure(
                figsize=(pdf_design.PAGE_WIDTH / 72, pdf_design.PAGE_HEIGHT / 72),
                facecolor="white",
            )
            try:
                canvas = pdf_design.Canvas(figure)
                frame.draw(canvas, number, len(pages))
                pdf_design.draw_table(table, frame)(canvas, rows)
                pdf.savefig(figure)
            finally:
                figure.clear()


def render_participation(document, output, *, format, email=False):
    """Write a fixed-layout artifact without host clock or random PDF metadata.

    ``email`` selects the daily email's PNG drawing (see
    ``participation_figure``); a PDF is the export's sections in the report
    frame (``participation_pdf``).
    """
    if format not in {"png", "pdf"}:
        raise ValueError("Participation charts support PNG or PDF.")
    if format == "pdf":
        return participation_pdf(document, output)
    with participation_figure(document, email=email) as figure:
        # The image no longer draws the source snapshot and request time; the
        # file keeps them as metadata instead (Administrator decision, #575).
        metadata = {
            "Title": "Family participation",
            "Creator": EMAIL_RENDERER_VERSION if email else RENDERER_VERSION,
            "Description": provenance_text(document),
        }
        figure.savefig(output, format=format, dpi=120, metadata=metadata)
