"""Synthetic exact observations rendered by the production reporting templates."""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.daily_digest import statistics_cards
from parishkit.stewardship.reports.digest_presentation import participation_context
from parishkit.stewardship.reports.workspace import ReportQuery

from ..test_daily_digest_content import document


def components(context, admin):
    """Reuse the existing digest image; production chart/table values stay shared."""
    value = document()
    chart = value.participation
    campaign = SimpleNamespace(
        pk=chart.campaign_id,
        active_configuration=SimpleNamespace(name=chart.campaign_name),
        state="active",
    )
    page = {
        **participation_context(chart),
        "campaign": campaign,
        "picker_url": "/campaigns",
        "campaigns": [
            {"id": campaign.pk, "name": chart.campaign_name, "url": "/participation"}
        ],
        "query": ReportQuery(timezone="America/Los_Angeles"),
        "selection": SimpleNamespace(updating=True),
        "statistics": value.statistics,
        "cards": statistics_cards(value.statistics),
        "timezones": ["UTC", "America/Los_Angeles"],
        "export_key": UUID(int=10),
        "export_allowed": True,
        "row_count": len(chart.days),
        "chart_url": "/digest-chart.png",
    }
    job = SimpleNamespace(
        pk=UUID(int=20),
        format="xlsx",
        requester_id=UUID(int=21),
        parameters={"population_scope": "historical"},
        fact_set=SimpleNamespace(source_generation=3, submission_watermark=4),
        created_at=chart.requested_at,
        browser_timezone="America/Los_Angeles",
    )
    pages = {
        "/participation": ("participation", page),
        "/report-export": (
            "report-export",
            {
                "job": job,
                "status": {"state": "ready"},
                "mutable": True,
                "report_url": "/participation",
            },
        ),
        "/report-export-busy": (
            "report-export-error",
            {"request_id": job.pk, "busy": True},
        ),
    }
    pages["/participation-auto?scope=historical"] = (
        "participation",
        page | {"query": ReportQuery()},
    )
    pages["/participation-auto?scope=historical&timezone=America%2FLos_Angeles"] = (
        "participation",
        page,
    )
    pages["/participation-auto?timezone=UTC"] = (
        "participation",
        page | {"query": ReportQuery()},
    )
    return {
        path: (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html", context | {"admin_chrome": admin} | data
            ),
        )
        for path, (template, data) in pages.items()
    }
