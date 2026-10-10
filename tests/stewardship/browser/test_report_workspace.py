"""Real report templates retain exact values, native forms and accessible controls."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_report_chart_scope_export_controls_and_accessibility(
    page, component_origin, axe_source, width
):
    """Inspect exact facts without changing scope, then validate native export forms."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/participation")
    visible(page.get_by_role("status").filter(has_text="Updating"))
    slider = page.get_by_role("slider", name="Inspect campaign date")
    slider.focus()
    page.keyboard.press("Home")
    values = page.locator("[data-digest-values]").inner_text()
    assert "Historical as of day" in values and "$1,234.56" in values
    # The live text stays full for screen readers; the visible tooltip is short.
    tip = page.locator("[data-digest-tip]")
    assert tip.is_visible()
    assert tip.locator("dd").all_inner_texts() == ["1", "1", "$1,235"]
    assert "Historical" not in tip.inner_text()
    assert page.locator("tbody tr").count() == 3
    page.get_by_label("Export format", exact=True).select_option("xlsx")
    button = page.get_by_role("button", name="Generate export")
    button.focus()
    assert page.locator("input[name=request_key]").count() == 2
    assert page.locator("input[name=browser_timezone]").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    ) == ["America/Los_Angeles", "America/Los_Angeles"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for path in (
        "/participation",
        "/report-export",
        "/report-export-busy",
        "/report-exact",
        "/report-export-expired",
    ):
        page.goto(component_origin + path)
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
          runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id, impact}) => ({id, impact}))""")
            == []
        )


def test_report_and_export_pages_offer_their_actions(page, component_origin):
    """Scope, exact values and each export state's action are on the page."""
    page.goto(component_origin + "/participation")
    assert "$3,234.56" in page.locator("table").inner_text()
    visible(page.get_by_role("button", name="Apply report options"))
    visible(page.get_by_role("button", name="Generate export"))
    visible(page.get_by_role("button", name="Queue export with the latest data"))
    page.goto(component_origin + "/report-export")
    visible(page.get_by_role("button", name="Download export"))
    page.goto(component_origin + "/report-exact")
    visible(page.get_by_role("button", name="Cancel export"))
    page.goto(component_origin + "/report-export-expired")
    visible(page.get_by_role("button", name="Regenerate expired file"))


def test_export_status_updates_itself_and_downloads_once(page, component_origin):
    """A queued export is re-read in place and its ready file downloads by itself."""
    ready = page.request.get(component_origin + "/report-export").text()
    reads, downloads = [], []

    def status(route):
        """The page's own passive status re-read: queued once, then ready."""
        reads.append(route.request.method)
        if len(reads) < 2:
            route.continue_()
        else:
            route.fulfill(body=ready, content_type="text/html")

    page.route("**/report-export-pending", status)

    def download(route):
        """Record the download POST; 204 keeps the page, as an attachment does.

        (Playwright's WebKit renders a route-fulfilled attachment inline, so
        the test answers with No Content rather than a file.)
        """
        downloads.append(route.request.method)
        route.fulfill(status=204)

    page.route("**/download", download)
    page.goto(component_origin + "/report-export-pending")
    visible(page.get_by_text("Preparing your file"))
    assert not page.get_by_text("Requested by (user reference)").is_visible()
    page.locator("[data-export-state=ready]").wait_for()
    page.wait_for_timeout(500)
    assert downloads == ["POST"]
    visible(page.get_by_role("button", name="Download export"))
    assert set(reads) == {"GET"}


def test_queued_export_shows_what_it_waits_for(page, component_origin):
    """The task ahead's start is a local clock time; the wait keeps ticking."""
    import re

    from playwright.sync_api import expect

    page.goto(component_origin + "/report-export-waiting")
    visible(page.get_by_text("Waiting for the ParishSoft update to finish"))
    expect(page.locator("time[data-time-only]")).to_have_text(
        re.compile(r"^\d{1,2}:\d{2} (AM|PM)$")
    )
    expect(page.locator("time[data-live-since]")).to_have_text(
        re.compile(r"^\d+ (seconds?|minutes?|hours?) ago$")
    )


def test_failed_export_explains_itself_with_retry(page, component_origin):
    """A failed export says so in plain words and offers Retry, without polling."""
    page.goto(component_origin + "/report-export-failed")
    visible(page.get_by_role("alert").filter(has_text="could not be created"))
    visible(page.get_by_role("button", name="Retry export"))
