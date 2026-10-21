"""Pinned chart values are responsive, accessible and usable without JavaScript."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def assert_tip_layout(page, tip, image):
    """The tooltip is readable, aligned on the colon and inside the chart.

    Labels end at one right edge (the colon) and values start at one left
    edge; text is at least 14px; the box never leaves the image, so a phone's
    page does not scroll sideways.
    """
    layout = tip.evaluate("""tip => {
      const box = el => el.getBoundingClientRect();
      const terms = [...tip.querySelectorAll("dt")].map(box);
      const details = [...tip.querySelectorAll("dd")].map(box);
      return {
        rights: terms.map(r => Math.round(r.right)),
        lefts: details.map(r => Math.round(r.left)),
        size: parseFloat(getComputedStyle(tip.querySelector("dd")).fontSize),
      };
    }""")
    assert len(set(layout["rights"])) == 1
    assert len(set(layout["lefts"])) == 1
    assert layout["size"] >= 14
    outer, inner = image.bounding_box(), tip.bounding_box()
    assert inner["x"] >= outer["x"] - 1
    assert inner["x"] + inner["width"] <= outer["x"] + outer["width"] + 1


def guide_share(guide, image):
    """Where the hover guide line (#1536) crosses the image, as a share of
    its width, after checking it runs down the image's plot and is hidden
    from assistive technology."""
    assert guide.is_visible()
    assert guide.get_attribute("aria-hidden") == "true"
    outer, line = image.bounding_box(), guide.bounding_box()
    assert (
        outer["y"]
        < line["y"]
        < line["y"] + line["height"]
        < outer["y"] + outer["height"]
    )
    assert line["height"] > outer["height"] / 2
    return (line["x"] - outer["x"]) / outer["width"]


@pytest.mark.parametrize("width", [320, 1280])
def test_chart_pointer_and_keyboard_preserve_exact_values(
    page, component_origin, axe_source, width
):
    """Input changes inspect retained data only and work under the actual CSP."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + "/daily-digest")
    image = page.locator(".digest-chart")
    assert image.evaluate("image => image.complete && image.naturalWidth === 1440")
    slider = page.get_by_role("slider", name="Inspect campaign date")
    slider.focus()
    page.keyboard.press("Home")
    values = page.locator("[data-digest-values]")
    # Campaign dates use the compact parish style (default US).
    assert "Oct 31, 2026" in values.inner_text()
    assert "1 out of 1,234 (0.1%)" in values.inner_text()
    assert "$1,234.56" in values.inner_text()
    assert slider.get_attribute("aria-valuetext") == values.inner_text()
    image.scroll_into_view_if_needed()
    bounds = image.bounding_box()
    image.hover(position={"x": bounds["width"] * 0.80, "y": bounds["height"] * 0.45})
    assert "Nov 2, 2026" in values.inner_text()
    # The native title tooltip (a dense sentence) is gone; the styled one
    # shows the date and short "label: value" lines (#575).
    assert image.get_attribute("title") is None
    tip = page.locator("[data-digest-tip]")
    assert tip.is_visible()
    assert tip.get_attribute("aria-hidden") == "true"
    assert tip.locator(".digest-tip-date").inner_text() == "Nov 2, 2026"
    assert tip.locator("dt").all_inner_texts() == [
        "Families:",
        "New today:",
        "Pledges:",
    ]
    assert tip.locator("dd").all_inner_texts() == ["3", "1", "$3,235"]
    assert_tip_layout(page, tip, image)
    # A faint line marks the inspected date across the plot: Nov 2, the last
    # of the three dates, sits in the right part of the image.
    guide = page.locator("[data-digest-guide]")
    assert guide_share(guide, image) > 0.6
    page.mouse.move(0, 0)
    assert tip.is_hidden()
    assert guide.is_hidden()
    # Keyboard users see the same tooltip, and Escape dismisses it. Opening
    # or closing it never moves the slider (on a phone it sits under the
    # chart, in space reserved for it).
    offset = "node => node.getBoundingClientRect().top + window.scrollY"
    resting = slider.evaluate(offset)
    slider.focus()
    page.keyboard.press("Home")
    assert tip.is_visible()
    assert tip.locator(".digest-tip-date").inner_text() == "Oct 31, 2026"
    assert_tip_layout(page, tip, image)
    # The slider moves the guide line too: Oct 31 is the first date.
    assert guide_share(guide, image) < 0.4
    assert slider.evaluate(offset) == resting
    page.keyboard.press("Escape")
    assert tip.is_hidden()
    assert guide.is_hidden()
    assert slider.evaluate(offset) == resting
    assert not errors
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
    assert violations == []


def test_chart_hit_testing_follows_the_email_drawing(page, component_origin):
    """The email drawing's plot fills the image, so its upper part takes a pointer.

    The email drawing's panels (#720) begin at 8% of the image height; the
    older Participation drawing's plot starts lower, at 26%, so a hover at
    15% only shows a tooltip when the page uses the retained drawing's
    layout.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/daily-digest")
    image = page.locator(".digest-chart")
    image.scroll_into_view_if_needed()
    bounds = image.bounding_box()
    image.hover(position={"x": bounds["width"] * 0.80, "y": bounds["height"] * 0.15})
    tip = page.locator("[data-digest-tip]")
    assert tip.is_visible()
    assert tip.locator(".digest-tip-date").inner_text() == "Nov 2, 2026"


@pytest.mark.parametrize("touch", [False, True])
def test_chart_press_after_slider_keeps_the_tooltip_open(
    browser_engine, component_origin, touch
):
    """Pressing the chart after using the slider opens, not closes, the tooltip.

    The press moves focus off the slider; that blur must not hide the tooltip
    the press just opened (a mouse blurs right after pointerdown, a touch tap
    only after the finger lifts). Tabbing away from the slider still hides it.
    """
    context = browser_engine.new_context(
        viewport={"width": 1280, "height": 900}, has_touch=touch
    )
    try:
        page = context.new_page()
        page.goto(component_origin + "/daily-digest")
        slider = page.get_by_role("slider", name="Inspect campaign date")
        tip = page.locator("[data-digest-tip]")
        slider.focus()
        page.keyboard.press("Home")
        assert tip.is_visible()
        image = page.locator(".digest-chart")
        image.scroll_into_view_if_needed()
        box = image.bounding_box()
        x, y = box["x"] + box["width"] * 0.80, box["y"] + box["height"] * 0.45
        if touch:
            page.touchscreen.tap(x, y)
        else:
            page.mouse.click(x, y)
        page.wait_for_timeout(200)
        assert tip.is_visible()
        assert tip.locator(".digest-tip-date").inner_text() == "Nov 2, 2026"
        if not touch:
            # A later blur that is not caused by a chart press still hides it.
            page.mouse.move(0, 0)
            slider.focus()
            assert tip.is_visible()
            page.wait_for_timeout(1100)
            page.keyboard.press("Tab")
            assert tip.is_hidden()
    finally:
        context.close()


def test_snapshot_keeps_every_value_and_the_original_chart(page, component_origin):
    """The complete table and the original chart's download link stay on the page."""
    page.goto(component_origin + "/daily-digest")
    assert page.locator("tbody tr").count() == 3
    assert "$3,234.56" in page.locator("table").inner_text()
    visible(page.get_by_role("link", name="Download the original chart (PNG)"))


def test_missing_controls_keeps_static_report_usable(page, component_origin):
    """A partial template must not turn optional chart enhancement into an error."""
    from playwright.sync_api import expect

    def omit_controls(route):
        """Keep the real response and CSP, omitting only the enhancement marker."""
        response = route.fetch()
        route.fulfill(
            response=response,
            body=response.text().replace(
                "data-digest-controls", "data-omitted-controls"
            ),
        )

    page.route("**/daily-digest", omit_controls)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(f"{message.text} ({message.location.get('url', '')})")
            if message.type == "error"
            else None
        ),
    )
    with (
        page.expect_response("**/digest-v1.js") as script,
        page.expect_response("**/admin/presence?format=count") as presence,
    ):
        page.goto(component_origin + "/daily-digest")
    assert script.value.status == 200
    assert presence.value.status == 200
    expect(page.locator("[data-presence-count]")).to_have_text("0")
    assert not errors
    assert page.locator("[data-omitted-controls]").count() == 1
    assert page.locator("[data-omitted-controls]").is_hidden()
    assert page.locator("input[type=range]").get_attribute("aria-valuetext") is None
    assert page.locator("tbody tr").count() == 3


@pytest.mark.parametrize("width", [320, 1280])
def test_the_saved_report_shows_the_response_funnel(
    page, component_origin, axe_source, width
):
    """The funnel table and figures are readable, accessible and fit (#477).

    The panel draws no charts (#1370), only the funnel's own table.
    """
    from playwright.sync_api import expect

    from .test_charts import assert_clean, watch
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    page.goto(component_origin + "/daily-digest-funnel")
    funnel = page.locator("[data-digest-funnel]")
    visible(funnel.get_by_role("heading", name="Response funnel", level=2))
    expect(funnel.locator("[data-chart]")).to_have_count(0)
    expect(funnel.get_by_role("heading", level=3)).to_have_count(0)
    # Four stages: "Link followed" is left out (#1016).
    table = funnel.locator('table[aria-labelledby="digest-funnel-heading"]')
    assert table.locator("tbody tr").count() == 4
    text = funnel.inner_text()
    assert "Email invites sent" in text and "40%" in text
    assert "Compared with email invites sent" in text
    assert "Link followed" not in text and "mail-scanner" not in text
    # No side figures (#1532).
    assert "more than once" not in text and "of Submitted" not in text
    expect(funnel.locator("dl")).to_have_count(0)
    assert "already responded" not in text and "Families at each stage" not in text
    assert "without a delivered invitation" not in text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1280])
def test_the_saved_comparison_section_draws_its_charts(
    page, component_origin, axe_source, width
):
    """The retained section's charts draw from their stored specifications.

    #1018 slice 7: under the real CSP, with no axe violation and no sideways
    scroll, each chart has its exact values, and the headline figures read
    as the email's lines, with Withheld where the small-count protection
    applies.
    """
    from playwright.sync_api import expect

    from .test_charts import assert_clean, watch
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    page.goto(component_origin + "/daily-digest-comparison")
    section = page.get_by_role("region", name="Compared with past campaigns")
    expect(section).to_be_visible()
    rendered = section.locator("[data-chart][data-chart-state=rendered]")
    expect(rendered).to_have_count(2)
    assert section.get_by_role("heading", level=3).all_inner_texts() == [
        "Families responded by campaign day",
        "Annual pledges by campaign day",
    ]
    figures = section.get_by_role("list", name="Headline figures")
    assert "Last year on day 3: 30 (↑ 20% more)" in figures.inner_text()
    assert "Last year on day 3: Withheld" in figures.inner_text()
    section.get_by_text("Exact values").first.click()
    table = section.get_by_role("table").first
    expect(table).to_be_visible()
    assert table.locator("th").first.inner_text() == "Day"
    link = section.get_by_role("link", name="Open the Campaign comparison report")
    assert link.get_attribute("href") == "/admin/reports/comparison/?day=3"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert_clean(page, errors)


# Each Campaign totals tile's value, how far its text runs past the tile's
# border box (0 when inside), how many lines it takes and its height in
# rem. The text is measured on a range: a tile is a flex column, so the
# value's element is stretched to the tile's width (test_response_dashboard).
# The range holds only the value's own text ("—" for an unknown value), not
# its toggletip, whose hidden tip text would count as more lines.
TOTALS_FIT = """() => [...document.querySelectorAll(
    '[data-digest-totals] .stat-tile[data-stat]')].map((tile) => {
  const value = tile.querySelector('.stat-value');
  const outer = tile.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(value.firstChild);
  const inner = range.getBoundingClientRect();
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return {
    key: tile.dataset.stat,
    overflow: Math.max(0, outer.left - inner.left, inner.right - outer.right),
    lines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size,
    height: Math.round(value.getBoundingClientRect().height / rem * 100) / 100,
  };
})"""


@pytest.mark.parametrize("width", [320, 375, 768, 1280])
def test_the_saved_report_draws_its_totals_as_tiles_that_fit(
    page, component_origin, axe_source, width
):
    """The Campaign totals are stat tiles that fit at every width (#1639).

    With four-digit counts and a seven-figure pledge total each value stays
    on one line inside its tile, the page never scrolls sideways, and the
    tiles read as the email's figures: the Families count with its "out of"
    line and share bar.
    """
    from playwright.sync_api import expect

    from .test_charts import assert_clean, watch
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    page.goto(component_origin + "/daily-digest-wide")
    totals = page.get_by_role("list", name="Campaign totals")
    tiles = totals.locator(".stat-tile")
    expect(tiles).to_have_count(3)
    expect(tiles.locator(".stat-value")).to_have_text(
        ["1,234", "1,088", "$1,234,567.89"]
    )
    responded = totals.locator("[data-stat=responded]")
    expect(responded.locator(".stat-detail")).to_have_text("out of 2,345 (52.6%)")
    expect(responded.locator("progress")).to_have_attribute("max", "2345")
    expect(totals.locator("progress")).to_have_count(1)
    assert page.locator("[data-digest-totals] dl").count() == 0
    drawn = page.evaluate(TOTALS_FIT)
    assert [tile["key"] for tile in drawn] == ["responded", "first", "pledges"]
    assert all(
        (tile["overflow"], tile["lines"], tile["height"]) == (0, 1, 2.1)
        for tile in drawn
    ), drawn
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1280])
def test_an_unknown_saved_total_reads_a_dash_with_its_reason(
    page, component_origin, axe_source, width
):
    """A pledge total the email read "Unavailable" is "—" on the page, with
    a toggletip saying why, and still fits (#1639)."""
    from playwright.sync_api import expect

    from .test_charts import assert_clean, watch
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    page.goto(component_origin + "/daily-digest-unknown")
    pledges = page.locator("[data-digest-totals] [data-stat=pledges]")
    expect(pledges.locator(".stat-value")).to_contain_text("—")
    assert "Unavailable" not in page.locator("[data-digest-totals]").inner_text()
    button = pledges.get_by_role("button")
    expect(button).to_have_accessible_description(
        "The total is added up only when every responding Family's pledge "
        "amount is known."
    )
    drawn = page.evaluate(TOTALS_FIT)
    assert all(
        (tile["overflow"], tile["lines"], tile["height"]) == (0, 1, 2.1)
        for tile in drawn
    ), drawn
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert_clean(page, errors)
