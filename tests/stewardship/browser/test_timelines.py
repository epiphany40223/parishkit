"""A Family's Timeline: accessible, browser-local times, filters in place (#1570).

The fixtures (``timeline_components``) serve the Administrator's page, the
exact pages its When heading and Kind filter lead to, Staff's reduced view
and a page whose Open form is unavailable. #477 built the page as the Family
timeline; #1570 renamed it, removed its Production / Testing pair and added
the filter bar.
"""

import pytest

from .automation_components import canonical
from .test_charts import assert_clean, watch
from .timeline_components import (
    ADMIN,
    EMAILS,
    LATER,
    NEWEST,
    OLDEST,
    STAFF,
    UNAVAILABLE,
)
from .waits import (
    focused,
    has_attribute,
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


def test_the_page_names_the_family_and_offers_no_testing_view(page, component_origin):
    """The heading names the Family; no Production / Testing control (#1570)."""
    page.goto(component_origin + ADMIN)
    visible(page.get_by_role("heading", name="Adams, Ann — timeline"))
    assert "Timeline" in page.title() and "Adams" not in page.title()
    assert page.locator("[data-in-place^='mode-']").count() == 0
    assert page.get_by_text("Testing rehearsal", exact=True).count() == 0


def test_times_are_shown_in_the_browsers_time_zone(page, component_origin):
    """Every time is the browser's local time (the fixture browser is Pacific)."""
    page.goto(component_origin + ADMIN)
    # Newest first by default, so the invitation (2026-10-03 14:00 UTC,
    # 7:00 AM Pacific) is the last row.
    first = page.locator("#table tbody time[data-local-instant]").last
    assert first.get_attribute("datetime") == "2026-10-03T14:00:00+00:00"
    # Table cells use the compact form, which names no zone (#1093).
    assert first.inner_text() == "Oct 3, 2026 7:00 AM"
    # From and Through are days in the browser's zone, which the page names.
    visible(page.locator("#timeline-zone-help"))
    assert "America/Los_Angeles" in page.locator("#timeline-zone-help").inner_text()
    # No time is labeled UTC. The export form's time zone menu and its help
    # offer UTC as a choice, which is not a shown time.
    shown = page.evaluate(
        "(() => { const main = document.querySelector('main').cloneNode(true);"
        " main.querySelector('form#timeline-export')?.remove();"
        " return main.textContent; })()"
    )
    assert "UTC" not in shown


def times(page):
    """The timeline's When values (instants), in row order."""
    return [
        cell.get_attribute("datetime")
        for cell in page.locator("#table tbody tr td:first-child time").all()
    ]


def lines(page):
    """The timeline's What happened words, in row order."""
    return [
        cell.inner_text() for cell in page.locator("#table tbody th[scope=row]").all()
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
    # The filter bar now carries the chosen order.
    assert page.locator('#table-filters input[name="sort"]').input_value() == "when"
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


def test_the_kind_filter_narrows_the_lines_in_place(page, component_origin):
    """Show lines swaps the table in place; nothing above it moves (#1570).

    The window is tall enough to hold the whole page, so the shorter table
    cannot clamp the scroll position and any move of the button is a shift.
    """
    errors = watch(page)
    page.set_viewport_size({"width": 1280, "height": 2400})
    page.goto(component_origin + ADMIN)
    every = lines(page)
    assert "Got past the first step" in every and "Invitation" in every
    page.evaluate(MARK)
    # The page script fills the browser's zone in on load; the request
    # carries it, as a reader's would.
    has_value(page.locator('#table-filters input[name="zone"]'), "America/Los_Angeles")
    page.locator("#timeline-kind").select_option("emails")
    button = page.get_by_role("button", name="Show lines")
    before = button.bounding_box()
    scrolled = page.evaluate("window.scrollY")
    with page.expect_response(
        lambda response: (
            "kind=emails" in response.url and response.request.method == "GET"
        )
    ) as fetched:
        button.click()
    assert fetched.value.request.headers.get("x-requested-with") == "fetch"
    assert fetched.value.status == 200, fetched.value.url
    page.wait_for_function(
        "!document.querySelector('#table tbody').textContent.includes('Got past')"
    )
    assert lines(page) == ["Submission receipt", "Reminder 1", "Invitation"]
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("window.scrollY") == scrolled
    assert same_place(button.bounding_box(), before)
    focused(button)
    assert page.locator("#timeline-kind").input_value() == "emails"
    # The address keeps the choice, so a reload or bookmark shows it again.
    assert page.url.endswith("#table")
    address = page.url.removeprefix(component_origin).split("#")[0]
    assert canonical(address) == EMAILS
    # The summary is not filtered.
    assert (
        "2 times; most recently" in page.locator("#summary-heading + dl").inner_text()
    )
    assert_clean(page, errors)


def test_from_and_through_gate_apply_and_a_reload_keeps_the_view(
    page, component_origin
):
    """A half-typed day holds Show lines; a whole one applies; reload keeps it.

    Engines differ in what typing does to a date control (see
    test_system_logs.py): Chromium and macOS WebKit mark a half-typed date
    as bad input, Linux WebKit ignores the keys. Each outcome is asserted,
    never skipped. The request carries the browser's zone, which the page
    script filled in, so the days are Pacific ones.
    """
    errors = watch(page)
    page.set_viewport_size({"width": 1280, "height": 2400})
    page.goto(component_origin + ADMIN)
    has_value(page.locator('#table-filters input[name="zone"]'), "America/Los_Angeles")
    button = page.get_by_role("button", name="Show lines")
    hint = page.locator("#table-filters [data-complete-hint]")
    start = page.get_by_label("From", exact=True)
    start.click()
    page.keyboard.type("09")
    page.locator("#timeline-kind").focus()
    if start.evaluate("node => node.validity.badInput"):
        assert button.is_disabled()
        has_text(hint, "Enter a whole From date, or clear it.")
    else:
        assert start.input_value() == "" and button.is_enabled()
    start.fill("2026-10-04")
    assert button.is_enabled()
    page.evaluate(MARK)
    before = button.bounding_box()
    with page.expect_response(
        lambda response: "start=2026-10-04" in response.url
    ) as fetched:
        button.click()
    assert fetched.value.status == 200, fetched.value.url
    assert fetched.value.request.headers.get("x-requested-with") == "fetch"
    message = page.locator("#table tbody").get_by_text(
        "No lines match these filters.", exact=False
    )
    visible(message)
    assert lines(page) == []
    assert page.evaluate(MARKED) == "kept"
    assert same_place(button.bounding_box(), before)
    address = page.url.removeprefix(component_origin).split("#")[0]
    assert canonical(address) == LATER
    # A reload asks the server for the same address and shows the same view,
    # with the day still chosen.
    page.reload()
    visible(message)
    assert page.evaluate(MARKED) is None
    assert start.input_value() == "2026-10-04"
    assert page.get_by_label("Through", exact=True).input_value() == ""
    assert page.locator("#timeline-kind").input_value() == "all"
    assert_clean(page, errors)


def test_the_filter_bar_is_one_row_on_a_desktop_and_wraps_on_a_phone(
    page, component_origin
):
    """Kind, From, Through and Show lines share one row until the page narrows."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + ADMIN)
    # The bar aligns its items' ends, so one row shares their bottoms.
    bottoms = page.locator(FIELDS).evaluate_all(
        "nodes => nodes.map(node => Math.round(node.getBoundingClientRect().bottom))"
    )
    assert len(bottoms) == 4 and max(bottoms) - min(bottoms) <= 4, bottoms
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_staff_see_the_summary_only(page, component_origin):
    """Submitted, last email and code with Open form; no timeline or filters."""
    page.goto(component_origin + STAFF)
    # Open form posts its hand-off to a new tab; no URL carries the code.
    button = page.get_by_role("button", name="Open form in a new tab")
    form = page.locator("form[data-open-form]").filter(has=button)
    assert form.get_attribute("method") == "post"
    assert form.get_attribute("target") == "_blank"
    assert form.get_attribute("action").endswith("/open-form/")
    assert page.locator("a[href*='#code=']").count() == 0
    assert page.get_by_role("heading", name="Timeline", exact=True).count() == 0
    assert page.locator("[data-in-place], #table-filters").count() == 0


def test_unavailable_open_form_is_disabled_with_its_reason(page, component_origin):
    """In Testing mode the button cannot be used, and the page says why."""
    page.goto(component_origin + UNAVAILABLE)
    button = page.get_by_role("button", name="Open form")
    assert button.is_disabled()
    reason = page.locator("#open-form-reason")
    visible(reason.get_by_text("accepts only Testing codes", exact=False))
    assert page.locator("form[data-open-form]").count() == 0


def export_form(page):
    """The timeline export form."""
    return page.locator("form#timeline-export")


def test_the_export_form_starts_on_the_browsers_zone(page, component_origin):
    """The form posts no mode, with the browser's zone preselected."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    form = export_form(page)
    visible(form)
    assert form.locator('input[name="mode"]').count() == 0
    # The fixture browser is Pacific; the menu starts there, not on UTC.
    assert form.locator("#timeline-export-timezone").input_value() == (
        "America/Los_Angeles"
    )
    assert_clean(page, errors)


def test_the_export_form_keeps_choices_across_a_filter(page, component_origin):
    """A format and zone picked before an in-place filter survive it."""
    errors = watch(page)
    page.goto(component_origin + ADMIN)
    form = export_form(page)
    form.locator("#timeline-format").select_option("pdf")
    form.locator("#timeline-export-timezone").select_option("UTC")
    page.locator("#timeline-kind").select_option("emails")
    page.get_by_role("button", name="Show lines").click()
    page.wait_for_function(
        "!document.querySelector('#table tbody').textContent.includes('Got past')"
    )
    assert form.locator("#timeline-format").input_value() == "pdf"
    assert form.locator("#timeline-export-timezone").input_value() == "UTC"
    assert_clean(page, errors)


def test_staff_get_no_export_form(page, component_origin):
    """Staff see the summary only, so no export is offered."""
    page.goto(component_origin + STAFF)
    visible(page.get_by_role("heading", name="Summary"))
    assert export_form(page).count() == 0
