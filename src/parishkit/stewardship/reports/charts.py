"""Non-interactive PNG/PDF from the same exact participation document as tables.

Matplotlib access is serialized process-locally, uses no pyplot figure registry,
and resets style under that lock. Rendering is deterministic for the pinned
runtime/fonts and input document, not promised byte-identical across upgrades.

The plot is the same in every file. Its page furniture differs: the page PNG
draws its own titles and footer, the daily email's PNG has none (#720), and
the PDF is a US Letter page in the shared report frame (#926).
"""

from contextlib import contextmanager
from functools import lru_cache
from io import BytesIO
from threading import RLock
from typing import NamedTuple

from parishkit.stewardship.web.dates import current, format_date

from . import pdf_design
from .participation import ParticipationDocument

_RENDER_LOCK = RLock()
# Chart PNGs kept per web process (#905); each is roughly 100 KB.
PAGE_PNG_CACHE_SIZE = 32
RENDERER_VERSION = "participation-v2"
# The daily email's drawing (#720), named in its PNG's Creator text so the
# saved report page can tell which drawing a retained digest holds.
EMAIL_RENDERER_VERSION = "participation-email-v1"
# The PDF's drawing: the plot inside the shared report page frame (#926).
PDF_RENDERER_VERSION = "participation-pdf-v1"
PLOT_LAYOUT = {"left": 0.09, "right": 0.89, "top": 0.74, "bottom": 0.29}
# The daily email's drawing has no page title or footer, so its plot fills
# the image (#720). The saved report page hit-tests whichever one it shows.
EMAIL_PLOT_LAYOUT = {"left": 0.09, "right": 0.89, "top": 0.90, "bottom": 0.17}


class ChartLayout(NamedTuple):
    """Where one drawing puts its plot, legend and note (figure fractions).

    ``furniture`` draws the page PNG's own parish, campaign and title lines
    and its renderer footer; the email has none, and the PDF's shared frame
    draws its own. ``zone_label`` names the campaign time zone on the date
    axis (the email's as-of line names it instead).
    """

    plot: dict
    legend_top: float
    legend_size: float
    legend_frame: bool
    note: tuple[float, float]
    furniture: bool
    zone_label: bool


PAGE_LAYOUT = ChartLayout(PLOT_LAYOUT, 0.805, 8, True, (0.09, 0.055), True, True)
# Without the page title the email's legend sits at the very top.
EMAIL_LAYOUT = ChartLayout(
    EMAIL_PLOT_LAYOUT, 0.99, 11, False, (0.09, 0.055), False, False
)


def pdf_layout(frame):
    """The PDF's chart layout inside the shared page frame's body (#926).

    The legend row sits at the top of the body. Under the plot there is room
    for the rotated date ticks, the axis label and the gap note, which sits
    on the body's last line. The left and right sides are only a first
    guess: ``_fit_to_margins`` then moves them so the value axes' tick
    labels and titles and the first rotated date end exactly at the page
    margins, however wide the numbers are.
    """

    def down(points):
        """A figure fraction for a distance in points from the page top."""
        return 1 - points / pdf_design.PAGE_HEIGHT

    width = pdf_design.PAGE_WIDTH
    return ChartLayout(
        plot={
            "left": (pdf_design.MARGIN + 54) / width,
            "right": (width - pdf_design.MARGIN - 70) / width,
            "top": down(frame.body_top + 34),
            "bottom": down(frame.body_bottom - 78),
        },
        legend_top=down(frame.body_top + 4),
        legend_size=8,
        legend_frame=False,
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


def pdf_frame(document):
    """The shared report page frame for the chart PDF (#926).

    It names the parish, campaign and population scope like the page PNG.
    Unlike the other report PDFs it states no capture or request time: the
    chart's provenance travels in the file's metadata instead (#575).
    """
    return pdf_design.PdfFrame(
        title=f"Family participation — {document.scope_label}",
        eyebrow=f"{document.parish_name} · {document.campaign_name}",
    )


def family_axis_top(days):
    """Return the Family axis top: the largest plotted count plus 10% headroom.

    Both the daily bars and the cumulative line share this axis. Unavailable
    days are gaps and do not count; an empty or all-zero chart still gets a
    top of 1 so the axis is drawable.
    """
    counts = [
        count
        for day in days
        if day.population_available
        for count in (day.first_responses, day.cumulative_responses)
    ]
    return max([1, *counts]) * 1.1


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
def participation_figure(document, *, email=False, pdf=False):
    """Own plotting state until consumption ends; callers cannot leak a figure.

    ``email`` drops the page furniture (parish and campaign names, the scope
    title and the renderer footer) that the daily email already states in its
    text (#720), and lets the plot fill the image (``EMAIL_PLOT_LAYOUT``).
    ``pdf`` draws a US Letter page: the shared report frame around the plot.
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
                frame = pdf_frame(document)
                frame.draw(pdf_design.Canvas(figure), 1, 1)
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
    """Separate units and line patterns; floats are only plot coordinates."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    axes = figure.add_subplot(111)
    figure.subplots_adjust(**layout.plot)
    if layout.furniture:
        figure.text(0.5, 0.96, document.parish_name, ha="center", fontsize=14)
        figure.text(0.5, 0.915, document.campaign_name, ha="center", fontsize=12)
        axes.set_title(f"Family participation — {document.scope_label}", pad=38)
    # The email's as-of line names the zone once (#720); the page names it here.
    axes.set_xlabel(
        f"Campaign date ({document.campaign_timezone})"
        if layout.zone_label
        else "Campaign date"
    )
    axes.set_ylabel("Families (count)")
    # Round 1/2/5 steps (10, 20, 50, ...) read more easily than 30 or 25.
    axes.yaxis.set_major_locator(
        MaxNLocator(integer=True, min_n_ticks=2, steps=[1, 2, 5, 10])
    )
    axes.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f"{value:,.0f}"))
    axes.grid(axis="y", color="#dddddd", linewidth=0.5)
    dates = list(range(len(document.days)))
    daily = [
        day.first_responses if day.population_available else float("nan")
        for day in document.days
    ]
    cumulative = [
        day.cumulative_responses if day.population_available else float("nan")
        for day in document.days
    ]
    axes.bar(
        dates,
        daily,
        color="#9ecae1",
        edgecolor="#26658b",
        hatch="//",
        label="First submissions that day",
        zorder=2,
    )
    axes.plot(
        dates,
        cumulative,
        color="#173d70",
        linestyle="-",
        marker="o",
        markersize=3,
        label="Cumulative participating Families",
        zorder=3,
    )
    # Scale the Family axis to the plotted counts, not to the eligible
    # population (#575): a top at the eligible total made the bars and the
    # cumulative line tiny.
    axes.set_ylim(bottom=0, top=family_axis_top(document.days))
    if dates:
        ticks = sorted(
            {0, len(dates) - 1} | set(range(0, len(dates), max(1, len(dates) // 8)))
        )
        axes.set_xticks(
            ticks,
            [
                format_date(document.days[index].local_date, compact=True)
                for index in ticks
            ],
            rotation=30,
            ha="right",
        )
        axes.set_xlim(*participation_limits(len(dates)))
    else:
        axes.set_xticks([])
        axes.text(
            0.5,
            0.5,
            "No campaign days in this report",
            transform=axes.transAxes,
            ha="center",
        )
    handles, labels = axes.get_legend_handles_labels()
    if document.financial_enabled:
        dollars = axes.twinx()
        dollars.set_ylabel("Effective annual pledges (USD)")
        dollars.yaxis.set_major_formatter(
            FuncFormatter(lambda value, pos: f"${value:,.0f}")
        )
        amounts = [
            float(day.pledge_total) if day.pledge_available else float("nan")
            for day in document.days
        ]
        dollars.plot(
            dates,
            amounts,
            color="#8a4f00",
            linestyle="--",
            marker="s",
            markersize=3,
            label="Effective annual pledges",
        )
        dollars.set_ylim(
            bottom=0,
            top=max(
                [1.0]
                + [
                    float(day.pledge_total)
                    for day in document.days
                    if day.pledge_available
                ]
            )
            * 1.1,
        )
        extra_handles, extra_labels = dollars.get_legend_handles_labels()
        handles += extra_handles
        labels += extra_labels
    figure.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, layout.legend_top),
        ncol=3,
        fontsize=layout.legend_size,
        frameon=layout.legend_frame,
    )
    unavailable = any(
        not day.population_available
        or (document.financial_enabled and not day.pledge_available)
        for day in document.days
    )
    note = "Missing observations are gaps, not zero." if unavailable else ""
    figure.text(*layout.note, note, fontsize=8)
    if layout.furniture:
        figure.text(0.89, 0.035, f"{RENDERER_VERSION} · Page 1", fontsize=7, ha="right")


def provenance_text(document):
    """Return the chart's source and request provenance as one line of text."""
    return " ".join(document.as_of_label.split("\n"))


def render_participation(document, output, *, format, email=False):
    """Write a fixed-layout artifact without host clock or random PDF metadata.

    ``email`` selects the daily email's PNG drawing (see
    ``participation_figure``); a PDF is always the framed report page.
    """
    if format not in {"png", "pdf"}:
        raise ValueError("Participation charts support PNG or PDF.")
    pdf = format == "pdf"
    with participation_figure(document, email=email and not pdf, pdf=pdf) as figure:
        metadata = {
            "Title": "Family participation",
            "Creator": PDF_RENDERER_VERSION
            if pdf
            else EMAIL_RENDERER_VERSION
            if email
            else RENDERER_VERSION,
        }
        # The image no longer draws the source snapshot and request time; the
        # file keeps them as metadata instead (Administrator decision, #575).
        # PNG takes any text key; PDF only the standard Info keys, so Subject.
        provenance = provenance_text(document)
        if format == "png":
            metadata["Description"] = provenance
        if format == "pdf":
            metadata.update(
                {
                    "Subject": provenance,
                    # The logical document originates at its immutable request,
                    # not at a retry's wall-clock render time.
                    "CreationDate": document.requested_at,
                    "ModDate": document.requested_at,
                    "Producer": "ParishKit",
                }
            )
        figure.savefig(output, format=format, dpi=120, metadata=metadata)


def participation_png(document):
    """Return the Participation page's chart PNG, rendered once per exact input.

    The page's chart comes from an immutable fact set, so a repeat view of the
    same chart gets the same bytes from this process's cache without entering
    the render lock: it neither waits behind a PDF rendering in this process
    nor spends GIL time in matplotlib (#905). The frozen document is the key,
    with the active date style because tick labels use it.
    """
    return _cached_participation_png(document, current())


@lru_cache(maxsize=PAGE_PNG_CACHE_SIZE)
def _cached_participation_png(document, date_style):
    """Render one page chart; ``date_style`` only separates cache entries."""
    output = BytesIO()
    render_participation(document, output, format="png")
    return output.getvalue()
