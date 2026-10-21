"""Render an exact synthetic report with the actual retained-chart UI component."""

from dataclasses import replace
from decimal import Decimal

from django.template.loader import render_to_string

from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_comparison import section
from parishkit.stewardship.reports.digest_presentation import (
    chart_layout,
    snapshot_context,
)
from parishkit.stewardship.reports.digest_views import daily_rows

from ..test_daily_digest_content import document, funnel_metrics
from ..test_digest_comparison import found as comparison_fixture


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
    # A digest with the Compared with past campaigns section (#1018 slice 7):
    # slice 6's invented comparison, with each imported year's checksum.
    compared = replace(value, comparison=section(comparison_fixture()))
    comparison = snapshot_context(
        compared,
        mode="production",
        chart_url="/digest-chart.png",
        download_url="/digest-chart-download.png",
        plot=chart_layout(content.chart.data),
    )
    comparison |= daily_rows(comparison, {})
    # The widest realistic Campaign totals (#1639): 1,234 of 2,345 Families
    # have responded, 1,088 first submissions and over a million dollars of
    # pledges on the report day; and the same day with its pledges unknown.
    wide, unknown = (
        report_day(
            value,
            first_responses=1088,
            cumulative_responses=1234,
            cohort_denominator=2345,
            pledge_total=pledges,
            pledge_available=pledges is not None,
        )
        for pledges in (Decimal("1234567.89"), None)
    )
    pages = {}
    for path, report in (("wide", wide), ("unknown", unknown)):
        totals = snapshot_context(
            report,
            mode="production",
            chart_url="/digest-chart.png",
            download_url="/digest-chart-download.png",
            plot=chart_layout(content.chart.data),
        )
        totals |= daily_rows(totals, {})
        pages[f"/daily-digest-{path}"] = (
            "text/html",
            render_to_string(
                "stewardship/daily-digest.html",
                context | {"admin_chrome": admin} | totals,
            ),
        )
    return pages | {
        "/daily-digest-comparison": (
            "text/html",
            render_to_string(
                "stewardship/daily-digest.html",
                context | {"admin_chrome": admin} | comparison,
            ),
        ),
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


def report_day(value, **changes):
    """``value`` with its report day (the chart's last day) changed."""
    chart = value.participation
    last = replace(chart.days[-1], **changes)
    return replace(value, participation=replace(chart, days=(*chart.days[:-1], last)))
