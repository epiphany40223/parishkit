"""Three-engine private native directories, accessible contacts and no-script use."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_directories_are_accessible_and_keep_filters_in_post(
    page,
    component_origin,
    axe_source,
    width,
):
    """Actual templates expose codes directly and preserve private pagination."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/family-directory", "/postal-directory", "/directory-empty"):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/family-directory")
    assert page.get_by_text("ABCDEFGH", exact=True).is_visible()
    assert page.locator("Family").count() == 0
    page.get_by_text("Contact details for Example <Family>", exact=True).click()
    assert page.get_by_text("Example Head", exact=True).is_visible()
    assert page.get_by_text("1 Example Street", exact=False).is_visible()
    search = page.get_by_label("Search Family name, DUID or address")
    search.fill("Private name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "exact_code"
    page.get_by_label("Exact Family code").fill("abcd-efgh")
    page.route("**/families/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    assert "exact_code=abcd-efgh" in sent.value.post_data
    assert "Private" not in sent.value.url and "abcd" not in sent.value.url


def test_directory_pagination_works_without_scripts(browser_engine, component_origin):
    """Codes, contacts and private navigation do not depend on JavaScript."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/postal-directory")
        assert page.get_by_text("ABCDEFGH", exact=True).is_visible()
        page.route("**/postal/", lambda route: route.fulfill(body="Next page"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Next page").click()
        assert (
            "page=2" in sent.value.post_data
            and "search=Example" in sent.value.post_data
        )
        assert "Example" not in sent.value.url and "?" not in sent.value.url
    finally:
        context.close()
