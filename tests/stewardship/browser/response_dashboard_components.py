"""The response dashboard as its view renders it, from synthetic metrics (#477).

The page is the real template with the Admin chrome, shaped by the view's own
``page_context`` from the response-metrics test rows plus one Family that
follows its link three hours later, so the activity chart has quiet hours to
draw as zero.
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string

from parishkit.stewardship.reports.response_dashboard import (
    DashboardQuery,
    page_context,
)
from parishkit.stewardship.reports.response_metrics import (
    activity_series,
    stage_counts,
)
from parishkit.stewardship.web.dates import using

from ..test_chart_specs import NEW_YORK, metrics
from ..test_response_metrics import ROWS, START

CAMPAIGN = SimpleNamespace(
    pk=UUID(int=477), active_configuration=SimpleNamespace(name="Sample campaign")
)
# The dashboard's own address, as its links name it.
PATH = DashboardQuery().url()


def dashboard_metrics():
    """The test metrics with a later link follow, so some hours are quiet."""
    later = replace(
        ROWS[2], family_id=uuid4(), family_duid=9, link_at=START + timedelta(hours=3)
    )
    rows = (*ROWS, later)
    return replace(
        metrics(),
        families=rows,
        stages=stage_counts(rows),
        activity=activity_series(rows, ZoneInfo(NEW_YORK), "hour"),
    )


def components(context, admin):
    """The Production page, the empty Testing view and two empty campaigns.

    ``PATH`` and its ``grain`` and ``mode`` variants serve the in-place
    switching tests.
    """
    pages = {
        "/response-dashboard": (DashboardQuery(), dashboard_metrics()),
        "/response-dashboard-testing": (DashboardQuery("testing"), None),
        # A campaign with nothing yet: no Families' activity, with and
        # without send markers.
        "/response-dashboard-empty": (
            DashboardQuery(),
            metrics(families=(), sends=False),
        ),
        "/response-dashboard-empty-sends": (DashboardQuery(), metrics(families=())),
    }
    # The page at its real address and the addresses its mode and grain links
    # lead to, so the in-place refresh fetches what the view would serve.
    for query in (
        DashboardQuery(),
        DashboardQuery(grain="hour"),
        DashboardQuery(grain="day"),
        DashboardQuery("testing"),
        DashboardQuery("testing", "day"),
    ):
        value = None if query.mode == "testing" else dashboard_metrics()
        pages[query.url()] = (query, value)
    responses = {}
    with using("us_long"):
        for path, (query, value) in pages.items():
            shaped = page_context(CAMPAIGN, query, value, can_test=True)
            responses[path] = (
                "text/html",
                render_to_string(
                    "stewardship/response-dashboard.html",
                    context | {"admin_chrome": admin} | shaped,
                ),
            )
    return responses
