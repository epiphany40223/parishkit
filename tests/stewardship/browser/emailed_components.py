"""Emailed reports (ADM-12.17) with a long daily list, with and without its schedule.

Synthetic rows only: the page shows identities and dates, never report data.
Sixty daily reports, the most the page lists, fill more than a screen, so the
browser tests can show Send a weekly report now stays at the top (#775).
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from django.template.loader import render_to_string

SHOWN = 60
START = date(2054, 10, 1)


def _daily():
    """The newest-first daily reports, one per day."""
    return [
        {
            "pk": UUID(int=index + 1),
            "through_date": START + timedelta(days=SHOWN - index),
            "covered_dates": [START + timedelta(days=SHOWN - index)],
            "observed_at": datetime(2054, 10, 2, 12, tzinfo=UTC)
            + timedelta(days=SHOWN - index),
        }
        for index in range(SHOWN)
    ]


def components(context, admin):
    """Emailed reports for an Administrator, with and without a weekly schedule."""
    page = {
        "campaign_id": UUID(int=999),
        "administrator": True,
        "daily": _daily(),
        "weekly": [],
        "requested": None,
        "shown": SHOWN,
    }
    return {
        path: (
            "text/html",
            render_to_string(
                "stewardship/emailed-reports.html",
                context | {"admin_chrome": admin} | page | {"weekly_schedule": ready},
            ),
        )
        for path, ready in (
            ("/emailed-reports", True),
            ("/emailed-reports-no-schedule", False),
        )
    }
