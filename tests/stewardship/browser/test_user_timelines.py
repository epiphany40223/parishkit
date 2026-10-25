"""A user's Timeline: accessible, paged and filtered in place (#1866).

The fixtures (``user_timeline_components``) serve the first page of a
Staff user's two-page timeline and the exact pages its navigator, When
heading and Kind filter (Role changes and Pages viewed) lead to.
"""

import pytest

from .automation_components import canonical
from .test_charts import assert_clean, watch
from .test_timelines import (
    assert_each_when_is_one_line_and_the_page_never_scrolls_sideways,
)
from .user_timeline_components import NEXT, OLDEST, PAGE, ROLES, VIEWS
from .waits import (
    focused,
    has_attribute,
    has_count,
    has_text,
    has_value,
    same_place,
    visible,
)

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact, nodes}) => ({
  id, impact, html: nodes.map(node => node.html.slice(0, 300))
}))"""
# A mark outside every region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
# The filter bar's fields, which share one row on a desktop.
FIELDS = "#table-filters > div, #table-filters > button"


def lines(page):
    """The timeline's What happened words, in row order."""
    return [
        cell.inner_text() for cell in page.locator("#table tbody th[scope=row]").all()
    ]


def test_the_page_is_accessible_and_fits_a_phone(page, component_origin, axe_source):
    """Help closed, data first, axe finds nothing, no sideways scroll."""
    errors = watch(page)
    page.goto(component_origin + PAGE)
    assert not page.locator("details.about-page").evaluate("node => node.open")
    visible(page.get_by_role("heading", name="Adams, Ann", exact=True))
    assert "Timeline" in page.title() and "Adams" not in page.title()
    visible(page.get_by_role("heading", name="Summary"))
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)


def test_each_when_is_one_line_and_the_page_never_scrolls_sideways(
    page, component_origin
):
    """A user's When values stay on one line, as a Family's do (#2175)."""
    assert_each_when_is_one_line_and_the_page_never_scrolls_sideways(
        page, component_origin + PAGE
    )


def test_the_filter_bar_is_one_row_on_a_desktop(page, component_origin):
    """Kind, From, Through and the filter buttons share one row."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    bottoms = page.locator(FIELDS).evaluate_all(
        "nodes => nodes.map(node => Math.round(node.getBoundingClientRect().bottom))"
    )
    assert len(bottoms) == 4 and max(bottoms) - min(bottoms) <= 4, bottoms


def test_next_page_and_the_when_heading_act_in_place(page, component_origin):
    """The navigator and the sort swap the table region without a reload."""
    errors = watch(page)
    page.goto(component_origin + PAGE)
    has_text(page.locator("#table [data-page-position]").first, "Page 1 of 2")
    assert len(lines(page)) == 50
    page.evaluate(MARK)
    with page.expect_request(
        lambda request: "page=2" in request.url and request.method == "GET"
    ) as fetched:
        page.locator("#table [data-table-next]").first.click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    has_text(page.locator("#table [data-page-position]").first, "Page 2 of 2")
    assert len(lines(page)) == 12
    assert page.evaluate(MARKED) == "kept"
    assert canonical(page.url.removeprefix(component_origin).split("#")[0]) == (
        canonical(NEXT)
    )
    heading = page.get_by_role("link", name="When (sort ascending)")
    heading.scroll_into_view_if_needed()
    heading.click()
    when = page.locator("#table th[data-sort-column='when']")
    has_attribute(when, "aria-sort", "ascending")
    assert lines(page)[0] == "Became a user"
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + OLDEST + "#table"
    assert_clean(page, errors)


def test_the_kind_filter_narrows_the_lines_in_place(page, component_origin):
    """Apply filters swaps the table in place; nothing above it moves."""
    errors = watch(page)
    page.set_viewport_size({"width": 1280, "height": 2400})
    page.goto(component_origin + PAGE)
    page.evaluate(MARK)
    has_value(page.locator('#table-filters input[name="zone"]'), "America/Los_Angeles")
    page.locator("#timeline-kind").select_option("roles")
    button = page.get_by_role("button", name="Apply filters", exact=True)
    before = button.bounding_box()
    scrolled = page.evaluate("window.scrollY")
    with page.expect_response(
        lambda response: (
            "kind=roles" in response.url and response.request.method == "GET"
        )
    ) as fetched:
        button.click()
    assert fetched.value.status == 200, fetched.value.url
    has_count(page.locator("#table tbody tr"), 1)
    assert lines(page) == ["Became a user"]
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("window.scrollY") == scrolled
    assert same_place(button.bounding_box(), before)
    focused(button)
    address = page.url.removeprefix(component_origin).split("#")[0]
    assert canonical(address) == ROLES
    # The summary is not filtered.
    assert (
        "Daily Administrator report"
        in page.locator("#summary-heading + dl").inner_text()
    )
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1280])
def test_pages_viewed_lists_only_page_views_in_place(page, component_origin, width):
    """All leaves the page view out; Pages viewed shows only it, in place (#2006)."""
    errors = watch(page)
    page.set_viewport_size({"width": width, "height": 2400})
    page.goto(component_origin + PAGE)
    opened = "Opened the Campaign Family directory."
    assert opened not in lines(page)
    page.evaluate(MARK)
    kind = page.locator("#timeline-kind")
    assert "Pages viewed" in kind.locator("option").all_inner_texts()
    kind.select_option("views")
    button = page.get_by_role("button", name="Apply filters", exact=True)
    with page.expect_response(
        lambda response: (
            "kind=views" in response.url and response.request.method == "GET"
        )
    ) as fetched:
        button.click()
    assert fetched.value.status == 200, fetched.value.url
    has_count(page.locator("#table tbody tr"), 1)
    assert lines(page) == [opened]
    assert page.evaluate(MARKED) == "kept"
    focused(button)
    address = page.url.removeprefix(component_origin).split("#")[0]
    assert canonical(address) == VIEWS
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
