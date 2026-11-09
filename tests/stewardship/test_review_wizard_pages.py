"""Review one at a time's start page by role, and its pages' headings (#2397).

Rendered from the browser fixtures' contexts (``review_wizard_components``)
with no database: a Ministry leader's start page has no stream line, a
shorter help and the Sort under Advanced; Staff keep the stream line and
the Sort below the filters (review pipeline spec, "Wizard flow" and
"Wizard filters and sort"). A run page's heading names the subject it
decides: the Family on a Family page, the Member on a Member page with
the Family on the smaller line under it ("Wizard pages").
"""

import re
from datetime import UTC, datetime, timedelta

from django.template.loader import render_to_string

from .browser.review_wizard_components import (
    LEADER,
    STAFF,
    page_context,
    start_context,
)
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


def run_page(position, **values):
    """Page ``position`` of the fixtures' run (1: Family, 2: Member, 3: New
    Member), as Staff see it, with ``values`` over its context."""
    configuration, admin_chrome = chrome(ADMIN, STAFF, {})
    return render_to_string(
        "stewardship/review-wizard-page.html",
        {
            "csrf_token": "a" * 64,
            "admin_chrome": admin_chrome,
            "principal": STAFF,
            "configuration": configuration,
        }
        | page_context(position)
        | values,
    )


def header(page):
    """The page's heading and the line under it, as markup."""
    heading = re.search(r"<h1 [^>]*data-review-heading>(.*?)</h1>", page, re.S)[1]
    return heading, re.search(r"data-review-family>(.*?)</p>", page, re.S)[1]


def text(markup):
    """Markup's text, tags removed."""
    return re.sub(r"<[^>]+>", "", markup)


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


def test_a_family_page_is_headed_by_the_family():
    """A Family page's heading is the Family line; the line under it is
    drawn empty. (The fixtures have no signed-in request, so the Family
    name is plain text; the PostgreSQL tests check its hub link.)"""
    heading, second = header(run_page(1))
    assert text(heading) == "Family: Squyres, Jeff and Tracy (DUID 4123, envelope 312)"
    assert second == ""


def test_a_member_page_is_headed_by_the_member_then_the_family():
    """A Member page's heading is the Member (their name opening the hub's
    tab for Staff); the Family follows on the smaller line under it."""
    heading, second = header(run_page(2))
    assert text(heading) == "Member: Squyres, Jeff (DUID 77001)"
    assert '<a href="/admin/reports/families/' in heading and "?tab=member-2" in heading
    assert text(second) == "Family: Squyres, Jeff and Tracy (DUID 4123)"
    # A New Member: the hub's badge in the heading, and no DUID yet.
    heading, second = header(run_page(3))
    assert text(heading) == "Member: Squyres, Jeff New Member"
    assert '<span class="status-badge">New Member</span>' in heading
    assert text(second) == "Family: Squyres, Jeff and Tracy (DUID 4123)"


def test_a_member_page_without_the_hub_names_the_member_in_plain_text():
    """Without the hub (a Ministry leader's page), the Member's name is not
    a link."""
    heading, second = header(run_page(2, member_url=None))
    assert "<a " not in heading
    assert text(heading) == "Member: Squyres, Jeff (DUID 77001)"
    assert text(second).startswith("Family: ")


def test_a_subject_no_longer_listed_names_no_one():
    """A page whose subject is no longer listed names neither line."""
    heading, second = header(run_page(2, listed=False, heading=None))
    assert heading == "No longer listed" and second == ""
