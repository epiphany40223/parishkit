"""Synthetic exact observations rendered by the production reporting templates."""

from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.jobs.queue_wait import QueueWait
from parishkit.stewardship.reports.daily_digest import statistics_cards
from parishkit.stewardship.reports.digest_presentation import participation_context
from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.reports.workspace_views import daily_table

from ..test_daily_digest_content import document
from .automation_components import canonical

# The participation report's real address (its options' form posts there).
REAL = f"/admin/reports/{document().participation.campaign_id}/participation/"


# Answered after a pause (see the server in conftest.py): applying this
# browser's zone to the report opened at ?scope=historical, so a test can
# follow a daily-table link while it is still in flight.
SLOW_GETS = {canonical(REAL + "?scope=historical&timezone=America%2FLos_Angeles")}


def options(query):
    """The query string Apply report options sends for ``query``."""
    fields = {"scope": query.scope, "timezone": query.timezone}
    if query.inactive:
        fields["inactive"] = "yes"
    fields |= {"size": query.size, "sort": query.sort}
    return urlencode(fields)


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
        "campaigns": [
            {"id": campaign.pk, "name": chart.campaign_name, "url": "/participation"}
        ],
        "query": ReportQuery(timezone="America/Los_Angeles"),
        "selection": SimpleNamespace(updating=True, expected=True),
        "statistics": value.statistics,
        "cards": statistics_cards(value.statistics),
        "timezones": ["UTC", "America/Los_Angeles"],
        "export_key": UUID(int=10),
        "exact_key": UUID(int=11),
        "exact_row_count": len(chart.days),
        "export_allowed": True,
        "row_count": len(chart.days),
        **daily_table(
            chart,
            participation_context(chart),
            ReportQuery(timezone="America/Los_Angeles"),
        ),
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
    pages["/report-export-pending"] = (
        "report-export",
        {
            "job": job,
            "status": {"state": "queued"},
            "mutable": True,
            "can_cancel": True,
            "report_url": "/participation",
        },
    )
    # Queued behind a named task (#340): its start shows as a clock time and
    # the time queued ticks forward.
    pages["/report-export-waiting"] = (
        "report-export",
        {
            "job": job,
            "status": {"state": "queued"},
            "wait": QueueWait(
                chart.requested_at,
                "3 minutes ago",
                "the ParishSoft update",
                chart.requested_at,
            ),
            "mutable": True,
            "can_cancel": True,
            "report_url": "/participation",
        },
    )
    pages["/report-export-failed"] = (
        "report-export",
        {
            "job": job,
            "status": {"state": "failed"},
            "mutable": True,
            "retry_key": UUID(int=31),
            "report_url": "/participation",
        },
    )
    pages["/report-export-expired"] = (
        "report-export",
        {
            "job": job,
            "status": {"state": "expired"},
            "mutable": True,
            "retry_key": UUID(int=30),
            "report_url": "/participation",
        },
    )
    pages["/report-exact"] = (
        "report-exact",
        {
            "job": SimpleNamespace(
                pk=UUID(int=40),
                format="xlsx",
                population_scope="historical",
                submission_watermark=4,
                through_date=chart.days[-1].local_date,
                timezone_configuration=SimpleNamespace(
                    timezone=chart.campaign_timezone
                ),
                browser_timezone="America/Los_Angeles",
                created_at=chart.requested_at,
            ),
            "status": {"state": "queued"},
            "source": {"generation": 3, "promoted_at": chart.source_as_of},
            "can_cancel": True,
            "mutable": True,
            "report_url": "/participation",
        },
    )
    # The report at its real address, for the in-place options (#519 PR 5).
    # Opened without a zone, it shows the server's UTC fallback, and its
    # links carry no zone (as the view renders them), until report-v1.js
    # loads the same address with this browser's zone in place. Also: an
    # explicit UTC, this browser's zone, and the answers the options and
    # the zone's application ask for, served under their query's pairs in
    # any order (the fixture server's canonical lookup).
    implicit = ReportQuery(timezone_explicit=False)
    fallback = page | {
        "query": implicit,
        **daily_table(chart, participation_context(chart), implicit),
    }
    los_angeles = ReportQuery(timezone="America/Los_Angeles")
    current = ReportQuery(
        scope="current", timezone="America/Los_Angeles", inactive=True
    )
    pages[REAL + "?timezone=UTC"] = ("participation", page | {"query": ReportQuery()})
    for query in ("", "?sort=date_desc", "?scope=historical"):
        pages[REAL + query] = ("participation", fallback)
        zoned = (query + "&" if query else "?") + "timezone=America%2FLos_Angeles"
        pages[canonical(REAL + zoned)] = ("participation", page)
    pages[canonical(REAL + "?" + options(los_angeles))] = ("participation", page)
    pages[canonical(REAL + "?" + options(current))] = (
        "participation",
        page
        | {
            "query": current,
            "inactive_cards": [("Inactive Families", "2")],
            **daily_table(chart, participation_context(chart), current),
        },
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
