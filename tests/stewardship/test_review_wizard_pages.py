"""Review one at a time's start page as each role sees it (#2397).

Rendered from the browser fixtures' contexts (``review_wizard_components``)
with no database: a Ministry leader's start page has no stream line, a
shorter help and the Sort under Advanced; Staff keep the stream line and
the Sort below the filters (review pipeline spec, "Wizard flow" and
"Wizard filters and sort").
"""

import re
from datetime import UTC, datetime, timedelta

from django.template.loader import render_to_string

from .browser.review_wizard_components import LEADER, STAFF, start_context
from .browser.review_work_components import chrome

LEADER_HELP = (
    "Review the join and leave requests for the Ministries you lead, one "
    "Member at a time, with Previous and Next. Acting on a request is "
    "optional, and you can finish at any time."
)
NOW = datetime(2054, 10, 1, 12, tzinfo=UTC)
# The chrome's session times, as the browser fixtures give them.
ADMIN = {
    "admin": True,
    "server_now": NOW,
    "idle_deadline": NOW + timedelta(hours=1),
    "absolute_deadline": NOW + timedelta(hours=12),
}
STAFF_HELP = "Review one Family or Member at a time, with Previous and Next."
# The Sort control's wrapper, with its attributes.
SORT = re.compile(r'<div([^>]*)><label for="review-sort">Sort</label>')


def start_page(reader, only=None):
    """The start page ``reader`` sees, offered ``only`` that stream or all."""
    configuration, admin_chrome = chrome(ADMIN, reader, {})
    return render_to_string(
        "stewardship/review-wizard-start.html",
        {
            "csrf_token": "a" * 64,
            "admin_chrome": admin_chrome,
            "principal": reader,
            "configuration": configuration,
        }
        | start_context(reader, only=only),
    )


def about(page):
    """The text of the page's About this page help."""
    return re.sub(r"<[^>]+>", "", page.split("about-page", 1)[1].split("</details>")[0])


def test_a_leaders_start_page_has_no_stream_line_and_sorts_under_advanced():
    """A leader reviews one stream: the stream is posted, never named, and
    the Sort shows only while Advanced is chosen; the help names their work."""
    page = start_page(LEADER, only="ministry")
    assert '<input type="hidden" name="stream" value="ministry">' in page
    assert "Stream:" not in page
    assert SORT.search(page)[1] == ' data-show-when="mode=advanced"'
    assert LEADER_HELP in about(page)
    assert STAFF_HELP not in page


def test_staff_keep_the_stream_line_and_the_sort_below_the_filters():
    """Staff offered one stream still see it named; the Sort is shown in
    Basic and Advanced alike, with the full help."""
    page = start_page(STAFF, only="ministry")
    assert "<p>Stream: Ministry requests</p>" in page
    assert SORT.search(page)[1] == ""
    assert STAFF_HELP in about(page)
    assert LEADER_HELP not in page
    # Several streams: the radio group, no stream line, the same Sort.
    page = start_page(STAFF)
    assert 'id="review-stream-census"' in page and "Stream:" not in page
    assert SORT.search(page)[1] == ""
