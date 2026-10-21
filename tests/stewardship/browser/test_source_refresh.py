"""The manual refresh confirmation page: accessible, plain, posts its key."""

import re
from uuid import UUID

import pytest

from .live_status_components import REFRESH, REFRESH_REQUESTED, TASK_STATUS
from .test_in_place import MARK, MARKED, VIEWPORT
from .test_live_status_in_place import answer_reads
from .waits import has_value, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_refresh_confirmation_is_accessible_and_states_what_happens(
    page, component_origin, axe_source, width
):
    """Both wordings pass the accessibility scan and say what a request does."""
    page.set_viewport_size({"width": width, "height": 900})
    # The last shows the run this sign-in requested (#521).
    for path in (
        "/source-refresh",
        "/source-refresh-running",
        "/source-refresh-late",
        REFRESH_REQUESTED,
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
        assert page.get_by_role("button", name="Refresh now").count() == 1
        # The breadcrumb is the way back (#521): no hand-written Cancel.
        assert page.get_by_role("link", name="Cancel").count() == 0
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


def test_the_request_posts_its_key(page, component_origin):
    """The form posts the page's key and CSRF token to the refresh route."""
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


def test_refresh_now_stays_on_the_page_and_follows_the_run(page, component_origin):
    """Refresh now stays on Refresh from ParishSoft (navigation rule 5, #521).

    The request posts in place and follows the server's redirect back to the
    page: no reload, nothing above the button moves, focus stays on the
    button, and the requested run's live status appears below it. When the
    run's passive status poll finds it finished, the page is re-read once in
    place, so the data age above and the request key are fresh too.
    """
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + REFRESH)
    page.evaluate(MARK)
    button = page.get_by_role("button", name="Refresh now")
    before = button.bounding_box()
    data_age = page.locator("section.panel time[data-local-instant]").first
    first_age = data_age.get_attribute("datetime")
    assert page.get_by_role("heading", name="Current status").count() == 0
    button.click()
    # Only the run's own announcer speaks (Refresh now is data-in-place-spoken),
    # so there is no "Refresh requested." ahead of the state sentence.
    from playwright.sync_api import expect

    expect(page.locator('[data-live-announcer="task"]')).to_have_text(re.compile(r"\S"))
    assert page.get_by_text("Refresh requested.").count() == 0
    region = page.locator("#refresh-run")
    visible(region.get_by_role("heading", name="Current status"))
    visible(region.get_by_text("of 4,000 records checked"))
    visible(region.get_by_role("link", name="View this refresh's Background task"))
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + REFRESH_REQUESTED + "#refresh-run"
    assert button.bounding_box()["y"] == before["y"]
    assert page.evaluate("document.activeElement.textContent.trim()") == "Refresh now"
    # The run finishes: the poll brings the finished status, and the page is
    # re-read once, in place, for the panel above and a fresh key.
    polls = answer_reads(
        page, component_origin, TASK_STATUS, "/live-task-finished-status"
    )
    reads = answer_reads(
        page, component_origin, REFRESH_REQUESTED, "/live-refresh-finished"
    )
    visible(region.get_by_text("Finished successfully."))
    key = page.locator("input[name=request_key]")
    visible(region.get_by_text("4,000 records checked, 12 changed."))
    has_value(key, str(UUID(int=522)))
    assert key.input_value() == str(UUID(int=522))
    assert data_age.get_attribute("datetime") != first_age
    assert page.evaluate(MARKED) == "kept"
    page.wait_for_timeout(3000)
    assert len(polls) >= 1 and len(reads) == 1, (polls, reads)
