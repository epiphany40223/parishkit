"""The Family timeline as its view renders it, from synthetic records (#477).

Each page is the real template with the Admin chrome, shaped by the view's
own ``page_context`` from the Family-timeline unit test records. Besides the
Administrator's page, the fixtures serve the pages its Testing switch and
When heading fetch at the exact addresses they carry, so the script's fetch is an
ordinary page request and the swapped-in region is the real template output,
plus Staff's reduced view and a page whose Open form is unavailable.
"""

from datetime import timedelta

from django.template.loader import render_to_string

from parishkit.stewardship.reports.family_timeline import Timeline
from parishkit.stewardship.reports.family_timeline_views import (
    page_context,
    timeline_url,
)
from parishkit.stewardship.web.dates import using

from ..test_family_timeline import CAMPAIGN, FAMILY, IDENTITY, TIMELINE
from ..test_response_metrics import START

ADMIN = timeline_url(CAMPAIGN.pk, FAMILY)
TESTING = timeline_url(CAMPAIGN.pk, FAMILY, "testing")
# The addresses the When heading leads to, as the shared table builds them:
# oldest first, then back to newest first.
OLDEST = ADMIN + "?size=all&sort=when"
NEWEST = ADMIN + "?size=all&sort=-when"
STAFF = "/family-timeline-staff"
UNAVAILABLE = "/family-timeline-unavailable"


def render(context, admin, timeline, *, mode="production", **options):
    """One Family timeline page for ``timeline`` in ``mode``."""
    shaped = page_context(
        CAMPAIGN,
        FAMILY,
        IDENTITY,
        mode,
        timeline,
        START + timedelta(days=1),
        **options,
    )
    return render_to_string(
        "stewardship/family-timeline.html", context | {"admin_chrome": admin} | shaped
    )


def components(context, admin):
    """The Administrator's page, its Testing view, Staff's and an unavailable one."""
    pages = {
        ADMIN: (TIMELINE, "production", {"full": True}),
        OLDEST: (
            TIMELINE,
            "production",
            {"full": True, "values": {"size": "all", "sort": "when"}},
        ),
        NEWEST: (
            TIMELINE,
            "production",
            {"full": True, "values": {"size": "all", "sort": "-when"}},
        ),
        # No Testing rehearsal: the empty Testing view.
        TESTING: (None, "testing", {"full": True}),
        STAFF: (Timeline(TIMELINE.summary), "production", {}),
        UNAVAILABLE: (
            TIMELINE,
            "production",
            {"full": True, "testing_codes": True, "family_test_url": ADMIN},
        ),
    }
    # Administrators and Staff both may see Family codes.
    pages = {
        path: (timeline, mode, options | {"show_codes": True})
        for path, (timeline, mode, options) in pages.items()
    }
    responses = {}
    with using("us_long"):
        for path, (timeline, mode, options) in pages.items():
            responses[path] = (
                "text/html",
                render(context, admin, timeline, mode=mode, **options),
            )
    return responses
