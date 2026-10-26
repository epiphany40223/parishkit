"""The Family hub Timeline names the Admin user who acted (#2259).

A review line names its reviewer by their Users-page Name, linked to the
user's Timeline for an Administrator; Staff, who may not open users'
Timelines, see the same words with no link. An address that is no current
user is shown as it is and an account the portal no longer has keeps its
wording, neither linked.
"""

import pytest

from .test_charts import assert_clean, watch
from .test_timelines import AXE
from .timeline_components import ACTOR_LINK, ACTORS_ADMIN, ACTORS_STAFF
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def row(page, what):
    """The Timeline row whose What happened is ``what``."""
    return page.locator("#table tbody tr", has_text=what)


def test_an_administrator_sees_the_reviewers_name_linked(page, component_origin):
    """Name plus link for a current user; the address and the gone account
    stay unlinked; the Name's link opens that user's Timeline."""
    errors = watch(page)
    page.goto(component_origin + ACTORS_ADMIN)
    accepted = row(page, "Accepted")
    visible(accepted)
    assert "Email address; by Administrator, IT" in accepted.inner_text()
    link = accepted.get_by_role("link", name="Administrator, IT")
    assert link.get_attribute("href") == ACTOR_LINK
    assert "itadmin@example.org" not in accepted.inner_text()
    ignored = row(page, "Ignored")
    assert "Home phone; by old.reviewer@example.org" in ignored.inner_text()
    assert ignored.locator("td a").count() == 0
    handled = row(page, "Handled")
    assert "Additional information; by Former portal user" in handled.inner_text()
    assert handled.locator("td a").count() == 0
    assert_clean(page, errors)


def test_staff_see_the_name_with_no_link(page, component_origin):
    """A reader who may not open users' Timelines gets no link to refuse."""
    errors = watch(page)
    page.goto(component_origin + ACTORS_STAFF)
    accepted = row(page, "Accepted")
    visible(accepted)
    assert "Email address; by Administrator, IT" in accepted.inner_text()
    assert page.locator("#table a[href*='/admin/users/']").count() == 0
    assert_clean(page, errors)


@pytest.mark.parametrize("path", [ACTORS_ADMIN, ACTORS_STAFF])
def test_the_named_lines_are_accessible_and_fit_a_320px_phone(
    page, component_origin, axe_source, path
):
    """axe finds nothing, and at 320 px the page never scrolls sideways."""
    errors = watch(page)
    page.goto(component_origin + path)
    visible(row(page, "Accepted"))
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 320, "height": 720})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
