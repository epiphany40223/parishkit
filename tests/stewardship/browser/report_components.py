"""Synthetic exact observations rendered by the production reporting templates."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.jobs.queue_wait import QueueWait
from parishkit.stewardship.reports.chart_specs import participation_chart
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.reports.participation import ParticipationDay
from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.reports.workspace_views import (
    figures_status,
    scope_links,
    statistics_panel,
)

from ..test_daily_digest_content import document
from ..test_response_dashboard import page_context
from .automation_components import canonical

# The Response dashboard's real address (#1537), where its choices lead.
REAL = reverse("admin:response_dashboard")


# The figures status read's fixed answers (#1586, test_report_figures.py).
FIGURES_PENDING = "/figures-status-pending"
FIGURES_NEWER = "/figures-status-newer"
# The Response dashboard that could not read its figures (#1586), and its
# slow figures status's answers: still nothing newer, then figures ready.
UNAVAILABLE = "/participation-unavailable"
FIGURES_RETRY = "/figures-status-retry"
FIGURES_RETRY_NEWER = "/figures-status-retry-newer"


# The dashboard opened oldest first, and its Families counted answer from
# there (#2324): that answer is slow, so a test can see the working spinner
# beside the choice while the read is under way.
SORTED = ReportQuery(timezone="America/Los_Angeles", sort="date_asc")
SORTED_CURRENT = replace(SORTED, scope="current")


# Answered after a pause (see the server in conftest.py): applying this
# browser's zone to the report opened at ?scope=historical, so a test can
# follow a daily-table link while it is still in flight, and the sorted
# page's Families counted answer.
SLOW_GETS = {
    canonical(REAL + "?scope=historical&timezone=America%2FLos_Angeles"),
    canonical(SORTED_CURRENT.url()),
}


def long_campaign(chart):
    """``chart`` over 75 campaign dates, pledges past a million dollars, and
    one unavailable date (the 20th), so the daily chart thins its date labels,
    shortens its dollar ticks and breaks its lines at the gap (#720)."""
    days, total = [], 0
    for index in range(75):
        when = chart.first_date + timedelta(days=index)
        if index == 19:
            days.append(ParticipationDay(when, 0, 0, 0, None, None, False, False, None))
            continue
        first = (index * 7) % 40
        total += first
        days.append(
            ParticipationDay(
                when,
                first,
                total,
                2000,
                3,
                chart.source_as_of,
                True,
                True,
                Decimal(total * 2311),
            )
        )
    return replace(chart, last_date=days[-1].local_date, days=tuple(days))


def options(query):
    """The query string the Families counted segment for ``query``'s scope
    follows (#1535), from a page drawn for ``query``."""
    return urlsplit(scope_links(query)[query.scope]).query


def viewing(query, *, testing=False, **changes):
    """The Response dashboard's context drawn for ``query`` (#1537), its
    daily chart updating, with ``changes`` applied. ``testing`` draws a site
    in Testing mode with a rehearsal (#2324)."""
    shaped = page_context(query, testing=testing)
    if not testing:
        shaped["selection"] = SimpleNamespace(updating=True, expected=True)
        # The chart is Updating: its figures status (#1586) is watched fast.
        shaped |= figures_status(
            "dashboard", query.scope, document().participation.fact_set_id, True
        )
    return shaped | changes


def components(context, admin):
    """The production reporting templates over the digest test document's values."""
    value = document()
    chart = value.participation
    page = viewing(ReportQuery(timezone="America/Los_Angeles"))
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
        "/participation": ("response-dashboard", page),
        # No ParishSoft data yet (#1540): every statistics tile reads "—"
        # with its reason.
        "/participation-unknown": (
            "response-dashboard",
            page
            | statistics_panel(
                replace(
                    value.statistics,
                    active=None,
                    cohort=None,
                    comparison_pledge_all=MoneyAmount(None),
                ),
                "historical",
            ),
        ),
        "/participation-long": (
            "response-dashboard",
            page | {"daily_chart": participation_chart(long_campaign(chart))},
        ),
        "/report-export": (
            "report-export",
            {
                "job": job,
                "status": {"state": "ready"},
                "mutable": True,
                "report_url": "/participation",
                "report_name": "Participation",
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
            "report_name": "Participation",
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
            "report_name": "Participation",
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
            "report_name": "Participation",
        },
    )
    # An ended export answers the fixed "no longer available" page (#1398).
    pages["/report-export-expired"] = (
        "report-export-gone",
        {"report_url": "/participation", "report_name": "Participation"},
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
            "report_name": "Participation",
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
    fallback = viewing(implicit)
    los_angeles = ReportQuery(timezone="America/Los_Angeles")
    current = ReportQuery(scope="current", timezone="America/Los_Angeles")
    # The current-population answer's statistics differ from the first page's,
    # so a test can see that the statistics panel was swapped in place.
    # Its responded count differs too, so the progress chart is redrawn (#720).
    refreshed = replace(
        value.statistics,
        comparison_pledge_all=MoneyAmount(98265700),
        active=replace(value.statistics.active, responses=450),
        cohort=replace(value.statistics.cohort, responses=450),
    )
    pages[REAL + "?timezone=UTC"] = ("response-dashboard", viewing(ReportQuery()))
    for query in ("", "?sort=date_asc", "?scope=historical"):
        pages[REAL + query] = ("response-dashboard", fallback)
        zoned = (query + "&" if query else "?") + "timezone=America%2FLos_Angeles"
        pages[canonical(REAL + zoned)] = ("response-dashboard", page)
    pages[canonical(REAL + "?" + options(los_angeles))] = ("response-dashboard", page)
    # The same page once newer figures are ready (test_report_figures.py):
    # no longer Updating, with the current-population answer's statistics.
    pages["/participation-later"] = (
        "response-dashboard",
        page
        | statistics_panel(refreshed, "historical")
        | figures_status("dashboard", "historical", UUID(int=50), False),
    )
    # The page that could not read its figures (#1586): Families by stage
    # and the unavailable line, its figures status beside it at the slow
    # retry pace; its status answers, and the same page's re-read once
    # figures are ready (the later page above).
    pages[UNAVAILABLE] = (
        "response-dashboard",
        viewing(
            ReportQuery(timezone="America/Los_Angeles"),
            participation_unavailable=True,
            participation_cause="facts",
            **figures_status("dashboard", "historical", None, False, retry=True),
        ),
    )
    pages[FIGURES_RETRY] = (
        "report-figures-status",
        figures_status("dashboard", "historical", None, False, retry=True),
    )
    pages[FIGURES_RETRY_NEWER] = (
        "report-figures-status",
        figures_status("dashboard", "historical", None, False, newer=True, retry=True),
    )
    # The status read's answers: still Updating, and newer figures ready.
    pages[FIGURES_PENDING] = (
        "report-figures-status",
        figures_status("dashboard", "historical", chart.fact_set_id, True),
    )
    pages[FIGURES_NEWER] = (
        "report-figures-status",
        figures_status("dashboard", "historical", chart.fact_set_id, False, newer=True),
    )
    pages[canonical(REAL + "?" + options(current))] = (
        "response-dashboard",
        viewing(current, **statistics_panel(refreshed, "current")),
    )
    # The page's own address on its first page.
    address = urlsplit(los_angeles.url(page=1)).query
    pages[canonical(REAL + "?" + address)] = (
        "response-dashboard",
        viewing(los_angeles),
    )
    # The sorted page and its slow Families counted answer (SLOW_GETS).
    pages[canonical(SORTED.url())] = ("response-dashboard", viewing(SORTED))
    pages[canonical(SORTED_CURRENT.url())] = (
        "response-dashboard",
        viewing(SORTED_CURRENT, **statistics_panel(refreshed, "current")),
    )
    # Every page's own figures status read (#1586) answers that the chart
    # shown is still the newest and is still Updating, so the watched region
    # stays as it is in every test that does not route it.
    for _template, data in list(pages.values()):
        status = data.get("figures_status")
        if status is not None and status["updating"]:
            pages.setdefault(canonical(status["url"]), ("report-figures-status", data))
    return {
        path: (
            "text/html",
            render_to_string(
                f"stewardship/{template}.html", context | {"admin_chrome": admin} | data
            ),
        )
        for path, (template, data) in pages.items()
    }
