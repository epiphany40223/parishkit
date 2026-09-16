"""Non-interactive PNG/PDF from the same exact participation document as tables.

Matplotlib access is serialized process-locally, uses no pyplot figure registry,
and resets style under that lock. Rendering is deterministic for the pinned
runtime/fonts and input document, not promised byte-identical across upgrades.
"""

from contextlib import contextmanager
from threading import RLock

from .participation import ParticipationDocument

_RENDER_LOCK = RLock()
RENDERER_VERSION = "participation-v1"


@contextmanager
def participation_figure(document):
    """Own plotting state until consumption ends; callers cannot leak a figure."""
    if not isinstance(document, ParticipationDocument):
        raise TypeError("Rendering requires an immutable participation document.")
    with _RENDER_LOCK:
        import matplotlib as mpl
        from matplotlib.figure import Figure

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
            figure = Figure(figsize=(12, 7), dpi=120, facecolor="white")
            try:
                _draw(figure, document)
                yield figure
            finally:
                figure.clear()


def _draw(figure, document):
    """Separate units and line patterns; floats are only plot coordinates."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    axes = figure.add_subplot(111)
    figure.subplots_adjust(left=0.09, right=0.89, top=0.74, bottom=0.29)
    figure.text(0.5, 0.96, document.parish_name, ha="center", fontsize=14)
    figure.text(0.5, 0.915, document.campaign_name, ha="center", fontsize=12)
    axes.set_title(f"Family participation — {document.scope_label}", pad=38)
    axes.set_xlabel(f"Campaign date ({document.campaign_timezone})")
    axes.set_ylabel("Families (count)")
    axes.yaxis.set_major_locator(MaxNLocator(integer=True, min_n_ticks=2))
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
    axes.set_ylim(
        bottom=0, top=max([1] + [day.cohort_denominator for day in document.days]) * 1.1
    )
    if dates:
        ticks = sorted(
            {0, len(dates) - 1} | set(range(0, len(dates), max(1, len(dates) // 8)))
        )
        axes.set_xticks(
            ticks,
            [document.days[index].local_date.isoformat() for index in ticks],
            rotation=30,
            ha="right",
        )
        axes.set_xlim(-0.6, max(0.6, len(dates) - 0.4))
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
        bbox_to_anchor=(0.5, 0.805),
        ncol=3,
        fontsize=8,
    )
    unavailable = any(
        not day.population_available
        or (document.financial_enabled and not day.pledge_available)
        for day in document.days
    )
    note = "Missing observations are gaps, not zero." if unavailable else ""
    figure.text(0.09, 0.13, document.as_of_label, fontsize=8, va="top")
    figure.text(0.09, 0.055, note, fontsize=8)
    figure.text(0.89, 0.035, f"{RENDERER_VERSION} · Page 1", fontsize=7, ha="right")


def render_participation(document, output, *, format):
    """Write a fixed-layout artifact without host clock or random PDF metadata."""
    if format not in {"png", "pdf"}:
        raise ValueError("Participation charts support PNG or PDF.")
    with participation_figure(document) as figure:
        metadata = {"Title": "Family participation", "Creator": RENDERER_VERSION}
        if format == "pdf":
            metadata.update(
                {
                    # The logical document originates at its immutable request,
                    # not at a retry's wall-clock render time.
                    "CreationDate": document.requested_at,
                    "ModDate": document.requested_at,
                    "Producer": "ParishKit",
                }
            )
        figure.savefig(output, format=format, dpi=120, metadata=metadata)
