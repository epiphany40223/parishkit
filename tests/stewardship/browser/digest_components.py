"""Render an exact synthetic report with the actual retained-chart UI component."""

from django.template.loader import render_to_string

from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_presentation import snapshot_context

from ..test_daily_digest_content import document


def components(context, admin):
    """Synthetic immutable inputs need no database, provider or private credentials."""
    value = document()
    content = render_daily_digest(value, public_origin="https://parish.example")
    page = snapshot_context(
        value,
        mode="testing",
        chart_url="/digest-chart.png",
        download_url="/digest-chart-download.png",
    )
    return {
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
