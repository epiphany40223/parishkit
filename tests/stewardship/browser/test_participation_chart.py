"""The Participation page's daily chart in the chart engine (#720 slice 2c).

The chart is drawn in the browser as stacked panels on one shared date
axis, with each date's figures in a tooltip; the daily table under it is
its exact values. The page is served at its real address
(report_components.REAL) with the digest test document's three days.
"""

import pytest

from .report_components import REAL
from .test_campaign_comparison import COLLISIONS
from .test_charts import assert_clean, watch
from .test_response_dashboard import AXE, FITTED
from .waits import eventually, painted

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

ZONE = "timezone=America%2FLos_Angeles"
DAILY = "#participation-chart"
# Each panel's plot box (Vega's group background), relative to the first
# panel's: the panels share one date axis, so their plots line up whatever
# their tick labels' widths. Relative, so fonts and page layout don't matter.
PLOTS = """() => {
  const boxes = [...document.querySelectorAll(
    "#participation-chart svg g.mark-group.role-scope > g > path.background"
  )].map((path) => path.getBoundingClientRect());
  return boxes.map((box) => [
    Math.round(box.left - boxes[0].left),
    Math.round(box.width - boxes[0].width),
    Math.round(box.top - boxes[0].top),
  ]);
}"""
TITLES = (
    "First submissions that day",
    "Cumulative participating Families",
    "Effective annual pledges",
)


def expect(page_or_locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(page_or_locator)


def drawn(page, origin):
    """Open the report and wait until its charts are drawn and fitted."""
    page.goto(origin + REAL + "?" + ZONE)
    expect(page.locator(f"{DAILY} [data-chart-state=rendered]")).to_have_count(1)
    eventually(page, FITTED)
    return page.locator(f"{DAILY} figure.chart")


@pytest.fixture
def chart(page, component_origin):
    """The daily chart, drawn at a desktop width, with errors and console
    warnings watched (Vega reports a spec it has to correct as a warning)."""
    page.errors = watch(page)
    page.warnings = []
    page.on(
        "console",
        lambda message: (
            page.warnings.append(message.text) if message.type == "warning" else None
        ),
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    return drawn(page, component_origin)


def test_daily_chart_is_panels_on_one_date_axis(page, chart):
    """Three titled panels, no legend and no second y-axis; the dates are
    labelled once, under the last panel; the plots line up; no image, date
    slider or Exact values disclosure remains, and the daily table follows."""
    texts = chart.locator("svg text").all_text_contents()
    for title in TITLES:
        assert texts.count(title) == 1
    assert chart.locator("svg g.mark-group.role-legend").count() == 0
    # One titled value axis per panel; one titled date axis, under the last.
    assert texts.count("Families") == 2 and texts.count("USD") == 1
    lines = chart.locator("svg tspan").all_text_contents()
    assert "Campaign date" in lines and "(America/New_York)" in lines
    # Short dollar ticks on the pledges panel ("$1K", "$2K", ...).
    assert any(text.startswith("$") and text.endswith("K") for text in texts)
    plots = page.evaluate(PLOTS)
    assert len(plots) == 3
    assert all(left == 0 and width == 0 for left, width, _top in plots)
    assert plots[0][2] < plots[1][2] < plots[2][2]
    # The PNG and its slider are gone; the daily table is the exact values.
    panel = page.locator(DAILY)
    assert panel.locator("img").count() == 0
    assert page.get_by_role("slider").count() == 0
    assert panel.locator(".chart-table").count() == 0
    assert panel.locator("table caption").inner_text().endswith("exact daily values")
    # The view is named and described, and takes focus; its summary is not
    # shown, since the table below has every figure.
    view = chart.locator("[data-chart-view]")
    assert view.get_attribute("aria-label") == (
        "Daily participation — Historical as of day"
    )
    summary = chart.locator("#" + view.get_attribute("aria-describedby"))
    assert summary.inner_text().startswith("Daily participation, Historical")
    assert "visually-hidden" in summary.get_attribute("class")
    view.focus()
    assert page.evaluate("document.activeElement.hasAttribute('data-chart-view')")
    assert page.evaluate(COLLISIONS) == []
    # Vega would name each mark and axis from raw fields (ISO dates,
    # unrounded dollars); they are hidden, so the view's name and summary
    # and the daily table are the accessible path. Only the panel titles
    # are named.
    assert (
        chart.locator("svg [role=graphics-symbol]").evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('aria-roledescription'))"
        )
        == ["title"] * 3
    )
    assert chart.locator("svg [aria-roledescription=axis]").count() == 0
    assert_clean(page, page.errors)
    assert page.warnings == []


TIP = (
    ("First submissions", "1"),
    ("Families that have responded", "3 out of 1,234 (0.2%)"),
    ("Effective annual pledges", "$3,235"),
)


def tip_rows(tooltip):
    """The tooltip's (label, value) rows, as vega-tooltip lays them out."""
    return [
        tuple(cell.strip() for cell in row.locator("td").all_inner_texts())
        for row in tooltip.locator("tr").all()
    ]


def _wheel_into_view(page, chart, *, attempts=5):
    """Wheel the page until the chart's top is near the window's top, and
    holds there.

    A scripted scroll (scroll_into_view_if_needed) can be undone later by
    Firefox, which re-applies the page's last scroll into view, so the box
    the test measured would no longer be under the pointer. A real wheel
    turn is never undone, but it can stop short, so the page is wheeled
    again until it holds across two settled checks. The page scrolls
    nothing itself here, so this changes no behaviour under test.
    """
    where = (
        "Math.min(document.documentElement.scrollHeight - innerHeight,"
        " Math.max(0, document.querySelector('#participation-chart figure.chart')"
        ".getBoundingClientRect().top + scrollY - 40))"
    )
    there = f"Math.abs(({where}) - scrollY) <= 1"
    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 450)
    for _ in range(attempts):
        page.mouse.wheel(0, page.evaluate(f"({where}) - scrollY"))
        painted(page)
        if page.evaluate(there):
            painted(page)
            if page.evaluate(there):
                return
    raise AssertionError(f"the chart did not stay in view after {attempts} turns")


def _point_until_shown(page, plot, tooltip, *, attempts=10):
    """Point at the last date a quarter of the way down ``plot`` until the
    tooltip shows.

    Firefox can miss a single jump of the pointer onto the chart, so the
    pointer moves there in steps, from a fresh measure of the panel's box
    each try (the page could still settle between tries). Only a tooltip
    that never shows fails the test.
    """
    for _ in range(attempts):
        box = plot.bounding_box()
        page.mouse.move(
            box["x"] + box["width"] * 5 / 6, box["y"] + box["height"] / 4, steps=5
        )
        if page.locator("#vg-tooltip-element:visible").count():
            return
        page.wait_for_timeout(200)
        if page.locator("#vg-tooltip-element:visible").count():
            return
    expect(tooltip).to_be_visible()


# The hover guide lines Vega shows (#1536): each shown rule's horizontal
# centre and its top and bottom, relative to its panel's plot box, rounded.
GUIDES = """() => [...document.querySelectorAll(
  "#participation-chart svg g.mark-rule.role-mark line"
)].filter((line) => line.getAttribute("opacity") !== "0").map((line) => {
  const box = line.getBoundingClientRect();
  const plot = line.closest("g.mark-group.role-scope > g")
    .querySelector("path.background").getBoundingClientRect();
  return [
    Math.round(box.left + box.width / 2),
    Math.round(box.top - plot.top),
    Math.round(plot.bottom - box.bottom),
  ];
})"""

# The horizontal centre of the last date's point on the participating
# Families line (the second panel's symbols, in date order).
LAST_POINT = """() => {
  const points = [...document.querySelectorAll(
    "#participation-chart svg g.mark-symbol.role-mark path"
  )].map((point) => point.getBoundingClientRect());
  const last = points[2];
  return Math.round(last.left + last.width / 2);
}"""


def test_a_date_column_shows_that_dates_figures(page, chart):
    """Pointing anywhere in a date's column, in any panel, shows one
    tooltip with that date's figures and one faint guide line at that date
    through every panel (#1536); leaving the chart hides both, and neither
    moves anything."""
    tooltip = page.locator("#vg-tooltip-element")
    _wheel_into_view(page, chart)
    # The wheel's pointer rests over the chart; nothing shows once it leaves.
    page.mouse.move(0, 0)
    eventually(page, f"() => ({GUIDES})().length", 0)
    resting = chart.bounding_box()
    plots = chart.locator("svg g.mark-group.role-scope > g > path.background").all()
    for plot in plots:
        _point_until_shown(page, plot, tooltip)
        expect(tooltip.locator("h2")).to_have_text("November 2, 2026")
        assert tip_rows(tooltip) == list(TIP)
        # One line per panel, at one x (the last date's middle, where its
        # points are), from the plot's top to its bottom.
        eventually(page, f"() => ({GUIDES})().length", 3)
        guides = page.evaluate(GUIDES)
        assert len({x for x, _top, _bottom in guides}) == 1
        assert all(abs(top) <= 1 and abs(bottom) <= 1 for _x, top, bottom in guides)
        assert abs(guides[0][0] - page.evaluate(LAST_POINT)) <= 1
        assert chart.bounding_box() == resting
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()
    eventually(page, f"() => ({GUIDES})().length", 0)
    assert chart.bounding_box() == resting


def test_printing_hides_the_guide_line_and_tooltip(page, chart):
    """Printing while the pointer rests on the chart clears the hover guide
    line (beforeprint) and hides the tooltip (print media) (#1536), so a
    printed page never shows them."""
    tooltip = page.locator("#vg-tooltip-element")
    _wheel_into_view(page, chart)
    _point_until_shown(
        page,
        chart.locator("svg g.mark-group.role-scope > g > path.background").first,
        tooltip,
    )
    eventually(page, f"() => ({GUIDES})().length", 3)
    page.evaluate("() => window.dispatchEvent(new Event('beforeprint'))")
    eventually(page, f"() => ({GUIDES})().length", 0)
    page.emulate_media(media="print")
    expect(tooltip).to_be_hidden()
    page.emulate_media(media="screen")
    page.evaluate("() => window.dispatchEvent(new Event('afterprint'))")


# Sets window.tipOpened once vega-tooltip shows its tooltip element, and
# window.guideShown once a hover guide line shows (#1536).
TIP_OPENED = """() => {
    window.tipOpened = false;
    window.guideShown = false;
    const tip = () => document.querySelector('#vg-tooltip-element');
    const guides = () => document.querySelectorAll(
        '#participation-chart svg g.mark-rule.role-mark line');
    new MutationObserver(() => {
        if (tip()?.classList.contains('visible')) window.tipOpened = true;
        if ([...guides()].some((line) => line.getAttribute('opacity') !== '0')) {
            window.guideShown = true;
        }
    }).observe(document.body, {
        attributes: true, attributeFilter: ['class', 'opacity'],
        childList: true, subtree: true,
    });
}"""


def test_tapping_a_date_shows_its_figures(browser_engine, component_origin):
    """On a touch screen, tapping a date's column shows its tooltip."""
    context = browser_engine.new_context(
        has_touch=True,
        viewport={"width": 390, "height": 900},
        timezone_id="America/Los_Angeles",
    )
    try:
        page = context.new_page()
        chart = drawn(page, component_origin)
        chart.scroll_into_view_if_needed()
        plot = chart.locator("svg g.mark-group.role-scope > g > path.background")
        box = plot.nth(1).bounding_box()
        # Record the tooltip as it opens. Headless Chromium keeps an idle
        # mouse cursor beside the touch screen and, a few milliseconds after
        # the tap, moves its hover back there; that mouseout closes the
        # tooltip before a poll for it can be sure to see it (seen in Linux
        # CI). A phone has no such cursor, so what the tap opened is checked
        # from the moment it opened, and its text from the tooltip itself,
        # which keeps its content when it hides.
        page.evaluate(TIP_OPENED)
        page.touchscreen.tap(box["x"] + box["width"] / 6, box["y"] + box["height"] / 2)
        tooltip = page.locator("#vg-tooltip-element")
        eventually(page, "() => window.tipOpened === true")
        expect(tooltip.locator("h2")).to_have_text("October 31, 2026")
        expect(tooltip.locator("tr").nth(1).locator("td")).to_have_text(
            ["Families that have responded", "1 out of 1,234 (0.1%)"]
        )
        # The tap drew the guide line too: its selection follows the same
        # replayed pointer move (#1536). Like the tooltip, Chromium's idle
        # cursor can clear it right after, so it is checked from the moment
        # the tap opened the tooltip, through the rule Vega drew then.
        eventually(page, "() => window.guideShown === true")
    finally:
        context.close()


@pytest.mark.parametrize("width", [320, 1280])
def test_a_long_campaign_thins_its_dates_and_breaks_at_a_gap(
    page, component_origin, width
):
    """Over 75 dates the date labels thin out rather than overlap, the
    pledges ticks stay short ("$1.5M"), the unavailable date breaks both
    lines, and the panels still line up and fit."""
    errors = watch(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/participation-long")
    chart = page.locator(f"{DAILY} figure.chart")
    expect(page.locator(f"{DAILY} [data-chart-state=rendered]")).to_have_count(1)
    eventually(page, FITTED)
    texts = chart.locator("svg text").all_text_contents()
    assert "$1.5M" in texts or "$1M" in texts
    assert all(not text.endswith("000K") for text in texts)
    # Each line is drawn in two pieces, either side of the gap.
    lines = chart.locator("svg g.mark-line.role-mark path")
    assert lines.count() == 2
    for index in range(2):
        assert lines.nth(index).get_attribute("d").count("M") == 2
    assert all(left == 0 and width == 0 for left, width, _top in page.evaluate(PLOTS))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate(COLLISIONS) == []
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1280])
def test_daily_chart_fits_and_is_accessible(page, component_origin, axe_source, width):
    """At phone and desktop widths the chart fits its panel, with no text cut
    off or overlapping and no sideways scroll, and the page passes axe."""
    page.set_viewport_size({"width": width, "height": 900})
    drawn(page, component_origin)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate(COLLISIONS) == []
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
