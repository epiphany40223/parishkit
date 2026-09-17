"""Shared exact-value presentation for the digest's protected snapshot page."""

from .charts import PLOT_LAYOUT, participation_limits
from .daily_digest import participation_row, statistics_cards


def snapshot_context(document, *, mode, chart_url, download_url):
    """Use the email's exact formatting without recalculating report statistics."""
    chart = document.participation
    rows = [
        participation_row(day, financial_enabled=chart.financial_enabled)
        for day in chart.days
    ]
    headings = ["Campaign date", "First submissions", "Cumulative participation"]
    if chart.financial_enabled:
        headings.append("Cumulative annual pledges (USD)")
    labels = [
        "; ".join(
            f"{label}: {value}" for label, value in zip(headings, row, strict=True)
        )
        for row in rows
    ]
    return {
        "document": document,
        "chart": chart,
        "mode": mode,
        "cards": statistics_cards(document.statistics),
        "headings": headings,
        "rows": rows,
        "chart_interaction": {
            "labels": labels,
            "plot": PLOT_LAYOUT,
            "limits": participation_limits(len(labels)),
        },
        "chart_last_index": len(labels) - 1,
        "chart_url": chart_url,
        "download_url": download_url,
    }
