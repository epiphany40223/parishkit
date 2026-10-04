"""Shared Admin tables re-sort and re-page in place, keeping the reader's place (#478).

The fixtures serve each page the table's own controls lead to at the exact
query string those controls carry, so the script's fetch is an ordinary page
request and the swapped-in region is the real template output. POST tables
have no fixture route of their own; each test answers the form's post with
the GET fixture the view would have rendered.
"""

import pytest

from .talent_components import PATH as TALENTS
from .waits import has_attribute, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Tall enough for the Admin chrome, short enough that the table starts below
# the fold, so scrolling to it is a real scroll position to keep.
VIEWPORT = {"width": 1280, "height": 500}
SORTED_MINISTRIES = "/ministries?state=all&size=50&sort=-name#table"
TABLE_TOP = "document.getElementById('table').getBoundingClientRect().top"
# A mark outside every region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"


def scroll_to_table(page, above=0):
    """Scroll until the table region starts ``above`` pixels down the viewport.

    Returns ``scrollY``, which must be a real offset for the test to mean
    anything.
    """
    page.evaluate(
        "above => window.scrollTo(0, document.getElementById('table')"
        ".getBoundingClientRect().top + window.scrollY - above)",
        above,
    )
    offset = page.evaluate("window.scrollY")
    assert offset > 0, "the table must start below the fold for this test"
    return offset


def ministry_names(page):
    """The Ministry names in row order."""
    return page.locator("#table tbody th[scope=row]").all_inner_texts()


def sort_heading(page, column, region="table"):
    """The sortable column heading cell for ``column`` in ``region``."""
    return page.locator(f"#{region} th[data-sort-column='{column}']")


def fulfil_post_with(page, pattern, origin, choose):
    """Answer a POST matching ``pattern`` with the GET fixture ``choose`` names.

    ``choose(request)`` returns a fixture path, or None to answer plain text
    (a filter form's own submission, which the test only inspects).
    """

    def handle(route):
        if route.request.method != "POST":
            route.continue_()
            return
        path = choose(route.request)
        if path is None:
            route.fulfill(body="Filtered")
            return
        route.fulfill(response=route.fetch(url=origin + path, method="GET"))

    page.route(pattern, handle)


def test_get_table_resorts_in_place(page, component_origin):
    """A heading click fetches the sorted page, swaps only the table region,
    keeps the scroll position, moves focus back to the heading, announces the
    order and records the sort in the address bar."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/ministries")
    assert ministry_names(page) == ["Community outreach", "Lectors"]
    page.evaluate(MARK)
    offset = scroll_to_table(page, above=150)
    with page.expect_request(
        lambda request: "sort=-name" in request.url and request.method == "GET"
    ) as fetched:
        page.get_by_role("link", name="Ministry (sort descending)").click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    has_attribute(sort_heading(page, "name"), "aria-sort", "descending")
    assert ministry_names(page) == ["Lectors", "Community outreach"]
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + SORTED_MINISTRIES
    assert "Ministry" in page.evaluate("document.activeElement.textContent")
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by Ministry, descending",
    )
    # The heading now offers the other direction, as a fresh page would.
    visible(page.get_by_role("link", name="Ministry (sort ascending)"))


def test_filter_form_follows_an_in_place_sort(page, component_origin):
    """The page's own filter form carries the table's sort as hidden fields;
    after an in-place re-sort it submits the new sort, not the one it loaded
    with."""
    page.goto(component_origin + "/ministries")
    sort = page.locator("#table-filters input[name=sort]")
    assert sort.input_value() == "name"
    page.get_by_role("link", name="Ministry (sort descending)").click()
    has_attribute(sort_heading(page, "name"), "aria-sort", "descending")
    assert sort.input_value() == "-name"
    with page.expect_request(lambda request: "state=" in request.url) as sent:
        page.get_by_role("button", name="Filter").click()
    assert "sort=-name" in sent.value.url


def test_selection_survives_an_in_place_resort(page, component_origin):
    """Rows still shown after the re-sort stay selected, and the swapped-in
    table's bulk controls work: Select all, the count and the action buttons."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/ministries")
    page.get_by_label("Select Lectors").check()
    visible(page.get_by_text("1 selected"))
    page.get_by_role("link", name="Ministry (sort descending)").click()
    has_attribute(sort_heading(page, "name"), "aria-sort", "descending")
    assert page.get_by_label("Select Lectors").is_checked()
    assert not page.get_by_label("Select Community outreach").is_checked()
    visible(page.get_by_text("1 selected"))
    assert page.get_by_role("button", name="Review inactivation").is_enabled()
    page.get_by_role("button", name="Select all").click()
    visible(page.get_by_text("2 selected"))


def test_rows_per_page_and_repeated_headings_work_after_a_swap(page, component_origin):
    """A new rows-per-page choice applies in place through the navigator's own
    form, and the swapped-in headings keep working: each click sorts again."""
    page.goto(component_origin + "/ministries")
    page.get_by_label("Rows per page").first.select_option("25")
    # The navigator's own form, as the browser would have sent it.
    page.wait_for_url("**/ministries?state=all&sort=name&size=25&page=1#table")
    assert page.get_by_label("Rows per page").last.input_value() == "25"
    assert page.evaluate("document.activeElement.matches('[data-page-size]')")
    page.get_by_role("link", name="Ministry (sort descending)").click()
    has_attribute(sort_heading(page, "name"), "aria-sort", "descending")
    assert ministry_names(page) == ["Lectors", "Community outreach"]
    page.get_by_role("link", name="Ministry (sort ascending)").click()
    has_attribute(sort_heading(page, "name"), "aria-sort", "ascending")
    assert ministry_names(page) == ["Community outreach", "Lectors"]
    assert page.url.endswith("/ministries?state=all&size=25&sort=name#table")


def test_navigator_pages_in_place(page, component_origin):
    """Next swaps in page 2, announces the rows shown and, with Next now plain
    text on the last page, puts focus on Previous."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/background")
    visible(page.get_by_text("Page 1 of 2").first)
    offset = scroll_to_table(page)
    page.get_by_role("link", name="Next").first.click()
    visible(page.get_by_text("Page 2 of 2").first)
    assert page.get_by_text("Page 1 of 2").count() == 0
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert "page=2" in page.url and page.url.endswith("#table")
    assert page.evaluate("document.activeElement.textContent") == "Previous"
    has_text(
        page.get_by_role("status").filter(has_text="Showing"),
        "Showing 51–51 of 51 · Page 2 of 2",
    )


def test_post_table_resorts_in_place_with_csrf_and_filters(page, component_origin):
    """A private report's heading posts the urlencoded body its form would,
    CSRF token and hidden filters included; the rows reorder in place and
    the address bar never changes."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/logs")
    first = page.locator("#table tbody th[scope=row] time").first
    newest = first.get_attribute("datetime")
    offset = scroll_to_table(page)
    fulfil_post_with(page, "**/logs", component_origin, lambda request: "/logs-oldest")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="sort ascending").click()
    body = sent.value.post_data
    assert sent.value.headers.get("x-requested-with") == "fetch"
    assert "application/x-www-form-urlencoded" in sent.value.headers["content-type"]
    assert f"csrfmiddlewaretoken={'a' * 64}" in body and "sort=oldest" in body
    assert "correlation=00000000" in body and "through=" in body
    assert "?" not in sent.value.url
    has_attribute(sort_heading(page, "time"), "aria-sort", "ascending")
    assert first.get_attribute("datetime") < newest
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.url == component_origin + "/logs"
    assert "Time" in page.evaluate("document.activeElement.textContent")
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by Time, ascending",
    )


def test_filter_and_export_forms_follow_a_post_tables_sort(page, component_origin):
    """After a report re-sorts in place its Sort order menu shows the new sort,
    Apply filters submits it, and the export form's hidden fields carry it."""
    page.goto(component_origin + "/information")
    page.evaluate(MARK)
    assert page.locator("#info-sort").input_value() == "newest"
    assert page.locator("#table-export input[name=sort]").input_value() == "newest"
    fulfil_post_with(
        page,
        "**/information/",
        component_origin,
        lambda request: (
            "/information-by-name"
            if request.headers.get("x-requested-with") == "fetch"
            else None
        ),
    )
    page.get_by_role("button", name="Family (sort ascending)").click()
    has_attribute(sort_heading(page, "family"), "aria-sort", "ascending")
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("#info-sort").input_value() == "name"
    assert page.locator("#table-export input[name=sort]").input_value() == "name"
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.headers.get("x-requested-with") != "fetch"
        )
    ) as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "sort=name" in sent.value.post_data


def test_two_tables_on_one_page_keep_each_others_choices(page, component_origin):
    """Sorting one table swaps every region from the fetched page, so a second
    table keeps the first table's choice and the filter form carries both."""
    page.goto(component_origin + "/talents-report")
    families = "#families-table tbody th[scope=row]"
    assert page.locator(families).all_inner_texts() == ["Baker", "Carter"]
    members = "#members-table tbody th[scope=row]"
    assert page.locator(members).all_inner_texts()[0].startswith("Zed")
    fulfil_post_with(
        page,
        "**" + TALENTS,
        component_origin,
        lambda request: (
            "/talents-report-both"
            if "members_sort=member" in request.post_data
            else "/talents-report-families-desc"
        ),
    )
    page.locator("#families-table").get_by_role(
        "button", name="Family (sort descending)"
    ).click()
    has_attribute(
        sort_heading(page, "family", "families-table"), "aria-sort", "descending"
    )
    assert page.locator(families).all_inner_texts() == ["Carter", "Baker"]
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("#members-table").get_by_role(
            "button", name="Member (sort ascending)"
        ).click()
    # The second heading carried the first table's sort, as the server
    # rendered it into the swapped-in region.
    assert "families_sort=-family" in sent.value.post_data
    has_attribute(
        sort_heading(page, "member", "members-table"), "aria-sort", "ascending"
    )
    assert page.locator(members).all_inner_texts()[0].startswith("Amy")
    assert page.locator(families).all_inner_texts() == ["Carter", "Baker"]
    assert page.locator("#table-filters input[name=families_sort]").input_value() == (
        "-family"
    )
    assert page.locator("#table-filters input[name=members_sort]").input_value() == (
        "member"
    )


def test_swapped_in_rows_are_enhanced_again(page, component_origin):
    """Script that binds to table rows runs again for a swapped-in table: Copy
    buttons appear and fields report their validity on blur."""
    page.goto(component_origin + "/hosted-files")
    if not page.evaluate(
        "Boolean(navigator.clipboard && navigator.clipboard.writeText)"
    ):
        pytest.skip("No clipboard API in this engine; Copy buttons stay hidden.")
    visible(page.locator("button[data-copy]").first)
    page.get_by_role("link", name="Name (sort ascending)").click()
    has_attribute(sort_heading(page, "name"), "aria-sort", "ascending")
    copy = page.locator("#table button[data-copy]").first
    visible(copy)
    # The read-only placeholder fields never validate; the navigator's page
    # number, swapped in with the rows, does.
    field = page.locator("#table input[data-page-number]").first
    field.focus()
    field.blur()
    has_attribute(field, "aria-invalid", "false")


def test_fetch_failure_falls_back_to_a_full_load_at_the_table(page, component_origin):
    """When the in-place fetch fails the ordinary navigation happens instead,
    and its fragment lands the reader on the table rather than at the top."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + "/ministries")
    aborted = []

    def handle(route):
        if route.request.headers.get("x-requested-with") == "fetch":
            aborted.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    page.route("**/ministries?*", handle)
    page.get_by_role("link", name="Ministry (sort descending)").click()
    page.wait_for_url(component_origin + SORTED_MINISTRIES)
    assert len(aborted) == 1 and "sort=-name" in aborted[0]
    has_attribute(sort_heading(page, "name"), "aria-sort", "descending")
    assert ministry_names(page) == ["Lectors", "Community outreach"]
    assert page.evaluate("window.scrollY") > 0
    assert page.evaluate(TABLE_TOP) < 100


def test_without_javascript_a_heading_loads_the_page_at_the_table(
    browser_engine, component_origin
):
    """No script: the heading is a plain link whose fragment lands on the table."""
    context = browser_engine.new_context(java_script_enabled=False, viewport=VIEWPORT)
    try:
        page = context.new_page()
        page.goto(component_origin + "/ministries")
        page.get_by_role("link", name="Ministry (sort descending)").click()
        page.wait_for_url(component_origin + SORTED_MINISTRIES)
        has_attribute(sort_heading(page, "name"), "aria-sort", "descending")
        assert ministry_names(page) == ["Lectors", "Community outreach"]
        assert page.evaluate("window.scrollY") > 0
        assert page.evaluate(TABLE_TOP) < 100
    finally:
        context.close()


def test_export_choices_survive_a_sort_and_its_hidden_fields_follow(
    page, component_origin
):
    """Only table state is synchronised: an export form's hidden sort follows
    the in-place re-sort while the reader's format and timezone, including
    the browser zone a script added, stay as chosen."""
    page.goto(component_origin + "/information")
    export = page.locator("#table-export")
    export.locator("select[name=format]").select_option("xlsx")
    zone = export.locator("select[data-information-timezone]")
    chosen = zone.input_value()
    options = zone.locator("option").count()
    assert export.locator("input[name=sort]").input_value() == "newest"
    fulfil_post_with(
        page,
        "**/information/",
        component_origin,
        lambda request: "/information-by-name",
    )
    page.get_by_role("button", name="Family (sort ascending)").click()
    has_attribute(sort_heading(page, "family"), "aria-sort", "ascending")
    assert export.locator("input[name=sort]").input_value() == "name"
    assert export.locator("select[name=format]").input_value() == "xlsx"
    assert zone.input_value() == chosen
    assert zone.locator("option").count() == options


def test_packet_choices_follow_the_summary_while_other_options_are_kept(
    page, component_origin
):
    """The Ministry report's packet lists the Ministries on the page, so the
    list is replaced with the rows; the packet's own options, the export's
    format and browser timezone, and a ticked history box are untouched."""
    page.goto(component_origin + "/ministry-summary")
    choices = page.locator("#ministry-packet-choices input[name=ministries]")
    assert choices.count() == 1
    history = page.get_by_role("checkbox", name="Also include resolved")
    history.check()
    page.locator("#ministry-export-format").select_option("xlsx")
    zone = page.locator("#ministry-export-timezone")
    packet_zone = page.locator("#ministry-packet-timezone")
    assert zone.input_value() != "UTC" and packet_zone.input_value() != "UTC"
    fulfil_post_with(
        page,
        "**/ministries/",
        component_origin,
        lambda request: "/ministry-summary-desc",
    )
    page.get_by_role("button", name="Ministry (sort descending)").click()
    has_attribute(sort_heading(page, "ministry"), "aria-sort", "descending")
    assert choices.count() == 2
    assert page.locator("#table-export input[name=sort]").input_value() == "name_desc"
    assert page.locator("#ministry-sort").input_value() == "name_desc"
    assert history.is_checked()
    assert page.locator("#ministry-export-format").input_value() == "xlsx"
    assert zone.input_value() != "UTC" and packet_zone.input_value() != "UTC"


def test_a_response_without_the_region_is_shown_without_resending(
    page, component_origin
):
    """A fetch answered by a redirect to a page without the table (a sign-in
    page after the session ended) navigates there, with one POST sent."""
    page.goto(component_origin + "/logs-expired")
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request.url) if request.method == "POST" else None,
    )
    page.get_by_role("button", name="sort ascending").click()
    page.wait_for_url(component_origin + "/login#table")
    visible(page.get_by_role("button", name="Sign in with Google"))
    assert posts == [component_origin + "/redirect-to-login"]
