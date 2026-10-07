"""The manual refresh confirmation page: accessible, plain, posts natively."""

import pytest

from .conftest import no_script_context
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_refresh_confirmation_is_accessible_and_states_what_happens(
    page, component_origin, axe_source, width
):
    """Both wordings pass the accessibility scan and say what a request does."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/source-refresh", "/source-refresh-running", "/source-refresh-late"):
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
    # "ParishSoft data as of" and the connection line, each one time (#510).
    assert page.locator("time[data-local-instant]").count() == 2
    panel = page.locator("section.panel")
    visible(panel.get_by_text("ParishSoft data as of", exact=False))
    visible(panel.get_by_text("Connection: working", exact=False))
    assert page.get_by_text("A refresh is running", exact=False).count() == 0
    page.goto(component_origin + "/source-refresh-running")
    visible(page.get_by_text("not yet loaded", exact=False))
    visible(page.get_by_text("Connection: not checked yet", exact=False))
    visible(page.get_by_text("A refresh is running now", exact=False))


def test_data_age_connection_and_lateness_use_browser_local_times(
    page, component_origin
):
    """Data as of, the last full refresh, the connection and the late refresh.

    Every time is converted to the browser's zone (the fixture browser is
    Pacific), and no time is labeled UTC.
    """
    page.goto(component_origin + "/source-refresh-late")
    main = page.locator("main")
    data = page.locator("section.panel p").filter(has_text="ParishSoft data as of")
    visible(data)
    assert ", last full refresh" in data.inner_text()
    visible(page.get_by_text("Connection: failing since", exact=False))
    visible(page.get_by_text("is 45 minutes late", exact=False))
    times = page.locator("main time[data-local-instant]")
    # Data as of, last full refresh, failing since and the late refresh.
    assert times.count() == 4
    for index in range(times.count()):
        assert "PDT" in times.nth(index).inner_text() or "PST" in (
            times.nth(index).inner_text()
        )
    assert "UTC" not in main.inner_text()


def test_the_request_posts_natively_with_its_key(browser_engine, component_origin):
    """Without scripts the form posts the page's key to the refresh route."""
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
        page.goto(component_origin + "/source-refresh")
        page.route(
            "**/admin/parish/parishsoft-refresh/",
            lambda route: route.fulfill(body="Queued"),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Refresh now").click()
        body = sent.value.post_data
        assert "request_key=" in body and "csrfmiddlewaretoken=" in body
        assert sent.value.url.endswith("/admin/parish/parishsoft-refresh/")
    finally:
        context.close()
