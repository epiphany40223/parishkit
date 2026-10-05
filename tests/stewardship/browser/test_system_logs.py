"""Administrator-only combined log screen and its private filters."""

import pytest

from .waits import eventually, has_attribute, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A mark outside the table region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
# Each row's Level cell (the cell right after the Time row header), as its
# icon's level or, for an audit record, its words.
LEVEL_CELLS = """() => [...document.querySelectorAll(
    '#table tbody tr > th[scope=row] + td.log-level-cell')].map(cell => {
    const icon = cell.querySelector('svg.level-icon');
    return icon ? icon.getAttribute('class').split('level-icon-')[1]
        : cell.textContent.trim();
})"""

PAGES = (
    "/logs",
    "/logs-default",
    "/logs-older",
    "/logs-empty",
    "/logs-error-400",
    "/logs-error-503",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_logs_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Every state stays readable, escaped and operable at phone width."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/logs")
    # Worker processes are named as such, not as an unknown person.
    visible(page.get_by_role("cell", name="Background worker", exact=False).first)
    # Severity is an icon whose shape, not only its color, tells the levels
    # apart; the icon is hidden from assistive technology and the level's word
    # names the cell instead (#569).
    for word in ("Debug", "Information", "Warning", "Error"):
        assert page.get_by_role("cell", name=word, exact=True).count() == 1
    assert page.locator(".log-level svg.level-icon[aria-hidden=true]").count() == 4
    # The level choices carry the same icons beside their words.
    assert page.locator(".log-level-choices svg.level-icon").count() == 5
    # Recorded detail is shown as text, never interpreted as markup.
    assert page.get_by_text("<b>safe</b>", exact=True).count() == 4
    assert page.locator("td b").count() == 0
    entry = page.get_by_role("row", name="dashboard_viewed", exact=False)
    # A resolved actor is named; its identifier moves under Technical details,
    # closed by default, and the entry cross-links by actor and campaign.
    assert entry.get_by_role("cell", name="admin@example.org", exact=False).count()
    details = entry.locator("details.technical-details")
    assert details.get_attribute("open") is None
    assert "00000000-0000-0000-0000-00000000012d" in details.text_content()
    assert entry.get_by_role("button", name="Same actor").count() == 1
    assert entry.get_by_role("button", name="Same campaign").count() == 1
    details.locator("summary").click()
    visible(details.get_by_text("00000000-0000-0000-0000-00000000012d"))
    assert entry.get_by_text("Audit record", exact=True).count() == 1
    # The chosen filters are kept, including a ticked DEBUG.
    assert page.get_by_label("Debug", exact=True).is_checked()
    assert page.get_by_label("Task or request correlation identifier").input_value()
    # The shared navigator, above and below the table, pages by POST forms.
    assert page.get_by_role("button", name="Next", exact=True).count() == 2
    assert page.get_by_role("button", name="Previous", exact=True).count() == 0
    assert page.get_by_text("Page 1 of 2", exact=True).count() == 2
    time = page.get_by_role("columnheader", name="Time", exact=False)
    assert time.get_attribute("aria-sort") == "descending"
    assert time.get_by_role("button", name="sort ascending").count() == 1
    search = page.get_by_label("Source")
    search.focus()
    # The type's help bubble sits between its label and its field. WebKit's
    # default Tab order skips buttons (a macOS setting), so allow either.
    page.keyboard.press("Tab")
    if page.locator(":focus").get_attribute("aria-controls") == "log-event-tip":
        page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "event"

    page.goto(component_origin + "/logs-default")
    # DEBUG is excluded until chosen.
    assert not page.get_by_label("Debug", exact=True).is_checked()
    assert page.get_by_label("Critical", exact=True).is_checked()
    page.goto(component_origin + "/logs-older")
    assert page.get_by_role("button", name="Previous", exact=True).count() == 2
    assert page.get_by_text("Page 2 of 2", exact=True).count() == 2
    # A CRITICAL entry stands out: its own icon on a highlighted row.
    critical = page.locator("tr.log-row-critical")
    assert critical.count() == 1
    assert critical.locator("svg.level-icon-critical").count() == 1
    departed = page.get_by_role("row", name="admin_login", exact=False)
    assert (
        departed.get_by_text("Service, or a former portal user", exact=False).count()
        == 1
    )
    assert departed.get_by_text(
        "A portal user (Administrator, Staff or Ministry leader) signed in"
    ).count()
    assert departed.get_by_role("button", name="Show related entries").count() == 1
    page.goto(component_origin + "/logs-empty")
    visible(page.get_by_text("No matching entries."))
    page.goto(component_origin + "/logs-error-400")
    assert page.get_by_role("alert").count() == 1
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 1
    assert page.get_by_role("link", name="Return to the newest log entries").count()
    assert page.get_by_text("never in a web address", exact=False).count() == 0
    # A query string is refused for where it was sent, not for what it said.
    page.goto(component_origin + "/logs-error-query")
    assert page.get_by_role("alert").count() == 1
    assert page.get_by_text("never in a web address", exact=False).count() == 1
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 0
    # An outage or a denial submitted nothing that could be corrected.
    page.goto(component_origin + "/logs-error-503")
    assert page.get_by_text("Identifiers must be complete", exact=False).count() == 0
    assert page.get_by_text("never in a web address", exact=False).count() == 0


def test_log_filters_and_paging_without_scripts(browser_engine, component_origin):
    """Identifiers, the snapshot and the sort post natively, never in the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/logs-default")
        page.get_by_label("Debug", exact=True).check()
        page.get_by_label("Information", exact=True).uncheck()
        page.get_by_label("Source").select_option("audit")
        # A type written directly by its owner, which no fixed list would offer.
        page.get_by_label("Event or action type", exact=False).fill("admin_login")
        actor = "1abcdef0-0000-4000-8000-000000000000"
        # Identifier filters are folded away until asked for.
        assert not page.get_by_label("Actor identifier").is_visible()
        page.get_by_text("Filter by identifier", exact=True).click()
        page.get_by_label("Actor identifier").fill(actor)
        # Answer only the form posts; the page itself shares this path.
        page.route(
            "**/logs",
            lambda route: (
                route.fulfill(body="Filtered")
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        body = sent.value.post_data
        # A submitted form is marked, so no tick can mean none rather than default.
        assert "applied=yes" in body and "debug=yes" in body and "info=" not in body
        assert "source=audit" in body and "event=admin_login" in body
        assert f"actor={actor}" in body
        assert actor not in sent.value.url and "?" not in sent.value.url

        page.goto(component_origin + "/logs")
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Next", exact=True).first.click()
        body = sent.value.post_data
        # The snapshot travels with the applied filters, not the edited form.
        assert "through=2026-09-19T15%3A04%3A05.123456%2B00%3A00" in body
        assert "page=2" in body and "sort=newest" in body and "size=25" in body
        assert "debug=yes" in body and "correlation=00000000" in body
        assert "?" not in sent.value.url

        page.goto(component_origin + "/logs")
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="sort ascending").click()
        body = sent.value.post_data
        # A heading keeps the filters and snapshot and starts at page one.
        assert "sort=oldest" in body and "through=" in body and "page=" not in body
        assert "correlation=00000000" in body and "?" not in sent.value.url
    finally:
        context.close()


def test_level_icon_column_survives_in_place_sort_and_paging(page, component_origin):
    """The second column shows each entry's level as the level choices' icon,
    named for screen readers and on hover, and keeps doing so after the table
    re-sorts and re-pages in place (#569)."""
    page.goto(component_origin + "/logs")
    headings = page.locator("#table thead th")
    assert "Time" in headings.nth(0).inner_text()
    assert headings.nth(1).inner_text() == "Level"
    newest = ["debug", "Audit record", "info", "Audit record", "warning", "error"]
    assert page.evaluate(LEVEL_CELLS) == newest
    warning = page.get_by_role("cell", name="Warning", exact=True)
    assert warning.locator(".log-level").get_attribute("title") == "Warning"
    # The icon is drawn at a readable size in a column no wider than it needs.
    icon = warning.locator("svg").bounding_box()
    assert icon["width"] >= 16 and icon["height"] >= 16
    assert headings.nth(1).bounding_box()["width"] < 120
    # Narrow, but never broken mid-word: "Level" is one line and "Audit
    # record" at most two (main's overflow-wrap: anywhere would split them).
    lines = """element => {
        const range = document.createRange();
        range.selectNodeContents(element);
        return new Set([...range.getClientRects()].map(rect => rect.top)).size;
    }"""
    assert headings.nth(1).evaluate(lines) == 1
    assert page.locator("#table .log-kind").first.evaluate(lines) <= 2
    page.evaluate(MARK)

    def answer(route):
        """Serve each table post the fixture its control leads to."""
        if route.request.method != "POST":
            route.continue_()
            return
        path = "/logs-older" if "page=2" in route.request.post_data else "/logs-oldest"
        route.fulfill(response=route.fetch(url=component_origin + path, method="GET"))

    page.route("**/logs", answer)
    page.get_by_role("button", name="sort ascending").click()
    has_attribute(
        page.locator("#table th[data-sort-column='time']"), "aria-sort", "ascending"
    )
    eventually(page, LEVEL_CELLS, newest[::-1])
    assert page.evaluate(MARKED) == "kept"
    page.get_by_role("button", name="Next", exact=True).first.click()
    eventually(page, LEVEL_CELLS, ["critical", "Audit record"])
    assert page.evaluate(MARKED) == "kept"
    critical = page.get_by_role("cell", name="Critical", exact=True)
    assert critical.locator("svg.level-icon-critical").count() == 1
