"""The manual refresh confirmation page: accessible, plain, posts natively."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_refresh_confirmation_is_accessible_and_states_what_happens(
    page, component_origin, axe_source, width
):
    """Both wordings pass the accessibility scan and say what a request does."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/source-refresh", "/source-refresh-running"):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
        assert page.get_by_role("button", name="Refresh now").count() == 1
        assert page.get_by_role("link", name="Cancel").count() == 1
    page.goto(component_origin + "/source-refresh")
    assert page.locator("time[data-local-instant]").count() == 1
    assert page.get_by_text("A refresh is running", exact=False).count() == 0
    page.goto(component_origin + "/source-refresh-running")
    assert page.get_by_text("Not yet refreshed", exact=False).is_visible()
    assert page.get_by_text("A refresh is running now", exact=False).is_visible()


def test_the_request_posts_natively_with_its_key(browser_engine, component_origin):
    """Without scripts the form posts the page's key to the refresh route."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/source-refresh")
        page.route("**/source/refresh", lambda route: route.fulfill(body="Queued"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Refresh now").click()
        body = sent.value.post_data
        assert "request_key=" in body and "csrfmiddlewaretoken=" in body
        assert sent.value.url.endswith("/source/refresh")
    finally:
        context.close()
