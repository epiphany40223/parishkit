"""Native private search and accessible scoped Ministry report controls."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_ministry_reports_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Reports stay readable and private without requiring a JavaScript UI."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (
        "/ministry-detail",
        "/ministry-summary",
        "/ministry-history",
        "/ministry-empty",
        "/ministry-gated",
    ):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/ministry-detail")
    assert page.get_by_text("Example <Member>", exact=False).count() == 1
    assert page.get_by_text("Not published", exact=True).count() == 2
    search = page.get_by_label("Search Member name or DUID")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "activity"
    assert page.get_by_role("button", name="Next page").is_visible()


def test_ministry_search_without_scripts(browser_engine, component_origin):
    """Private search and history selection submit only through native POST."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/ministry-detail")
        page.get_by_label("Search Member name or DUID").fill("Private name")
        page.get_by_label("Request history").select_option("all")
        page.route("**/ministries/join/", lambda route: route.fulfill(body="Filtered"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert "search=Private+name" in sent.value.post_data
        assert "history=all" in sent.value.post_data
        assert "ministry=9" in sent.value.post_data
        assert "Private" not in sent.value.url and "?" not in sent.value.url
    finally:
        context.close()


def test_ministry_complete_export_without_scripts(browser_engine, component_origin):
    """Applied filters and private selection survive native export without URL leaks."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/ministry-detail")
        page.get_by_label("Export format").select_option("xlsx")
        page.get_by_label("Export timezone").select_option("America/Detroit")
        page.route("**/ministries/export/", lambda route: route.fulfill(body="Queued"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Queue complete export").click()
        assert "search=Example" in sent.value.post_data
        assert (
            "ministry=9" in sent.value.post_data
            and "action=join" in sent.value.post_data
        )
        assert (
            "format=xlsx" in sent.value.post_data
            and "page=" not in sent.value.post_data
        )
        assert "?" not in sent.value.url and "/9/" not in sent.value.url
        page.goto(component_origin + "/ministry-gated")
        assert page.get_by_role("button", name="Queue complete export").is_disabled()
    finally:
        context.close()
