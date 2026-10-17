"""The Response dashboard as its view renders it, from synthetic values (#477, #1537).

The page is the real template with the Admin chrome, shaped as the view
shapes it (``test_response_dashboard.page_context``) from the
response-metrics test rows plus one Family that follows its link three hours
later, and the digest test document's statistics and daily chart. Its real
address and the addresses its choices lead to are served by
``report_components``.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string

from parishkit.stewardship.reports.response_metrics import (
    activity_series,
    stage_counts,
)
from parishkit.stewardship.reports.workspace import ReportQuery
from parishkit.stewardship.web.dates import using

from ..test_chart_specs import NEW_YORK, metrics
from ..test_response_dashboard import page_context
from ..test_response_metrics import ROWS, START


def dashboard_metrics():
    """The test metrics with one more link follow (6 for 5 invited)."""
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
    """The Production page, the Testing views and a campaign with nothing yet."""
    # Drawn in the test browser's zone, so report-v1.js has no zone to
    # apply and the page stays at its fixture address.
    production = ReportQuery(timezone="America/Los_Angeles")
    testing = ReportQuery(timezone="America/Los_Angeles", mode="testing")
    pages = {
        "/response-dashboard": (production, dashboard_metrics()),
        # The Testing view with no rehearsal, and during one.
        "/response-dashboard-testing": (testing, None),
        "/response-dashboard-rehearsal": (testing, dashboard_metrics()),
        # A campaign with nothing invited or followed yet.
        "/response-dashboard-empty": (production, metrics(families=(), sends=False)),
    }
    responses = {}
    with using("us_long"):
        for path, (query, value) in pages.items():
            responses[path] = (
                "text/html",
                render_to_string(
                    "stewardship/response-dashboard.html",
                    context | {"admin_chrome": admin} | page_context(query, value),
                ),
            )
    return responses
