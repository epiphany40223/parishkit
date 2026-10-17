"""The Response dashboard draws its charts under the CSP, accessibly (#477, #1537)."""

import re
from urllib.parse import urlencode

import pytest
from django.urls import reverse

from ..test_directories import RESPONSE_LIST_PRESETS
from .report_components import REAL
from .test_charts import assert_clean, watch
from .waits import eventually, has_text, visible

DIRECTORY = reverse("admin:family_directory")
ZONE = "timezone=America%2FLos_Angeles"

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Every drawn chart is as wide as its panel (chart-v1.js refits a chart whose
# panel changed width after it was drawn).
FITTED = """() => [...document.querySelectorAll("[data-chart-view] svg")].every(
  svg => Math.abs(
    Number(svg.getAttribute("width")) - svg.parentElement.clientWidth
  ) <= 1)"""

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact, nodes}) => ({
  id, impact, html: nodes.map(node => node.html.slice(0, 300))
}))"""


def test_dashboard_renders_tiles_and_charts_cleanly(page, component_origin, axe_source):
    """Families by stage, then the funnel, the statistics and the daily
    chart, all drawn with no policy violation or error (#1537).

    The page fits a phone-width window, and axe finds nothing to report.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard")
    # The funnel, the statistics' progress bars and the daily chart.
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(3)
    stages = page.locator("#response-stages")
    assert stages.locator(".stat-tile").count() == 5
    assert stages.locator(".stat-value").all_inner_texts() == ["5", "6", "4", "3", "3"]
    # The tiles come before the funnel, and the funnel before the statistics.
    tiles = stages.locator(".stat-tiles").bounding_box()
    funnel = page.locator("[data-chart='response-funnel-spec']").bounding_box()
    statistics = page.locator("#participation-statistics").bounding_box()
    assert tiles["y"] < funnel["y"] < statistics["y"]
    # No activity chart, no Also counted figures, no lists of Families, and
    # the funnel has no exact-values table (#1537).
    assert page.locator("[data-chart='response-activity-spec']").count() == 0
    main = page.locator("main").inner_text()
    assert "Also counted" not in main and "Lists of Families" not in main
    assert page.locator("#response-funnel .chart-table").count() == 0
    assert_clean(page, errors)
    page.set_viewport_size({"width": 390, "height": 844})
    eventually(
        page,
        "document.querySelector(\"[data-chart='response-funnel-spec'] svg\")"
        ".getBoundingClientRect().width < 390",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_testing_view_without_a_rehearsal_is_accessible(
    page, component_origin, axe_source
):
    """The empty Testing view says why it is empty and marks the mode chosen."""
    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-testing")
    main = page.locator("main").inner_text()
    assert "no Testing responses to show" in main
    assert "count Production responses only" in main
    current = page.get_by_role("group", name="Responses shown").locator(
        "a[aria-current=page]"
    )
    assert current.all_inner_texts() == ["Testing rehearsal"]
    assert page.locator("[data-chart]").count() == 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_testing_view_shows_the_rehearsal_funnel_only(
    page, component_origin, axe_source
):
    """During a rehearsal, Families by stage and the funnel show it; the
    Production-only panels are left out, with one line saying why (#1537)."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-rehearsal")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert page.locator("#response-stages .stat-tile").count() == 5
    visible(page.get_by_text("never counted in Production"))
    visible(page.locator("#production-only"))
    for gone in ("#participation-statistics", "#participation-chart", "#export-format"):
        assert page.locator(gone).count() == 0
    assert page.get_by_role("group", name="Chart population").count() == 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_empty_campaign_renders_cleanly(page, component_origin, axe_source):
    """Nothing invited or followed yet: zero tiles, a plain sentence, and the
    funnel still drawn, with no policy violation; axe is clean."""
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard-empty")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(3)
    assert page.locator("#response-stages .stat-value").all_inner_texts() == ["0"] * 5
    main = page.locator("#response-stages").inner_text()
    assert "No invitations have been delivered yet." in main
    assert "compared with invited" not in main
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_mode_choice_refreshes_the_dashboard_in_place(page, component_origin):
    """Testing rehearsal and Production change the page without a reload or
    a jump (#519, #1537).

    The page's own marker survives (no navigation), the scroll position is
    kept, the address follows the choice (and the region's fragment),
    keyboard focus stays on the chosen segment, and the live region names
    the view now shown. The Testing view leaves out the Production panels.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.set_viewport_size({"width": 1000, "height": 700})
    page.goto(component_origin + REAL + "?" + ZONE)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(3)
    eventually(page, FITTED)
    page.evaluate("window.__dashboardMarker = 1")
    testing = page.locator('[data-in-place="mode-testing"]')
    testing.scroll_into_view_if_needed()
    before = page.evaluate("window.scrollY")
    testing.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**mode=testing*#response-dashboard")
    expect(page.locator("#production-only")).to_be_visible()
    expect(page.locator("#participation-statistics")).to_have_count(0)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert page.evaluate("window.__dashboardMarker") == 1
    assert abs(page.evaluate("window.scrollY") - before) < 2
    assert page.evaluate("document.activeElement.dataset.inPlace") == "mode-testing"
    has_text(
        page.get_by_role("status").filter(has_text="Testing rehearsal"),
        "Testing rehearsal",
    )
    assert testing.get_attribute("aria-current") == "page"
    assert_clean(page, errors)
    # Production again: the statistics, chart and export come back.
    page.locator('[data-in-place="mode-production"]').click()
    expect(page.locator("#participation-statistics")).to_have_count(1)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(3)
    assert "mode=" not in page.url
    assert page.evaluate("window.__dashboardMarker") == 1
    assert page.evaluate("document.activeElement.dataset.inPlace") == "mode-production"
    assert_clean(page, errors)


@pytest.mark.parametrize(
    ("preset", "response", "check", "columns"),
    [
        ("submitted", "submitted", "any", ["Submissions"]),
        (
            "not-opened",
            "never-opened",
            "any",
            ["Invitation delivered", "Link followed"],
        ),
        ("data-quality", "any", "anything", ["What to check"]),
    ],
)
def test_list_presets_open_the_directory_with_their_choices(
    page, component_origin, preset, response, check, columns
):
    """A saved list link opens the directory with that list's choices.

    The link carries only closed presets (#933); the directory shows them
    chosen, with the list's columns, and a further filter change posts
    privately to the directory's own address, with no query string. The
    answer's data-page-address then replaces the preset in the address bar
    (#536), so Reload or Back cannot bring the preset back: here the answer
    has a search applied, so the address is the bare page.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    query = urlencode(RESPONSE_LIST_PRESETS[preset])
    page.goto(component_origin + DIRECTORY + "?" + query)
    expect(page.get_by_label("Response", exact=True)).to_have_value(response)
    assert page.get_by_label("ParishSoft data to check").input_value() == check
    assert page.get_by_label("Include response columns").is_checked() is (
        response != "any"
    )
    headings = [
        text.split("\n")[0].strip()
        for text in page.locator("table thead th").all_inner_texts()
    ]
    assert all(column in headings for column in columns), headings
    preset_url = page.url
    assert "?" in preset_url
    answer = page.request.get(component_origin + "/family-directory").text()
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert f"response={response}" in sent.value.post_data
    assert "?" not in sent.value.url
    expect(page).to_have_url(preset_url.partition("?")[0])
    assert_clean(page, errors)


def test_testing_links_open_the_directory_testing_switch(page, component_origin):
    """A Testing list link opens the directory in Testing rehearsal (#933).

    The directory shows the Responses shown switch set to Testing rehearsal,
    says so in a notice and leaves out the Production-only Responded column.
    Switching back to Production is a filter like the others: Apply posts
    it privately, the answer replaces the table in place without moving the
    filter bar, and the address bar drops ``mode=testing``.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    query = urlencode(RESPONSE_LIST_PRESETS["submitted"] | {"mode": "testing"})
    page.goto(component_origin + DIRECTORY + "?" + query)
    expect(page).to_have_url(re.compile(r"[?&]mode=testing$"))
    switch = page.get_by_label("Responses shown", exact=True)
    expect(switch).to_have_value("testing")
    expect(page.locator("[data-testing-responses]")).to_be_visible()
    headings = page.locator("table thead th").all_inner_texts()
    assert not any(text.strip() == "Responded" for text in headings), headings
    production = (
        component_origin
        + DIRECTORY
        + ("?response=submitted&responses=yes&sort=submitted")
    )
    answer = page.request.get(production).text()
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    # The filter bar's place on the page (not in the viewport: the answer
    # may scroll to the table).
    layout = """() => {
        const box = document.querySelector('#table-filters').getBoundingClientRect();
        return [box.x + scrollX, box.y + scrollY, box.width, box.height];
    }"""
    before = page.evaluate(layout)
    switch.select_option("production")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "mode=production" in sent.value.post_data
    assert "?" not in sent.value.url
    expect(page).to_have_url(production)
    expect(page.locator("[data-testing-responses]")).to_have_count(0)
    expect(page.locator("table thead th", has_text="Responded")).to_have_count(1)
    assert page.evaluate(layout) == pytest.approx(before, abs=1)
    assert_clean(page, errors)


def test_switch_sends_mode_once_after_the_rehearsal_ends(page, component_origin):
    """An answer drawn without the switch never doubles the mode (#933).

    If the rehearsal ends after the page loads, the in-place answer has no
    Responses shown switch. The page keeps its own select, and since the
    fresh form carries no hidden ``mode`` either, the next Apply still
    sends ``mode`` once (the server then refuses Testing with its 409),
    never twice, which every later Apply would answer with a 400.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    testing = DIRECTORY + "?response=started&responses=yes&mode=testing"
    page.goto(component_origin + testing)
    answer = page.request.get(component_origin + "/family-directory").text()
    assert 'id="directory-mode"' not in answer
    page.route(
        "**/families/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    for _ in range(2):
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert sent.value.post_data.count("mode=") == 1, sent.value.post_data
        # The answer (no rehearsal notice) is in place before the next Apply.
        expect(page.locator("[data-testing-responses]")).to_have_count(0)
    assert_clean(page, errors)


# Each stage tile's share bar, with the tile's and the bar's boxes, read from
# the page itself so no assertion depends on content above the tiles.
TILE_BARS = """() => [...document.querySelectorAll('#response-stages .stat-tile')]
  .map(tile => {
  const bar = tile.querySelector('.stat-bar progress');
  const t = tile.getBoundingClientRect(), b = bar.getBoundingClientRect();
  return {value: bar.value, max: bar.max,
          given: Number(bar.getAttribute('value')),
          hidden: bar.closest('[aria-hidden=true]') !== null,
          top: Math.round(t.top), tileBottom: t.bottom, barTop: b.top,
          barBottom: b.bottom, barWidth: b.width, tileWidth: t.width};
})"""


@pytest.mark.parametrize("width", [1280, 390])
def test_stage_tiles_draw_their_share_bars_at_their_feet(page, component_origin, width):
    """Every tile has a share bar of Invited, at its foot, hidden from AT (#720).

    Invited's bar is full, and so is Link followed, whose share passes
    100%: the fixture has 6 links followed for 5 invited, so the bar is
    given 6 and the browser draws it full (its value reads back clamped to
    5). Tiles in one row stretch to one height, so their bars line up
    whatever their labels' wrapping.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/response-dashboard")
    bars = page.evaluate(TILE_BARS)
    assert [(bar["value"], bar["max"]) for bar in bars] == [
        (5, 5),
        (5, 5),
        (4, 5),
        (3, 5),
        (3, 5),
    ]
    assert [bar["given"] for bar in bars] == [5, 6, 4, 3, 3]
    assert all(bar["hidden"] for bar in bars)
    for bar in bars:
        # Inside the tile, near its foot, and nearly as wide as the tile.
        assert bar["barBottom"] <= bar["tileBottom"]
        assert bar["tileBottom"] - bar["barBottom"] < 40
        assert bar["barWidth"] > bar["tileWidth"] * 0.6
    rows = {}
    for bar in bars:
        rows.setdefault(bar["top"], []).append(round(bar["barTop"]))
    for tops in rows.values():
        assert max(tops) - min(tops) <= 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
