"""Shared exact-value presentation for the digest's protected snapshot page."""

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
            "plot": PLOT_LAYOUT,
            "limits": participation_limits(len(labels)),
        },
        "chart_last_index": len(labels) - 1,
    }
