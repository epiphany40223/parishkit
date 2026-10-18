"""The Response dashboard's Chart population applies in place (#519 PR 5).

The Response dashboard (#1537) is served at its real address
(report_components.REAL), where its Chart population links (a binary
choice, #1535) lead, with the answers for the choices these tests make.
Each test sets a mark outside every region, which only a full page load
could lose, as test_in_place.py does.
"""

import pytest

from .report_components import REAL
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .waits import eventually, has_text, visible


def expect(page_or_locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(page_or_locator)


pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The Chart population's Current population segment (#1535).
CURRENT = '#participation-scope a[data-in-place="scope-current"]'
# The binary choice's box and each segment's, in the viewport: choosing a
# segment must change only colours, never a size or a place (#1535), and
# nothing above may move it under the reader's pointer (#1537). Measured
# against the viewport, not the page or its panel, so a taller statistics
# row above fails here even where the browser's scroll anchoring would
# hide it (WebKit has none).
PAIR_LAYOUT = """() => [...document.querySelectorAll(
    '#participation-scope, #participation-scope a')].map((node) => {
  const box = node.getBoundingClientRect();
  return [box.x, box.y, box.width, box.height];
}).flat()"""
ZONE = "timezone=America%2FLos_Angeles"
# The current-population answer's all-Families comparison value, which the
# first page lacks ($1,000.00 there): it shows that the Summary's Active
# population row was swapped in place. It is wider, so on Linux fonts it
# once wrapped onto a second line and made the row taller (#1537); a value
# is now one line.
REFRESHED = "$982,657.00"
# The daily chart panel's top, measured from the top of the Summary
# panel: the swapped-in Summary keeps its height (each tile value is one
# line, #1537), so the panel below it must not move. PAIR_LAYOUT checks
# that nothing above the Chart population moved in the viewport.
CHART_TOP = (
    "document.getElementById('participation-chart').getBoundingClientRect().top"
    " - document.getElementById('response-summary').getBoundingClientRect().top"
)
# The export panel's top, measured from the top of the daily chart panel:
# the redrawn daily chart keeps its space, so the panel below it must not
# move (#720). Relative, so the page above (and its fonts) does not matter.
EXPORT_TOP = (
    "document.getElementById('participation-export').getBoundingClientRect().top"
    " - document.getElementById('participation-chart').getBoundingClientRect().top"
)


def zones(page):
    """The zone the export form carries (the page has one, #1537)."""
    return page.locator("input[name=browser_timezone]").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    )


def test_options_apply_in_place(page, component_origin):
    """Choosing Current population (a binary choice, #1535) sends one GET
    and swaps the dashboard's region: no reload, no Apply
    button, the reader's place kept, focus on the segment just chosen, the
    view announced, the address replaced, the chosen segment marked, the
    pair not moved or resized, and the fresh daily chart drawn."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + REAL + "?" + ZONE)
    page.evaluate(MARK)
    choice = page.get_by_role("group", name="Chart population")
    expect(choice.locator("a[aria-current=page]")).to_have_text("Historical as of day")
    assert page.get_by_role("button", name="Apply report options").count() == 0
    # The inactive subtotal option was removed (#728).
    expect(page.locator("input[name=inactive]")).to_have_count(0)
    offset = scroll_below(page, CURRENT)
    layout = page.evaluate(PAIR_LAYOUT)
    gets = count_requests(page, "GET", "scope=current")
    page.locator(CURRENT).click()
    visible(
        page.get_by_role("heading", name="Daily participation — Current population")
    )
    visible(page.locator("#response-summary .stat-value", has_text=REFRESHED))
    assert page.evaluate(MARKED) == "kept"
    # Exactly the same place: scroll anchoring would change it to
    # follow content that moved above.
    assert page.evaluate("window.scrollY") == pytest.approx(offset, abs=1)
    eventually(
        page,
        "() => document.activeElement?.dataset.inPlace === 'scope-current'",
        True,
    )
    has_text(
        page.get_by_role("status").filter(has_text="Current population"),
        "Current population",
    )
    expect(choice.locator("a[aria-current=page]")).to_have_text("Current population")
    assert page.evaluate(PAIR_LAYOUT) == pytest.approx(layout, abs=1)
    # The default sort (newest first) is left out of the address (#1534).
    assert page.url == (
        component_origin + REAL + f"?scope=current&page=1&{ZONE}#response-dashboard"
    )
    assert len(gets) == 1
    # The swapped-in daily chart is drawn by chart-v1.js (#720).
    expect(
        page.locator("#participation-chart [data-chart-state=rendered]")
    ).to_have_count(1)


def test_options_keep_the_panels_in_place(page, component_origin):
    """Choosing a Chart population swaps in the Summary's fresh tiles and
    redraws the daily chart, its only chart (#1537), and each keeps its
    space, so the panels below stay as far below as they were (#720, #736)."""
    from .test_charts import assert_clean, watch
    from .test_response_dashboard import FITTED

    errors = watch(page)
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + REAL + "?" + ZONE)
    rendered = page.locator("[data-chart-state=rendered]")
    daily = page.locator("#participation-chart [data-chart-state=rendered]")
    expect(daily).to_have_count(1)
    expect(rendered).to_have_count(1)
    eventually(page, FITTED)
    top = page.evaluate(CHART_TOP)
    export = page.evaluate(EXPORT_TOP)
    page.locator(CURRENT).click()
    visible(page.locator("#response-summary .stat-value", has_text=REFRESHED))
    expect(daily).to_have_count(1)
    expect(rendered).to_have_count(1)
    eventually(page, FITTED)
    assert abs(page.evaluate(CHART_TOP) - top) < 2
    assert abs(page.evaluate(EXPORT_TOP) - export) < 2
    assert_clean(page, errors)


def test_daily_table_refreshes_every_panel(page, component_origin):
    """Sorting the daily table refreshes the Summary and the Chart
    population from the same answer, as the whole region is refreshed."""
    page.goto(component_origin + REAL + "?" + ZONE)
    page.evaluate(MARK)
    body = page.request.get(
        component_origin + REAL + f"?scope=current&page=1&{ZONE}"
    ).text()
    page.route(
        lambda url: "sort=" in url,
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=body
        ),
    )
    page.locator("#table a.sort-link").first.click()
    visible(page.locator("#response-summary .stat-value", has_text=REFRESHED))
    choice = page.get_by_role("group", name="Chart population")
    expect(choice.locator("a[aria-current=page]")).to_have_text("Current population")
    assert page.evaluate(MARKED) == "kept"


def test_browser_zone_is_applied_in_place_once(page, component_origin):
    """A report opened without a zone loads the same address with this
    browser's zone in place: one fetch, not a second page load, the address
    replaced, the export panels carrying the zone, and neither focus moved
    nor anything announced, since the reader did not act. An explicit zone
    in the address is kept."""
    documents, fetches = [], []
    page.on(
        "request",
        lambda request: (
            (documents if request.resource_type == "document" else fetches).append(
                request.url
            )
            if REAL in request.url
            else None
        ),
    )
    page.goto(component_origin + REAL)
    expect(page).to_have_url(component_origin + REAL + "?" + ZONE)
    assert zones(page) == ["America/Los_Angeles"]
    assert documents == [component_origin + REAL]
    assert fetches == [component_origin + REAL + "?" + ZONE]
    assert page.evaluate("document.activeElement === document.body")
    assert page.get_by_role("status").filter(has_text="Current population").count() == 0
    # The Chart population's links carry the zone too (#1535).
    for link in page.locator("#participation-scope a").all():
        assert ZONE in link.get_attribute("href")
    page.goto(component_origin + REAL + "?timezone=UTC")
    expect(page.locator("[data-report-options]")).to_have_attribute(
        "data-report-zone", "UTC"
    )
    assert page.url.endswith("?timezone=UTC")


def test_browser_zone_keeps_the_address_and_its_fragment(page, component_origin):
    """Applying the zone keeps the address's other parameters (the daily
    table's sort) and its fragment."""
    page.goto(component_origin + REAL + "?sort=date_asc#participation-chart")
    expect(page).to_have_url(
        component_origin + REAL + f"?sort=date_asc&{ZONE}#participation-chart"
    )
    assert zones(page) == ["America/Los_Angeles"]


def test_browser_zone_falls_back_without_a_jump(page, component_origin):
    """When the in-place request gets no answer, the same address with the
    zone is loaded the ordinary way, landing at the top of the page rather
    than at a panel."""
    failed = []

    def answer(route):
        """Drop the in-place fetch; let the ordinary load through."""
        if route.request.resource_type == "fetch" and not failed:
            failed.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    page.route(lambda url: ZONE in url, answer)
    page.goto(component_origin + REAL)
    expect(page).to_have_url(component_origin + REAL + "?" + ZONE)
    assert failed == [component_origin + REAL + "?" + ZONE]
    assert zones(page) == ["America/Los_Angeles"]


def test_a_link_followed_before_the_zone_applies_gets_it_too(page, component_origin):
    """A daily-table link followed while the zone is still being applied
    cancels that request, and the page it brings has no zone (UTC). The zone
    is then applied to that page in turn, its sort kept."""
    page.goto(component_origin + REAL + "?scope=historical")
    utc = page.request.get(component_origin + REAL + "?scope=historical").text()
    zoned = page.request.get(component_origin + REAL + "?" + ZONE).text()

    def answer(route):
        """The sort link's page without a zone, then with this browser's."""
        body = zoned if ZONE in route.request.url else utc
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    page.route(lambda url: "sort=" in url, answer)
    # The zone's own request is answered after a pause (SLOW_GETS).
    page.locator("#table a.sort-link").first.click()
    eventually(
        page,
        f"() => location.href.includes('{ZONE}') && location.href.includes('sort=')",
        True,
    )
    expect(page.locator("input[name=browser_timezone]").first).to_have_value(
        "America/Los_Angeles"
    )
    assert zones(page) == ["America/Los_Angeles"]


# Each component-drawn value's overflow past its tile's border box, in px,
# after writing ``text`` (at its StatTile.fit size) into every one: 0 when
# it stays inside. Measured on a range around the text, not the value's
# element: a tile is a flex column, so the element is stretched to the
# tile's width and only the text inside it runs past the border.
OVERFLOW = """([text, fit]) => [...document.querySelectorAll(
    '.stat-tile[data-stat]')].map((tile) => {
  const value = tile.querySelector('.stat-value');
  value.textContent = text;
  value.className = ('stat-value ' + fit).trim();
  const outer = tile.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(value);
  const inner = range.getBoundingClientRect();
  return Math.max(0, outer.left - inner.left, inner.right - outer.right);
})"""


@pytest.mark.parametrize("width", [760, 1100, 1600])
@pytest.mark.parametrize("text", ["$9,999,999.99", "$1,000.00"])
@pytest.mark.parametrize("font_size", ["100%", "150%"])
def test_long_values_stay_inside_their_tiles(
    page, component_origin, width, text, font_size
):
    """A one-line value stays inside its tile (#1537) at the widths where a
    four-across row once sat at its narrowest column: without a wider
    minimum column, a 13-character money value at the long size and a
    9-character one at full size ran past the tile's border. 150% stands
    in for a reader's 24px default font size."""
    from parishkit.stewardship.web.stat_tiles import StatTile

    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + REAL + "?" + ZONE)
    page.evaluate(f"document.documentElement.style.fontSize = '{font_size}'")
    fit = StatTile(key="probe", label="Probe", text=text).fit
    overflow = page.evaluate(OVERFLOW, [text, fit])
    assert len(overflow) >= 4
    assert overflow == [0] * len(overflow)
