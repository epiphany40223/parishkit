"""The response dashboard draws its charts under the CSP, accessibly (#477)."""

import pytest

from .response_dashboard_components import PATH
from .test_charts import assert_clean, watch

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


def test_dashboard_renders_tiles_and_both_charts_cleanly(
    page, component_origin, axe_source
):
    """Tiles first, then both charts drawn with no policy violation or error.

    The activity line drops to zero in the quiet hours between busy ones, the
    page fits a phone-width window, and axe finds nothing to report.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/response-dashboard")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    assert page.locator(".stat-tile").count() == 5
    assert page.locator(".stat-value").all_inner_texts() == ["5", "6", "4", "3", "3"]
    # The tiles come before the charts in reading order.
    tiles = page.locator(".stat-tiles").bounding_box()
    funnel = page.locator("[data-chart='response-funnel-spec']").bounding_box()
    assert tiles["y"] < funnel["y"]
    # Four hours, one point per series each: the two quiet hours are zeros.
    activity = page.locator("[data-chart='response-activity-spec']")
    assert activity.locator("svg g.mark-symbol.role-mark path").count() == 12
    assert_clean(page, errors)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_function(
        "document.querySelector(\"[data-chart='response-activity-spec'] svg\")"
        ".getBoundingClientRect().width < 390"
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
    assert "no Testing responses to show" in page.locator("main").inner_text()
    current = page.locator(".choice-links a[aria-current=page]")
    assert current.all_inner_texts() == ["Testing rehearsal"]
    assert page.locator("[data-chart]").count() == 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


@pytest.mark.parametrize(
    ("path", "rules"),
    [("/response-dashboard-empty", 0), ("/response-dashboard-empty-sends", 2)],
)
def test_empty_campaign_renders_cleanly(
    page, component_origin, axe_source, path, rules
):
    """Nothing invited or followed yet: zero tiles, a plain sentence, two charts.

    Both charts still draw (the activity chart with only its send markers,
    when there are any) with no policy violation, and axe is clean.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + path)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    assert page.locator(".stat-value").all_inner_texts() == ["0"] * 5
    main = page.locator("main").inner_text()
    assert "No invitations have been delivered yet." in main
    assert "compared with invited" not in main
    assert "No link follows, form opens or submissions yet." in main
    activity = page.locator("[data-chart='response-activity-spec']")
    assert activity.locator("svg g.mark-rule.role-mark line").count() == rules
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_switches_refresh_the_dashboard_in_place(page, component_origin):
    """Grain and mode change the page without a reload or a jump.

    The page's own marker survives (no navigation), the scroll position is
    kept, the chart is drawn again at the other grain, the address follows
    the choice, and keyboard focus stays on the chosen switch.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.set_viewport_size({"width": 1000, "height": 700})
    page.goto(component_origin + PATH)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    page.wait_for_function(FITTED)
    page.evaluate("window.__dashboardMarker = 1")
    day = page.locator('[data-region-link="grain-day"]')
    day.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, 100)")
    before = page.evaluate("window.scrollY")
    assert before > 0
    day.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**?grain=day")
    heading = page.locator("#activity-heading")
    expect(heading).to_have_text("Response activity by day")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    page.wait_for_function(FITTED)
    assert page.evaluate("window.__dashboardMarker") == 1
    assert abs(page.evaluate("window.scrollY") - before) < 2
    assert page.evaluate("document.activeElement.dataset.regionLink") == "grain-day"
    assert (
        page.locator('[data-region-link="grain-day"]').get_attribute("aria-current")
        == "page"
    )
    # The redrawn chart is the daily one: one day, three points.
    activity = page.locator("[data-chart='response-activity-spec']")
    assert activity.locator("svg g.mark-symbol.role-mark path").count() == 3
    assert_clean(page, errors)
    # The mode switch works the same way; the empty Testing view replaces it.
    page.locator('[data-region-link="mode-testing"]').click()
    page.wait_for_url("**mode=testing*")
    expect(page.locator("main")).to_contain_text("no Testing responses to show")
    assert page.evaluate("window.__dashboardMarker") == 1
    assert page.evaluate("document.activeElement.dataset.regionLink") == "mode-testing"
    assert page.locator("[data-chart]").count() == 0
    assert_clean(page, errors)


def test_switches_are_plain_links_without_scripts(browser_engine, component_origin):
    """With no JavaScript a switch loads the other view as an ordinary page."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + PATH)
        page.locator('[data-region-link="grain-day"]').click()
        page.wait_for_url("**?grain=day")
        assert page.locator("#activity-heading").inner_text() == (
            "Response activity by day"
        )
    finally:
        context.close()
