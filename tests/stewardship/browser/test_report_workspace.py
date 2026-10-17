"""Real report templates retain exact values, native forms and accessible controls."""

from uuid import UUID

import pytest
from django.urls import reverse

from .test_campaign_comparison import COLLISIONS
from .test_charts import assert_clean, watch
from .test_response_dashboard import AXE, FITTED
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


# True when every y-axis label of the statistics chart ends left of the
# bars and starts inside the chart (#720).
INSIDE_AXIS = """(() => {
  const svg = document.querySelector('#participation-statistics svg');
  const left = svg.getBoundingClientRect().left;
  const bars = Math.min(...[...svg.querySelectorAll('g.mark-rect path')]
    .map((bar) => bar.getBoundingClientRect().left));
  return [...svg.querySelectorAll('.role-axis-label text:has(tspan)')]
    .every((text) => {
      const box = text.getBoundingClientRect();
      return box.left >= left && box.right <= bars;
    });
})()"""


def test_statistics_draw_as_progress_bars_with_labelled_values(
    page, component_origin, axe_source
):
    """The statistics panel is tiles, then progress bars (#720).

    The chart draws under the CSP with no script error and is named and
    focusable; a bar's tooltip and accessible name have its count and share
    of active Families; each bar's axis label is its name over its value,
    in the cards' order, so there is no Exact values table (#1266); and at
    320 and 1280 pixels the page fits its window with every label inside
    the chart and no text drawn over other text.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/participation")
    panel = page.locator("#participation-statistics")
    expect(panel.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert panel.locator(".stat-label").all_inner_texts() == [
        "Active Families",
        "Active Members",
        "Current annual pledges",
        "Last year's pledges (all Families)",
    ]
    view = panel.locator("[data-chart-view]")
    assert view.get_attribute("aria-label") == "Responses and email reach"
    assert view.get_attribute("tabindex") == "0"
    # The summary would repeat the axis labels' values, so it is not shown;
    # it stays the view's description for assistive technology.
    caption = panel.locator("figcaption")
    assert view.get_attribute("aria-describedby") == caption.get_attribute("id")
    assert caption.inner_text().startswith("Responses and email reach: Families")
    assert caption.bounding_box()["width"] <= 1
    ticks = panel.locator("svg .role-axis-label text:has(tspan)")
    assert [tick.locator("tspan").all_text_contents() for tick in ticks.all()] == [
        ["Families that have responded", "2 out of 1,000 (0.2%)"],
        ["Families with eligible email", "900 out of 1,000 (90%)"],
        ["Families with deliverable email", "800 out of 1,000 (80%)"],
    ]
    # The grey tracks, then the three bars.
    bars = panel.locator("svg g.mark-rect")
    assert bars.nth(0).locator("path").count() == 3
    bars.nth(1).locator("path").nth(1).hover()
    tooltip = page.locator("#vg-tooltip-element")
    visible(tooltip)
    assert "eligible email" in tooltip.inner_text() and "90%" in tooltip.inner_text()
    # Pointer away: axe must not catch the tooltip while it fades out.
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()
    assert bars.nth(1).locator("path").nth(1).get_attribute("aria-label") == (
        "Families with eligible email: 900 out of 1,000 (90%)"
    )
    # The values are in the chart, so there is no Exact values table (#1266).
    expect(panel.locator(".chart-table")).to_have_count(0)
    expect(panel.get_by_text("Exact values")).to_have_count(0)
    assert_clean(page, errors)
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        eventually(page, FITTED)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Every label stays inside the chart and clear of the bars, and no
        # text is drawn over other text or cut off (#1266).
        assert page.evaluate(INSIDE_AXIS)
        assert page.evaluate(COLLISIONS) == []
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert_clean(page, errors)


def test_report_and_export_pages_offer_their_actions(page, component_origin):
    """Scope, exact values and each export state's action are on the page."""
    page.goto(component_origin + "/participation")
    assert "$3,234.56" in page.locator("[data-table-region] table").inner_text()
    visible(page.get_by_role("button", name="Apply report options"))
    visible(page.get_by_role("button", name="Generate export"))
    visible(page.get_by_role("button", name="Queue export with the latest data"))
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
    page.wait_for_timeout(4000)
    assert len(reads) == settled == 2
    assert page.get_by_text("Couldn't check status").count() == 0


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
        page.get_by_text("Couldn't check status. Refresh the page or sign in again.")
    )
    settled = len(reads)
    page.wait_for_timeout(4000)
    assert len(reads) == settled == 2
    assert page.get_by_text("Couldn't check status — retrying.").count() == 0


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
TILES = """() => [...document.querySelectorAll('#participation-statistics .stat-tile')]
  .map(tile => [tile.querySelector('.stat-label').innerText,
                tile.querySelector('.stat-value').firstChild.textContent.trim(),
                tile.querySelector('.stat-value').getBoundingClientRect().height])"""


def test_unknown_statistics_read_a_dash_with_a_reason(
    page, component_origin, axe_source
):
    """ui-conventions "Stat tiles" (#1540): without ParishSoft data every
    statistics tile reads "—" (never zero, never a word), its toggletip
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
        ("Families with eligible email", "—"),
        ("Families with deliverable email", "—"),
        ("Families that have responded", "—"),
        ("Current annual pledges", "—"),
        ("Last year's pledges (all Families)", "—"),
    ]
    assert all(abs(height - known_height) <= 1 for _label, _text, height in tiles)
    assert page.locator("#participation-statistics [data-chart]").count() == 0
    tile = page.locator('[data-stat="active_families"]')
    tile.get_by_role("button", name="More information").click()
    visible(
        tile.get_by_text("No ParishSoft data has been loaded for this campaign yet.")
    )
    group = page.get_by_role("list", name="Current active population statistics")
    assert group.locator(".stat-tile").count() == 7
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
