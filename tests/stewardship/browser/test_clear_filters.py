"""Clear filters puts a filter form back to the page's defaults in place (#1651).

The ui-conventions spec, "Clear filters". The Ministries list is a GET
filter bar (its address shows the filters), System logs a POST form whose
defaults are not blank (its Show ticks), and Additional information a
Filters panel, whose grid holds Apply filters and Clear filters in one cell.
Each test sets a mark outside every region, which only a full page load
could lose.
"""

from urllib.parse import parse_qs, urlsplit

import pytest

from .log_components import LIVE
from .test_in_place import MARK, MARKED, VIEWPORT
from .test_logs_in_place import FILTERS, fixture_filters
from .test_system_logs import posted
from .test_table_sorting import ministry_names
from .waits import focused, has_attribute, has_class, has_text, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

CLEAR = "#table-filters [data-clear-filters]"
TIP = "#table-filters-clear-tip"
FILTERED = "/ministries?q=Lectors&size=50&sort=name"
# The Ministries list as Clear filters asks for it: only the table's size
# and sort, which the form keeps.
CLEARED = "/ministries?size=50&sort=name"
# Apply filters' and Clear filters' page boxes, to catch any shift.
BOXES = """() => [...document.querySelectorAll("#table-filters .filter-actions button")]
    .map((node) => { const r = node.getBoundingClientRect();
        return [r.x, r.y + scrollY, r.width, r.height]; })"""


def unavailable(page):
    """Clear filters is greyed but focusable, described by its reason."""
    clear = page.locator(CLEAR)
    has_attribute(clear, "aria-disabled", "true")
    has_attribute(clear, "aria-describedby", TIP[1:])
    # Focusable: never the disabled property (Playwright reads aria-disabled
    # as not enabled).
    assert clear.evaluate("node => !node.disabled")


def available(page):
    """Clear filters can be used and names no reason."""
    from playwright.sync_api import expect

    clear = page.locator(CLEAR)
    expect(clear).not_to_have_attribute("aria-disabled", "true")
    expect(clear).not_to_have_attribute("aria-describedby", TIP[1:])


def serve_cleared(page, origin):
    """Answer the cleared Ministries query with the unfiltered list."""
    page.route(
        "**" + CLEARED,
        lambda route: route.fulfill(
            response=route.fetch(url=origin + "/ministries", method="GET")
        ),
    )


def test_get_filters_clear_in_place(page, component_origin):
    """Unfiltered, Clear filters waits with its reason; a typed filter makes
    it available; after Apply, Clear asks for the list with only its sort and
    size, empties the field, swaps the list in place, records the address,
    keeps focus on the now unavailable button and says what happened."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/ministries")
    page.evaluate(MARK)
    unavailable(page)
    before = page.evaluate(BOXES)
    page.get_by_label("Search by name").fill("Lectors")
    available(page)
    page.get_by_role("button", name="Apply filters").click()
    visible(page.get_by_text("Showing 1–1 of 1").first)
    assert ministry_names(page) == ["Lectors"]
    # Apply's own announcement first, so Clear's is not joined to it.
    has_text(
        page.get_by_role("status").filter(has_text="Showing"),
        "Showing 1–1 of 1 · Page 1 of 1",
    )
    available(page)
    serve_cleared(page, component_origin)
    with page.expect_request(lambda request: "/ministries?" in request.url) as sent:
        page.get_by_role("button", name="Clear filters").click()
    assert parse_qs(urlsplit(sent.value.url).query) == {
        "size": ["50"],
        "sort": ["name"],
    }
    assert sent.value.headers.get("x-requested-with") == "fetch"
    visible(page.get_by_text("Showing 1–2 of 2").first)
    assert ministry_names(page) == ["Community outreach", "Lectors"]
    assert page.get_by_label("Search by name").input_value() == ""
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + CLEARED
    focused(page.locator(CLEAR))
    unavailable(page)
    has_text(
        page.get_by_role("status").filter(has_text="Filters cleared."),
        "Filters cleared. Showing 1–2 of 2 · Page 1 of 1",
    )
    # Neither state ever moved the buttons.
    assert same_place(page.evaluate(BOXES), before)


def test_unavailable_clear_sends_nothing(page, component_origin):
    """Activating Clear filters while it has nothing to do only shows why."""
    page.goto(component_origin + "/ministries")
    unavailable(page)
    sent = []
    page.on("request", lambda request: sent.append(request.url))
    page.locator(CLEAR).click(force=True)
    has_class(page.locator(TIP), "is-shown")
    page.keyboard.press("Escape")
    page.locator(CLEAR).press("Enter")
    page.wait_for_timeout(300)
    assert sent == []


def test_page_opened_filtered_can_clear_until_its_defaults_show(page, component_origin):
    """A page opened already filtered (a bookmark, a count link) cannot know
    its defaults, so Clear filters is available; after it, it is not."""
    page.goto(component_origin + FILTERED)
    assert page.get_by_label("Search by name").input_value() == "Lectors"
    available(page)
    serve_cleared(page, component_origin)
    page.get_by_role("button", name="Clear filters").click()
    visible(page.get_by_text("Showing 1–2 of 2").first)
    unavailable(page)
    # Typing again makes it available; undoing the edit does not.
    search = page.get_by_label("Search by name")
    search.fill("x")
    available(page)
    search.fill("")
    unavailable(page)


def test_post_filters_clear_to_the_servers_defaults(page, component_origin):
    """System logs posts its filters. Clear filters posts only the kept
    fields (no submitted-form mark, search, tick or identifier), even while
    a half-typed identifier holds Apply filters, and the form then shows the
    server's first-visit defaults, ticks included, with "Filter by
    identifier" closed again."""
    import re

    from playwright.sync_api import expect

    page.set_viewport_size(VIEWPORT)
    answers = iter(["/logs-searched", LIVE])
    page.route(
        "**" + LIVE,
        lambda route: (
            route.fulfill(
                response=route.fetch(url=component_origin + next(answers), method="GET")
            )
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    page.goto(component_origin + LIVE)
    page.evaluate(MARK)
    defaults = page.evaluate(f"() => ({FILTERS})(document)")
    unavailable(page)
    page.get_by_label("Search", exact=True).fill("lag")
    page.locator("#log-show-info").uncheck()
    available(page)
    apply = page.get_by_role("button", name="Apply filters")
    with page.expect_response(lambda response: response.request.method == "POST"):
        apply.click()
    visible(page.locator("#table").get_by_text("Showing 1–1 of 1").first)
    # Apply's own announcement first, so Clear's is not joined to it.
    has_text(
        page.get_by_role("status").filter(has_text="Showing"),
        "Showing 1–1 of 1 · Page 1 of 1",
    )
    available(page)
    page.locator("#log-identifier-filters summary").click()
    page.get_by_label("Task or request correlation identifier").fill("not-an-id")
    expect(apply).to_be_disabled()
    with page.expect_response(
        lambda response: response.request.method == "POST"
    ) as answer:
        page.get_by_role("button", name="Clear filters").click()
    fields = posted(answer.value.request)
    assert set(fields) <= {"zone", "size", "sort"}, fields
    assert "zone" in fields
    has_text(
        page.get_by_role("status").filter(has_text="Filters cleared."),
        re.compile(r"^Filters cleared\. Showing 1–"),
    )
    assert page.evaluate(MARKED) == "kept"
    # The address follows the answer as after Apply (System logs shows its
    # link filters, #536), never the search or identifier cleared.
    address = urlsplit(page.url)
    assert address.path == LIVE
    assert not {"text", "correlation"} & set(parse_qs(address.query))
    cleared = page.evaluate(f"() => ({FILTERS})(document)")
    assert cleared == defaults == fixture_filters(page, component_origin, LIVE)
    assert cleared["identifiers"] is False
    focused(page.locator(CLEAR))
    unavailable(page)
    expect(apply).to_be_enabled()


def test_panel_holds_both_buttons_in_one_cell(page, component_origin):
    """In a Filters panel's grid, Clear filters sits beside Apply filters in
    one row, and its state never moves either button or the fields."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(component_origin + "/information-unfiltered")
    page.locator("details[data-filter-panel] summary").click()
    apply = page.get_by_role("button", name="Apply filters")
    clear = page.get_by_role("button", name="Clear filters")
    visible(clear)
    unavailable(page)
    a, c = apply.bounding_box(), clear.bounding_box()
    assert abs(a["y"] - c["y"]) < 1
    assert a["x"] + a["width"] <= c["x"] <= a["x"] + a["width"] + 16
    boxes = """() => [...document.querySelectorAll(
        "#table-filters > div, #table-filters > fieldset, #table-filters button")]
        .map((node) => { const r = node.getBoundingClientRect();
            return [r.x, r.y + scrollY, r.width, r.height]; })"""
    before = page.evaluate(boxes)
    page.get_by_label("Search Family name, DUID, submitted text or Staff notes").fill(
        "Example"
    )
    available(page)
    assert same_place(page.evaluate(boxes), before)


def test_directory_clear_keeps_the_testing_view(page, component_origin):
    """The directory's Responses shown choice is a view, not a filter: a
    segmented pair (#1535) whose choice the filter form carries as a kept
    hidden field. Clear filters posts it with the kept fields, the answer
    is the unfiltered Testing rehearsal view, and the pair still shows
    Testing. Switching the view alone changes no filter, so Clear stays
    unavailable."""
    from django.urls import reverse
    from playwright.sync_api import expect

    directory = reverse("admin:family_directory")
    page.goto(
        component_origin + directory + "?response=started&responses=yes&mode=testing"
    )
    switch = page.get_by_role("group", name="Responses shown", exact=True)
    chosen = switch.locator("[aria-current=page]")
    expect(chosen).to_have_text("Testing rehearsal")
    expect(page.get_by_label("Response", exact=True)).to_have_value("started")
    page.evaluate(MARK)
    available(page)
    answers = {
        mode: page.request.get(component_origin + directory + query).text()
        for mode, query in (
            ("testing", "?mode=testing"),
            ("production", "?mode=production"),
        )
    }
    page.route(
        "**/families/",
        lambda route: (
            route.fulfill(
                body=answers[
                    "production"
                    if "mode=production" in (route.request.post_data or "")
                    else "testing"
                ],
                content_type="text/html",
            )
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Clear filters").click()
    fields = posted(sent.value)
    assert fields.get("mode") == ["testing"], fields
    assert not {"response", "check", "reach", "q"} & set(fields), fields
    expect(page.get_by_label("Response", exact=True)).to_have_value("any")
    expect(chosen).to_have_text("Testing rehearsal")
    expect(page.locator("[data-testing-responses]")).to_be_visible()
    assert page.evaluate(MARKED) == "kept"
    unavailable(page)
    switch.get_by_role("button", name="Production").click()
    expect(chosen).to_have_text("Production")
    expect(page.locator("#table-filters input[name=mode]")).to_have_value("production")
    assert page.evaluate(MARKED) == "kept"
    unavailable(page)
