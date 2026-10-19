"""Shared exact-value presentation for the digest's protected snapshot page."""

from io import BytesIO

from django.urls import reverse
from django.utils.translation import gettext as _
from PIL import Image

from parishkit.stewardship.web.stat_tiles import UNKNOWN, shown

from .chart_specs import whole_dollars as tooltip_dollars
from .charts import (
    DIGEST_PLOT_LAYOUTS,
    LEGACY_PLOT_LAYOUT,
    PLOT_LAYOUT,
    participation_limits,
)
from .daily_digest import PLEDGE_HEADING, participation_row, report_day_cards
from .digest_comparison import data_line
from .digest_funnel import funnel_context


def snapshot_context(document, *, mode, chart_url, download_url, plot=PLOT_LAYOUT):
    """Show the email's exact report-day figures; nothing is recalculated.

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
        "comparison_tiles": saved_tiles(section) if section else [],
        "comparison_url": comparison_path(section) if section else "",
        **participation_context(chart, plot=plot),
        "document": document,
        "mode": mode,
        "cards": report_day_cards(document),
        # The response funnel at the end of the report day, counted again
        # from the same durable timestamps the email used (#477); no charts
        # (#1370), even for a digest whose email still had them.
        **funnel_context(document.funnel, document.mode),
        "chart_url": chart_url,
        "download_url": download_url,
    }


def saved_tiles(section):
    """The saved Compared with past campaigns figures as stat tiles (#1540).

    The section stores each figure as the email showed it (label, value
    text, comparison lines), so each tile takes that text word for word
    (``shown``); nothing is formatted again. The section stores no reason
    for a figure that read "—", so its toggletip says it was not available.
    """
    missing = _("This figure was not available on the report day.")
    return [
        shown(
            f"figure-{index}",
            figure["label"],
            figure["value"],
            unknown_reason=missing if figure["value"] == UNKNOWN else "",
            details=figure["lines"],
        )
        for index, figure in enumerate(section["tiles"], start=1)
    ]


def comparison_path(section):
    """The Campaign comparison report on the section's day (``?day=N``)."""
    return f"{reverse('admin:campaign_comparison')}?day={int(section['day'])}"


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

    Digests compiled since #720 carry an email drawing, named in the PNG's
    Creator text: the separate-panel one, or the earlier one with a second
    dollar axis. Older ones carry the Participation page's drawing from
    before #720. The retained bytes are never redrawn, so the page hit-tests
    the drawing it actually shows. Unreadable metadata falls back to the
    oldest layout.
    """
    try:
        with Image.open(BytesIO(chart)) as image:
            creator = image.text.get("Creator")
    except (OSError, ValueError, SyntaxError):
        creator = None
    return DIGEST_PLOT_LAYOUTS.get(creator, LEGACY_PLOT_LAYOUT)


def participation_table_context(chart):
    """The exact daily table's rows and headings, as every page shows them.

    The live Response dashboard needs only these: its chart is drawn in the
    browser (#720), so it has no image to hit-test.
    """
    rows = [
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
    ]
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append(PLEDGE_HEADING)
    return {"chart": chart, "headings": headings, "rows": rows}


def participation_context(chart, *, plot=PLOT_LAYOUT):
    """The table plus a retained chart image's hover and slider data.

    A saved digest's page hit-tests the PNG it holds (``digest-v1.js``).
    """
    table = participation_table_context(chart)
    headings, rows = table["headings"], table["rows"]
    labels = [
        chart.scope_label
        + "; "
        + "; ".join(
            f"{label}: {value}" for label, value in zip(headings, row, strict=True)
        )
        for row in rows
    ]
    return {
        **table,
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
