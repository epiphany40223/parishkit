"""The Family timeline as its view renders it, from synthetic records (#477).

Each page is the real template with the Admin chrome, shaped by the view's
own ``page_context`` from the Family-timeline unit test records. Besides the
Administrator's page, the fixtures serve the pages its Testing switch and
When heading fetch at the exact addresses they carry, so the script's fetch is an
ordinary page request and the swapped-in region is the real template output,
plus Staff's reduced view and a page whose Open form is unavailable.
"""

from datetime import timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.family_timeline import Timeline
from parishkit.stewardship.reports.family_timeline_views import (
    page_context,
    timeline_url,
)
from parishkit.stewardship.web.dates import using

from ..test_family_timeline import CAMPAIGN, FAMILY, IDENTITY, TIMELINE
from ..test_response_metrics import START

ADMIN = timeline_url(FAMILY)
TESTING = timeline_url(FAMILY, "testing")
# The addresses the When heading leads to, as the shared table builds them:
# oldest first, then back to newest first.
OLDEST = ADMIN + "?size=all&sort=when"
NEWEST = ADMIN + "?size=all&sort=-when"
STAFF = "/family-timeline-staff"
UNAVAILABLE = "/family-timeline-unavailable"
# A second Family whose campaign has a Testing rehearsal, so the export form
# is drawn in both modes and its mode follows the in-place switch.
REHEARSED = UUID(int=7)
REHEARSED_ADMIN = timeline_url(REHEARSED)
REHEARSED_TESTING = timeline_url(REHEARSED, "testing")
# The export form's values, as the view's ``export_context`` gives them.
EXPORT = {
    "url": "/family-timeline-export",
    "request_key": UUID(int=99),
    "timezones": ["America/Los_Angeles", "America/New_York", "UTC"],
    "mutable": True,
}


def render(context, admin, timeline, *, mode="production", family=FAMILY, **options):
    """One Family timeline page for ``timeline`` in ``mode``."""
    shaped = page_context(
        CAMPAIGN,
        family,
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
        REHEARSED_ADMIN: (TIMELINE, "production", {"full": True, "family": REHEARSED}),
        REHEARSED_TESTING: (TIMELINE, "testing", {"full": True, "family": REHEARSED}),
    }
    # Administrators and Staff both may see Family codes.
    pages = {
        path: (timeline, mode, options | {"show_codes": True, "export": EXPORT})
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
