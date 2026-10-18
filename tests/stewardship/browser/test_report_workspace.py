"""Real report templates retain exact values, native forms and accessible controls."""

import re
from uuid import UUID

import pytest
from django.urls import reverse

from .test_charts import assert_clean, watch
from .test_response_dashboard import AXE
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_report_chart_scope_export_controls_and_accessibility(
    page, component_origin, axe_source, width
):
    """The daily chart takes focus and its exact facts are in the daily table,
    then validate native export forms."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/participation")
    visible(page.get_by_role("status").filter(has_text="Updating"))
    # The daily chart (#720) is a focusable view named for the chart; the
    # daily table under it holds every date's exact values.
    view = page.locator("#participation-chart [data-chart-view]")
    eventually(page, "() => !!document.querySelector('#participation-chart svg')")
    view.focus()
    assert page.evaluate("document.activeElement.hasAttribute('data-chart-view')")
    assert page.locator("[data-table-region] tbody tr").count() == 3
    assert "$1,234.56" in page.locator("[data-table-region] table").inner_text()
    # The small export panel (#1537): XLSX first and chosen, no PNG.
    menu = page.get_by_label("Export format", exact=True)
    assert menu.input_value() == "xlsx"
    assert menu.locator("option").all_inner_texts() == ["XLSX", "CSV", "PDF"]
    button = page.get_by_role("button", name="Generate export")
    button.focus()
    assert page.locator("input[name=request_key]").count() == 1
    assert page.locator("input[name=browser_timezone]").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    ) == ["America/Los_Angeles"]
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


# The Summary's Active population row (#1537).
POPULATION = '#response-summary [aria-label="Active population"]'


def test_statistics_are_the_summary_first_row(page, component_origin, axe_source):
    """The statistics are the Summary's first row of tiles, with no
    "Responses and email reach" chart (#1537): the Families by stage row
    gives the responses. The page draws under the CSP with no script error
    and passes axe."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/participation")
    row = page.get_by_role("list", name="Active population")
    assert row.locator(".stat-label").all_inner_texts() == [
        "Active Families",
        "Active Members",
        "Current annual pledges",
        "Last year's pledges (all Families)",
    ]
    expect(page.locator("#response-summary [data-chart]")).to_have_count(0)
    expect(page.get_by_text("Responses and email reach")).to_have_count(0)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_report_and_export_pages_offer_their_actions(page, component_origin):
    """Scope, exact values and each export state's action are on the page."""
    page.goto(component_origin + "/participation")
    assert "$3,234.56" in page.locator("[data-table-region] table").inner_text()
    # The Chart population is a binary choice with no Apply button (#1535).
    choice = page.get_by_role("group", name="Chart population")
    visible(choice.get_by_role("link", name="Current population"))
    assert page.get_by_role("button", name="Apply report options").count() == 0
    visible(page.get_by_role("button", name="Generate export"))
    # The latest-data export is the command line's only (#1537).
    assert (
        page.get_by_role("button", name="Queue export with the latest data").count()
        == 0
    )
    page.goto(component_origin + "/report-export")
    visible(page.get_by_role("button", name="Download export"))
    page.goto(component_origin + "/report-exact")
    visible(page.get_by_role("button", name="Cancel export"))
    # Exports are point-in-time (#1398): an ended one is the fixed page,
    # with a way back to its report and no Regenerate.
    page.goto(component_origin + "/report-export-expired")
    visible(page.get_by_text("This export is no longer available."))
    visible(page.locator("main").get_by_role("link", name="Participation"))
    assert page.get_by_role("button").filter(has_text="Regenerate").count() == 0


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

    page.route(
        "**" + reverse("admin:report_export_download", args=[UUID(int=20)]), download
    )
    page.goto(component_origin + "/report-export-pending")
    visible(page.get_by_text("Preparing your file"))
    assert not page.get_by_text("Requested by (user reference)").is_visible()
    page.locator("[data-export-state=ready]").wait_for()
    page.wait_for_timeout(500)
    assert downloads == ["POST"]
    visible(page.get_by_role("button", name="Download export"))
    assert set(reads) == {"GET"}


def test_an_export_cancelled_elsewhere_settles_its_open_page(page, component_origin):
    """A preparing page whose export ended elsewhere (another Administrator's
    Cancel, ``pk-admin export cancel``, another tab; #1398) reads the 410
    "no longer available" page once: its sentence takes the status panel's
    place, polling stops, and nothing above the panel moves."""
    gone = page.request.get(component_origin + "/report-export-expired").text()
    reads = []

    def status(route):
        """The page's own passive status re-read: queued once, then 410."""
        reads.append(route.request.method)
        if len(reads) < 2:
            route.continue_()
        else:
            route.fulfill(status=410, body=gone, content_type="text/html")

    page.route("**/report-export-pending", status)
    page.goto(component_origin + "/report-export-pending")
    visible(page.get_by_text("Preparing your file"))
    panel = page.locator("[data-live-status=export]")
    top = panel.evaluate("node => node.getBoundingClientRect().top")
    page.locator("[data-live-state=gone]").wait_for()
    visible(panel.get_by_text("This export is no longer available."))
    visible(panel.get_by_role("link", name="Participation"))
    assert page.get_by_role("button", name="Cancel export").count() == 0
    assert panel.evaluate("node => node.getBoundingClientRect().top") == top
    settled = len(reads)
    # Polling has stopped: no further read, and no "Couldn't check" line.
    # The common prefix matches both the refusal and the retry line
    # (#1644, #1647), so a rewording of either cannot make this pass
    # whatever the page says.
    page.wait_for_timeout(4000)
    assert len(reads) == settled == 2
    assert page.get_by_text("Couldn't check").count() == 0


def test_a_410_without_a_gone_sentence_stops_polling(page, component_origin):
    """Any other 410 (such as a latest-data export whose campaign is no
    longer current) is settled too: polling stops with the refusal line
    rather than retrying a permanent answer."""
    reads = []

    def status(route):
        """Queued once, then a 410 page with no [data-live-gone] sentence."""
        reads.append(route.request.method)
        if len(reads) < 2:
            route.continue_()
        else:
            route.fulfill(
                status=410,
                body="<!doctype html><title>Gone</title><p>No longer current.</p>",
                content_type="text/html",
            )

    page.route("**/report-export-pending", status)
    page.goto(component_origin + "/report-export-pending")
    visible(
        page.get_by_text(
            "Couldn't check for updates. Reload the page or sign in again."
        )
    )
    settled = len(reads)
    page.wait_for_timeout(4000)
    assert len(reads) == settled == 2
    # No retry line, in any wording (#1644).
    assert page.get_by_text(re.compile(r"Couldn't check .*retrying")).count() == 0


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


# Each statistics tile's label, value text and value line's height.
TILES = f"""() => [...document.querySelectorAll('{POPULATION} .stat-tile')]
  .map(tile => [tile.querySelector('.stat-label').innerText,
                tile.querySelector('.stat-value').firstChild.textContent.trim(),
                tile.querySelector('.stat-value').getBoundingClientRect().height])"""


def test_unknown_statistics_read_a_dash_with_a_reason(
    page, component_origin, axe_source
):
    """ui-conventions "Stat tiles" (#1540): without ParishSoft data every
    Active population tile reads "—" (never zero, never a word), its toggletip
    says why, its value line is as tall as a known value's (so a value
    that becomes unknown in place moves nothing), and the page passes
    axe."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/participation")
    known_height = page.evaluate(TILES)[0][2]
    page.goto(component_origin + "/participation-unknown")
    tiles = page.evaluate(TILES)
    assert [(label, text) for label, text, _height in tiles] == [
        ("Active Families", "—"),
        ("Active Members", "—"),
        ("Current annual pledges", "—"),
        ("Last year's pledges (all Families)", "—"),
    ]
    assert all(abs(height - known_height) <= 1 for _label, _text, height in tiles)
    assert page.locator("#response-summary [data-chart]").count() == 0
    tile = page.locator('[data-stat="active_families"]')
    tile.get_by_role("button", name="More information").click()
    visible(
        tile.get_by_text("No ParishSoft data has been loaded for this campaign yet.")
    )
    group = page.get_by_role("list", name="Active population")
    assert group.locator(".stat-tile").count() == 4
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
