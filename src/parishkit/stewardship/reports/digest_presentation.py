"""Shared exact-value presentation for the digest's protected snapshot page."""

from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO

from django.urls import reverse
from PIL import Image

from .charts import (
    EMAIL_PLOT_LAYOUT,
    EMAIL_RENDERER_VERSION,
    PLOT_LAYOUT,
    participation_limits,
)
from .daily_digest import PLEDGE_HEADING, participation_row, report_day_cards
from .digest_comparison import data_line
from .digest_funnel import funnel_context


def snapshot_context(
    document, *, mode, chart_url, download_url, plot=PLOT_LAYOUT, funnel_charts=False
):
    """Show the email's exact report-day figures; nothing is recalculated.

    ``funnel_charts`` says the email had the Response funnel's two charts
    (the ready row retained them, #477), so the page draws them too; a
    digest compiled before them, or sent with its charts withheld, shows
    only the funnel's table, as its email did.

    ``plot`` is where the retained chart image draws its plot, for the date
    hit-testing; see ``chart_layout``. The Compared with past campaigns
    section (#1018 slice 7) is the retained one, drawn again from its stored
    chart specifications; the page links the report on the same day.
    """
    chart = document.participation
    section = document.comparison
    return {
        "comparison": section,
        "comparison_line": data_line(section) if section else "",
        "comparison_url": comparison_path(section) if section else "",
        **participation_context(chart, plot=plot),
        "document": document,
        "mode": mode,
        "cards": report_day_cards(document),
        # The response funnel at the end of the report day, counted again
        # from the same durable timestamps the email used (#477), with the
        # email's two charts drawn in the browser when it had them (slice 2).
        **funnel_context(document.funnel, document.mode, charts=funnel_charts),
        "chart_url": chart_url,
        "download_url": download_url,
    }


def comparison_path(section):
    """The Campaign comparison report on the section's day (``?day=N``)."""
    return f"{reverse('admin:campaign_comparison')}?day={int(section['day'])}"


def tooltip_dollars(amount):
    """Round an exact pledge total to whole dollars, halves up: $760,410."""
    return f"${amount.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"


def tooltip_point(day, row):
    """Return one date's short chart tooltip: a date heading and label/value rows.

    The tooltip is deliberately terse (#575): cumulative Families, that day's
    first submissions and, when the table has a pledge column, the pledge total.
    Pledges are whole dollars here, as the Administrator asked; the table, live
    text and slider value text keep the exact cents.
    """
    rows = [
        [
            "Families",
            f"{day.cumulative_responses:,}"
            if day.population_available
            else "Unavailable",
        ],
        ["New today", row[1]],
    ]
    if len(row) > 3:
        rows.append(
            [
                "Pledges",
                tooltip_dollars(day.pledge_total) if day.pledge_available else row[3],
            ]
        )
    return {"date": row[0], "rows": rows}


def chart_layout(chart):
    """Return the plot layout of a retained digest chart from its PNG bytes.

    Digests compiled since #720 carry the email drawing, named in the PNG's
    Creator text; earlier ones carry the Participation page's drawing. The
    retained bytes are never redrawn, so the page hit-tests the drawing it
    actually shows. Unreadable metadata falls back to the older layout.
    """
    try:
        with Image.open(BytesIO(chart)) as image:
            creator = image.text.get("Creator")
    except (OSError, ValueError, SyntaxError):
        creator = None
    return EMAIL_PLOT_LAYOUT if creator == EMAIL_RENDERER_VERSION else PLOT_LAYOUT


def participation_context(chart, *, plot=PLOT_LAYOUT):
    """Shared exact chart/table/hover presentation for live and pinned pages."""
    rows = [
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
    ]
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append(PLEDGE_HEADING)
    labels = [
        chart.scope_label
        + "; "
        + "; ".join(
            f"{label}: {value}" for label, value in zip(headings, row, strict=True)
        )
        for row in rows
    ]
    return {
        "chart": chart,
        "headings": headings,
        "rows": rows,
        "chart_interaction": {
            "labels": labels,
            "points": [
                tooltip_point(day, row)
                for day, row in zip(chart.days, rows, strict=True)
            ],
            "plot": plot,
            "limits": participation_limits(len(labels)),
        },
        "chart_last_index": len(labels) - 1,
    }
