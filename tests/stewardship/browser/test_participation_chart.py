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
from .waits import eventually

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
    """The daily chart, drawn at a desktop width, with errors watched."""
    page.errors = watch(page)
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
    # One value axis per panel, all on the left, and one date axis.
    assert chart.locator("svg g.role-axis[aria-label^='Y-axis']").count() == 3
    assert chart.locator("svg g.role-axis[aria-label^='X-axis']").count() == 1
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
    assert_clean(page, page.errors)


def test_a_date_column_shows_that_dates_figures(page, chart):
    """Pointing anywhere in a date's column, in any panel, shows one
    tooltip with that date's figures; leaving the chart hides it."""
    chart.scroll_into_view_if_needed()
    tooltip = page.locator("#vg-tooltip-element")
    plots = chart.locator("svg g.mark-group.role-scope > g > path.background").all()
    for plot in plots:
        box = plot.bounding_box()
        # The last of the three dates, a quarter of the way down the panel.
        page.mouse.move(box["x"] + box["width"] * 5 / 6, box["y"] + box["height"] / 4)
        expect(tooltip).to_be_visible()
        expect(tooltip.locator("h2")).to_have_text("November 2, 2026")
        for text in (
            "First submissions",
            "Families that have responded",
            "Effective annual pledges",
        ):
            expect(tooltip).to_contain_text(text)
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()


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
