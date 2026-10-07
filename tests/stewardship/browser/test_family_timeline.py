"""The Family timeline: accessible, browser-local times, mode in place (#477).

The fixtures (``family_timeline_components``) serve the Administrator's page,
the exact page its Testing switch leads to, Staff's reduced view and a page
whose Open form is unavailable.
"""

import pytest

from .family_timeline_components import (
    ADMIN,
    NEWEST,
    OLDEST,
    STAFF,
    TESTING,
    UNAVAILABLE,
)
from .test_charts import assert_clean, watch
from .waits import has_attribute, has_text, visible

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


@pytest.mark.parametrize("path", [ADMIN, STAFF, UNAVAILABLE])
def test_pages_are_accessible_and_fit_a_phone(page, component_origin, axe_source, path):
    """Help closed, data first, no policy violation, and axe finds nothing."""
    errors = watch(page)
    page.goto(component_origin + path)
    assert not page.locator("details.about-page").evaluate("node => node.open")
    visible(page.get_by_role("heading", name="Summary"))
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)


def test_times_are_shown_in_the_browsers_time_zone(page, component_origin):
    """Every time is the browser's local time (the fixture browser is Pacific)."""
    page.goto(component_origin + ADMIN)
    # Newest first by default, so the invitation (2026-10-03 14:00 UTC,
    # 7:00 AM Pacific) is the last row.
    first = page.locator("#table tbody time[data-local-instant]").last
    assert first.get_attribute("datetime") == "2026-10-03T14:00:00+00:00"
    assert "7:00" in first.inner_text() and "PDT" in first.inner_text()
    assert "UTC" not in page.locator("main").inner_text()


def test_mode_switch_refreshes_in_place(page, component_origin):
    """Testing replaces the region without a reload and keeps focus on the switch."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    page.evaluate(MARK)
    link = page.locator('[data-in-place="mode-testing"]')
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url(component_origin + TESTING + "#table")
    visible(page.locator("#table").get_by_text("no Testing rehearsal now"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement.dataset.inPlace") == "mode-testing"
    assert page.locator("#table table").count() == 0
    current = page.locator('#table [aria-current="page"]')
    assert current.inner_text() == "Testing rehearsal"
    assert_clean(page, errors)


def times(page):
    """The timeline's When values (instants), in row order."""
    return [
        cell.get_attribute("datetime")
        for cell in page.locator("#table tbody tr td:first-child time").all()
    ]


def test_when_heading_sorts_in_place_both_ways(page, component_origin):
    """Newest first by default; the When heading reverses it without a reload."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    newest = times(page)
    assert newest == sorted(newest, reverse=True) and len(newest) > 2
    page.evaluate(MARK)
    # Bring the heading into view first (a click would scroll to it anyway),
    # so the position seen after the swap is the reader's own.
    heading = page.get_by_role("link", name="When (sort ascending)")
    heading.scroll_into_view_if_needed()
    scrolled = page.evaluate("window.scrollY")
    with page.expect_request(
        lambda request: "sort=when" in request.url and request.method == "GET"
    ) as fetched:
        heading.click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    when = page.locator("#table th[data-sort-column='when']")
    has_attribute(when, "aria-sort", "ascending")
    assert times(page) == sorted(newest)
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("window.scrollY") == scrolled
    assert page.url == component_origin + OLDEST + "#table"
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by When, ascending",
    )
    page.get_by_role("link", name="When (sort descending)").click()
    has_attribute(when, "aria-sort", "descending")
    assert times(page) == newest
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + NEWEST + "#table"
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by When, descending",
    )
    assert_clean(page, errors)


def test_staff_see_the_summary_only(page, component_origin):
    """Submitted, last email and code with Open form; no timeline or switch."""
    page.goto(component_origin + STAFF)
    # Open form posts its hand-off to a new tab; no URL carries the code.
    button = page.get_by_role("button", name="Open form in a new tab")
    form = page.locator("form[data-open-form]").filter(has=button)
    assert form.get_attribute("method") == "post"
    assert form.get_attribute("target") == "_blank"
    assert form.get_attribute("action").endswith("/open-form")
    assert page.locator("a[href*='#code=']").count() == 0
    assert page.get_by_role("heading", name="Timeline", exact=True).count() == 0
    assert page.locator("[data-in-place]").count() == 0


def test_unavailable_open_form_is_disabled_with_its_reason(page, component_origin):
    """In Testing mode the button cannot be used, and the page says why."""
    page.goto(component_origin + UNAVAILABLE)
    button = page.get_by_role("button", name="Open form")
    assert button.is_disabled()
    reason = page.locator("#open-form-reason")
    visible(reason.get_by_text("accepts only Testing codes", exact=False))
    assert page.locator("form[data-open-form]").count() == 0
