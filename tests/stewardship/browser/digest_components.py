"""Render an exact synthetic report with the actual retained-chart UI component."""

from dataclasses import replace

from django.template.loader import render_to_string

from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_presentation import (
    chart_layout,
    snapshot_context,
)
from parishkit.stewardship.reports.digest_views import daily_rows

from ..test_daily_digest_content import document, funnel_metrics


def components(context, admin):
    """Synthetic immutable inputs need no database, provider or private credentials."""
    value = document()
    content = render_daily_digest(value, public_origin="https://parish.example")
    page = snapshot_context(
        value,
        mode="testing",
        chart_url="/digest-chart.png",
        download_url="/digest-chart-download.png",
        # The page hit-tests the drawing the retained digest holds (#720).
        plot=chart_layout(content.chart.data),
    )
    page |= daily_rows(page, {})
    # A Production digest with its response funnel (#477).
    production = replace(value, funnel=funnel_metrics(), mode="production")
    funnel = snapshot_context(
        production,
        mode="production",
        chart_url="/digest-chart.png",
        download_url="/digest-chart-download.png",
        plot=chart_layout(content.chart.data),
    )
    funnel |= daily_rows(funnel, {})
    return {
        "/daily-digest-funnel": (
            "text/html",
            render_to_string(
                "stewardship/daily-digest.html",
                context | {"admin_chrome": admin} | funnel,
            ),
        ),
        "/daily-digest": (
            "text/html",
            render_to_string(
                "stewardship/daily-digest.html",
                context | {"admin_chrome": admin} | page,
            ),
        ),
        "/digest-chart.png": ("image/png", content.chart.data),
        "/digest-chart-download.png": ("image/png", content.chart.data),
    }
