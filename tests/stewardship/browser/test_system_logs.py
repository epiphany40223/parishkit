"""Administrator-only combined log screen and its private filters."""

import pytest

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
    # Severity is a word beside its symbol, so it never depends on color.
    for word in ("Debug", "Information", "Warning", "Error"):
        assert page.locator(".log-level", has_text=word).count() == 1
    assert page.locator(".log-level span[aria-hidden=true]").count() == 4
    # Recorded detail is shown as text, never interpreted as markup.
    assert page.get_by_text("<b>safe</b>", exact=True).count() == 4
    assert page.locator("td b").count() == 0
    entry = page.get_by_role("row", name="dashboard_viewed", exact=False)
    assert entry.get_by_text("admin@example.org", exact=True).count() == 1
    assert entry.get_by_text("Audit record", exact=True).count() == 1
    # The chosen filters are kept, including a ticked DEBUG.
    assert page.get_by_label("Debug").is_checked()
    assert page.get_by_label("Task or request correlation identifier").input_value()
    assert page.get_by_role("button", name="Older entries").count() == 1
    assert page.get_by_role("button", name="Back to the newest entries").count() == 0
    search = page.get_by_label("Source")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "event"

    page.goto(component_origin + "/logs-default")
    # DEBUG is excluded until chosen.
    assert not page.get_by_label("Debug").is_checked()
    assert page.get_by_label("Critical").is_checked()
    page.goto(component_origin + "/logs-older")
    assert page.get_by_role("button", name="Back to the newest entries").count() == 1
    departed = page.get_by_role("row", name="admin_login", exact=False)
    assert departed.get_by_text("Not a current portal user", exact=False).count() == 1
    page.goto(component_origin + "/logs-empty")
    assert page.get_by_text("No matching entries.", exact=True).is_visible()
    page.goto(component_origin + "/logs-error-400")
    assert page.get_by_role("alert").count() == 1
    assert page.get_by_role("link", name="Return to the newest log entries").count()


def test_log_filters_and_paging_without_scripts(browser_engine, component_origin):
    """Identifiers and the cursor post natively, never through the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/logs-default")
        page.get_by_label("Debug").check()
        page.get_by_label("Information").uncheck()
        page.get_by_label("Source").select_option("audit")
        # A type written directly by its owner, which no fixed list would offer.
        page.get_by_label("Event or action type", exact=False).fill("admin_login")
        actor = "1abcdef0-0000-4000-8000-000000000000"
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
            page.get_by_role("button", name="Older entries").click()
        body = sent.value.post_data
        # The cursor travels with the applied filters, not the edited form.
        assert "before=2026-09-19T15%3A0" in body and "before_id=" in body
        assert "debug=yes" in body and "correlation=00000000" in body
        assert "?" not in sent.value.url
    finally:
        context.close()
