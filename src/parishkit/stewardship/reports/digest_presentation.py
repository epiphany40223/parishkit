"""Shared exact-value presentation for the digest's protected snapshot page."""

from decimal import ROUND_HALF_UP, Decimal

from .charts import PLOT_LAYOUT, participation_limits
from .daily_digest import participation_row, statistics_cards


def snapshot_context(document, *, mode, chart_url, download_url):
    """Use the email's exact formatting without recalculating report statistics."""
    chart = document.participation
    return {
        **participation_context(chart),
        "document": document,
        "mode": mode,
        "cards": statistics_cards(document.statistics),
        "chart_url": chart_url,
        "download_url": download_url,
    }


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


def participation_context(chart):
    """Shared exact chart/table/hover presentation for live and pinned pages."""
    rows = [
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
    ]
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append("Cumulative annual pledges (USD)")
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
            "plot": PLOT_LAYOUT,
            "limits": participation_limits(len(labels)),
        },
        "chart_last_index": len(labels) - 1,
    }
