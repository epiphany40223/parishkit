"""Administrator-only combined log screen and its private filters."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

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
    # Severity is a word beside its icon, so it never depends on color; the
    # icon is decorative and hidden from assistive technology.
    for word in ("Debug", "Information", "Warning", "Error"):
        assert page.locator(".log-level", has_text=word).count() == 1
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
    assert departed.get_by_text("An Administrator or Staff member signed in").count()
    assert departed.get_by_role("button", name="Show related entries").count() == 1
    page.goto(component_origin + "/logs-empty")
    visible(page.get_by_text("No matching entries.", exact=True))
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
