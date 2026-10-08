"""The participation report's options apply in place (#519 PR 5).

The report is served at its real address (report_components.REAL), where
its options' form sends them, with the answers for the choices these tests
make. Each test sets a mark outside every region, which only a full page
load could lose, as test_in_place.py does.
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

APPLY = "#table-filters button[type=submit]"
ZONE = "timezone=America%2FLos_Angeles"
# The current-population answer's all-Families comparison card, which the
# first page lacks: it shows that the statistics panel was swapped in place.
REFRESHED = "$982,657.00"
# Counts every AbortController.abort() call, so a test can see that a
# swapped-out chart's page-wide listeners were removed.
COUNT_ABORTS = """(() => {
    const abort = AbortController.prototype.abort;
    window.aborts = 0;
    AbortController.prototype.abort = function (...args) {
        window.aborts += 1;
        return abort.apply(this, args);
    };
})()"""


def zones(page):
    """The zone each export form carries."""
    return page.locator("input[name=browser_timezone]").evaluate_all(
        "nodes => nodes.map(node => node.value)"
    )


def test_options_apply_in_place(page, component_origin):
    """Apply report options sends one GET and swaps the statistics, chart and
    export panels: no reload, the reader's place and focus kept, the options
    announced, the address replaced, and the fresh chart's date slider wired
    again while the old chart's page-wide listeners are removed."""
    page.add_init_script(COUNT_ABORTS)
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + REAL + "?" + ZONE)
    page.evaluate(MARK)
    page.get_by_label("Chart population").select_option("current")
    # The inactive subtotal option was removed (#728).
    expect(page.locator("input[name=inactive]")).to_have_count(0)
    offset = scroll_below(page, APPLY)
    gets = count_requests(page, "GET", "scope=current")
    page.locator(APPLY).click()
    visible(
        page.get_by_role("heading", name="Daily participation — Current population")
    )
    visible(page.locator("#participation-statistics dd", has_text=REFRESHED))
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate(
        f"document.activeElement === document.querySelector('{APPLY}')"
    )
    has_text(
        page.get_by_role("status").filter(has_text="Report options applied."),
        "Report options applied.",
    )
    assert page.url == (
        component_origin
        + REAL
        + f"?scope=current&{ZONE}"
        + "&size=50&sort=date_asc#participation-statistics"
    )
    assert len(gets) == 1
    assert page.locator("input[name=population_scope]").input_value() == "current"
    # One chart was swapped out: its listeners' controller, and nothing else.
    assert page.evaluate("window.aborts") == 1
    # The swapped-in chart's slider shows its tooltip, so digest-v1.js wired
    # the fresh chart.
    page.get_by_role("slider", name="Inspect campaign date").focus()
    page.keyboard.press("Home")
    visible(page.locator("[data-digest-tip]"))


def test_daily_table_refreshes_every_panel(page, component_origin):
    """Sorting the daily table refreshes the statistics and export panels from
    the same answer, as every region on the page is refreshed."""
    page.goto(component_origin + REAL + "?" + ZONE)
    page.evaluate(MARK)
    body = page.request.get(
        component_origin + REAL + f"?scope=current&{ZONE}&size=50&sort=date_asc"
    ).text()
    page.route(
        lambda url: "sort=date_desc" in url,
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=body
        ),
    )
    page.locator("#table a.sort-link").first.click()
    visible(page.locator("#participation-statistics dd", has_text=REFRESHED))
    assert page.locator("input[name=population_scope]").input_value() == "current"
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
    assert zones(page) == ["America/Los_Angeles"] * 2
    assert documents == [component_origin + REAL]
    assert fetches == [component_origin + REAL + "?" + ZONE]
    assert page.evaluate("document.activeElement === document.body")
    assert page.locator("[data-digest-values]").inner_text() == ""
    assert "Report options applied." not in page.locator("main").inner_text()
    page.goto(component_origin + REAL + "?timezone=UTC")
    expect(page.locator("select[name=timezone]")).to_have_value("UTC")
    assert page.url.endswith("?timezone=UTC")


def test_browser_zone_keeps_the_address_and_its_fragment(page, component_origin):
    """Applying the zone keeps the address's other parameters (the daily
    table's sort) and its fragment."""
    page.goto(component_origin + REAL + "?sort=date_desc#participation-chart")
    expect(page).to_have_url(
        component_origin + REAL + f"?sort=date_desc&{ZONE}#participation-chart"
    )
    assert zones(page) == ["America/Los_Angeles"] * 2


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
    assert zones(page) == ["America/Los_Angeles"] * 2


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
    assert zones(page) == ["America/Los_Angeles"] * 2
