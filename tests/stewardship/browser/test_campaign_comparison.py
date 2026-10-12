"""The Campaign comparison page (#1018) in a browser.

The page is served by ``comparison_components``. It must pass the automated
accessibility checks, fit a phone and a laptop without scrolling sideways,
give each headline tile its full wording as its accessible text, and
explain a "—" with a keyboard-reachable toggletip. Its charts (slice 5)
draw under the real CSP with no text drawn over other text and nothing cut
off at either width, and Compare on day changes the day in place without
moving the control under the pointer.
"""

import pytest

from .comparison_components import DAY_5, PAGE
from .test_charts import assert_clean, watch
from .test_components import axe_violations

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

PAGES = [
    "/campaign-comparison",
    "/campaign-comparison-crowded",
    "/campaign-comparison-empty",
]
# The charts each page draws: three day by day, two final results and two
# pledge distributions with Financial stewardship; without it, two and one.
CHARTS = {
    "/campaign-comparison": 7,
    "/campaign-comparison-crowded": 7,
    "/campaign-comparison-empty": 3,
}
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
# Every pair of text boxes in each drawn chart that overlap by more than a
# pixel, and every text box or chart that reaches past its view's edges
# (cut off, or making the panel scroll). Vega draws axis labels, titles,
# legend labels and the charts' own labels all as SVG text.
COLLISIONS = """() => {
  const found = [];
  for (const view of document.querySelectorAll("[data-chart-view]")) {
    const edge = view.getBoundingClientRect();
    const svg = view.querySelector("svg");
    const frame = svg.getBoundingClientRect();
    if (frame.left < edge.left - 1 || frame.right > edge.right + 1) {
      found.push(["chart wider than its view", view.dataset.chartTitle]);
    }
    // Vega hides an axis label that would overlap another (labelOverlap)
    // by making it transparent, so only text that is drawn counts.
    const drawn = (text) => text.textContent.trim()
      && Number(getComputedStyle(text).opacity) > 0
      && text.getAttribute("opacity") !== "0"
      && text.getAttribute("fill-opacity") !== "0";
    const boxes = [...svg.querySelectorAll("text")]
      .filter(drawn)
      .map((text) => [text.textContent, text.getBoundingClientRect()]);
    for (const [name, box] of boxes) {
      if (box.left < frame.left - 1 || box.right > frame.right + 1
          || box.top < frame.top - 1 || box.bottom > frame.bottom + 1) {
        found.push(["cut off", view.dataset.chartTitle, name]);
      }
    }
    boxes.forEach(([a, one], index) => {
      boxes.slice(index + 1).forEach(([b, two]) => {
        const wide = Math.min(one.right, two.right) - Math.max(one.left, two.left);
        const tall = Math.min(one.bottom, two.bottom) - Math.max(one.top, two.top);
        if (wide > 1 && tall > 1) found.push([view.dataset.chartTitle, a, b]);
      });
    });
  }
  return found;
}"""


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("width", [320, 1280])
def test_page_is_accessible_and_fits(page, component_origin, axe_source, path, width):
    """No axe violation and no sideways page scroll; tables scroll in place."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = watch(page)
    page.goto(component_origin + path)
    rendered = page.locator("[data-chart][data-chart-state=rendered]")
    expect(rendered).to_have_count(CHARTS[path])
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # The published infographic's lessons: at phone width no label is drawn
    # over another, and no chart or label runs past its panel.
    assert page.evaluate(COLLISIONS) == []
    assert axe_violations(page, axe_source) == []
    assert_clean(page, errors)


def test_dark_mode_keeps_the_light_theme_and_its_contrast(
    page, component_origin, axe_source
):
    """A browser set to dark mode still gets the portal's one light theme.

    The theme declares only a light color scheme, and the charts draw with
    its tokens on a white background, so their text keeps its contrast.
    """
    page.emulate_media(color_scheme="dark")
    page.goto(component_origin + "/campaign-comparison")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(7)
    assert page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
    assert page.evaluate("getComputedStyle(document.body).colorScheme") in (
        "light",
        "normal",
    )
    panel = page.locator("#comparison-families-heading").locator("xpath=..")
    assert panel.evaluate("e => getComputedStyle(e).backgroundColor") == (
        "rgb(255, 255, 255)"
    )
    # The axis labels use the theme's muted ink, not Vega's light grey.
    label = page.locator("[data-chart-view] svg g.role-axis-label text").first
    assert label.evaluate("e => e.getAttribute('fill')") == "#4f5e66"
    assert axe_violations(page, axe_source) == []


def test_charts_show_each_year_and_their_exact_values(page, component_origin):
    """The lines, end labels, day rule, tooltips and tables of the charts."""
    errors = watch(page)
    page.goto(component_origin + "/campaign-comparison")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(7)
    families = page.locator("[data-chart='comparison-families-spec']")
    view = families.locator("[data-chart-view]")
    assert view.get_attribute("aria-label") == "Families responded by campaign day"
    summary = families.locator("#" + view.get_attribute("aria-describedby"))
    assert summary.inner_text() == (
        "Families responded by campaign day, day 1 being each campaign's first "
        "invitation day. On day 12: 2027 (so far) 270; 2026 (last year) 240; "
        "2025–2026 average 230."
    )
    texts = families.locator("svg text").all_text_contents()
    # The grey years are labelled at their lines' ends; the day is marked.
    assert "2025" in texts and "2024" in texts and "Day 12" in texts
    for name in ("2027 (so far)", "2026 (last year)", "Prior-years average"):
        assert name in texts
    # Pointing anywhere over the plot, not just at a line, picks that day:
    # one tooltip lists every line's value (and date) on it. Here the
    # pointer is on the day 12 rule, halfway up the plot (the mouse moves
    # in the viewport, so the chart is scrolled into it first).
    view.scroll_into_view_if_needed()
    rule = families.locator("svg g.mark-rule.role-mark line").first.bounding_box()
    page.mouse.move(rule["x"] + 1, rule["y"] + rule["height"] / 2)
    tooltip = page.locator("#vg-tooltip-element")
    expect(tooltip).to_be_visible()
    expect(tooltip.locator("h2")).to_have_text("Day 12")
    for text in (
        "2027 (so far)",
        "270 · ",
        "2026 (last year)",
        "240 · ",
        "Prior-years average",
        "230",
    ):
        expect(tooltip).to_contain_text(text)
    # Leaving the chart hides it again.
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()
    # The exact values: one row per day, current campaign first.
    table = page.locator("[data-chart='comparison-families-spec'] + .chart-table")
    table.locator("summary").click()
    headings = table.locator("thead th").all_text_contents()
    assert headings == [
        "Day",
        "2027 (so far)",
        "2026 (last year)",
        "Prior-years average",
        "2024 (partial)",
        "2025",
    ]
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(40)
    # Day 40: 2026 and 2025 ended there; 2024 stopped on day 9.
    assert rows.nth(39).locator("td").all_text_contents() == [
        "40",
        "—",
        "900",
        "890",
        "—",
        "880",
    ]
    # The final results mark this campaign's bar as so far.
    finals = page.locator("[data-chart='comparison-final-families-spec']")
    assert "2027 (so far)" in finals.locator("svg text").all_text_contents()
    # Pointing beside a short bar, on its year's row, still reads its value.
    finals.scroll_into_view_if_needed()
    row = finals.locator("svg g.mark-rect.role-mark").first.locator("path").last
    box = row.bounding_box()
    page.mouse.move(box["x"] + box["width"] - 4, box["y"] + box["height"] / 2)
    expect(tooltip).to_be_visible()
    expect(tooltip).to_contain_text("2027 (so far)")
    page.mouse.move(0, 0)
    bands = page.locator("[data-chart='comparison-bands-dollars-spec'] + .chart-table")
    bands.locator("summary").click()
    expect(bands.locator("tbody tr")).to_have_count(4)
    assert_clean(page, errors)


def test_compare_on_day_changes_the_day_in_place(page, component_origin):
    """Compare on day swaps the report without a reload or a layout shift.

    The control stays where it was under the pointer, the reader keeps
    their place, focus stays on Compare, the address names the day, and
    the tiles, charts and tables show day 5.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    errors = watch(page)
    page.goto(component_origin + PAGE)
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(7)
    page.evaluate(MARK)
    field = page.get_by_label("Compare on day")
    button = page.get_by_role("button", name="Compare", exact=True)
    assert field.get_attribute("min") == "1" and field.get_attribute("max") == "12"
    page.mouse.wheel(0, 120)
    page.wait_for_function("() => window.scrollY > 0")
    scrolled = page.evaluate("window.scrollY")
    before = button.bounding_box()
    field.fill("5")
    with page.expect_request(
        lambda request: request.url.endswith(DAY_5) and request.method == "GET"
    ):
        button.click()
    page.wait_for_url(component_origin + DAY_5 + "#comparison-report")
    report = page.locator("#comparison-report")
    expect(report.locator("p").first).to_contain_text("Day 5 of Stewardship 2027")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(7)
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("window.scrollY") == scrolled
    assert button.bounding_box() == before
    assert page.evaluate("document.activeElement.textContent.trim()") == "Compare"
    assert field.input_value() == "5"
    families = page.locator("[data-chart='comparison-families-spec']")
    assert "Day 5" in families.locator("svg text").all_text_contents()
    expect(families.locator("figcaption")).to_contain_text("On day 5:")
    assert_clean(page, errors)


def test_compare_on_day_refuses_a_day_past_the_last_complete_one(
    page, component_origin
):
    """The browser holds a day out of range at the field; nothing is sent."""
    page.goto(component_origin + "/campaign-comparison")
    field = page.get_by_label("Compare on day")
    sent = []
    page.on("request", lambda request: sent.append(request.url))
    for value in ("13", "0", "2.5"):
        field.fill(value)
        page.get_by_role("button", name="Compare", exact=True).click()
        assert field.evaluate("e => e.validity.valid") is False
    field.blur()
    assert field.get_attribute("aria-invalid") == "true"
    assert not [url for url in sent if "day=" in url]


def test_tiles_carry_their_full_wording(page, component_origin):
    """Each tile's accessible text is the whole comparison, arrows and words."""
    page.goto(component_origin + "/campaign-comparison")
    tiles = page.locator(".stat-tile")
    expect(tiles).to_have_count(5)
    families = tiles.first
    expect(families).to_have_attribute(
        "title",
        "Families responded: 270 on day 12. Last year on day 12: 240; this year "
        "is 12.5% more. 2025–2026 average: 230; this year is 17.4% more.",
    )
    expect(families.locator(".stat-detail").first).to_have_text(
        "Last year on day 12: 240 (↑ 12.5% more)"
    )
    # Money keeps its cents; the median sits beside the average.
    expect(tiles.nth(2).locator(".stat-value")).to_have_text("$345,000.50")
    expect(tiles.nth(3)).to_contain_text("Median so far: $1,000.00")


def test_tables_and_notes(page, component_origin):
    """Every number, the pledge bands and escaped data notes are shown."""
    page.goto(component_origin + "/campaign-comparison")
    rows = page.locator("#numbers-caption").locator("xpath=..").locator("tbody tr")
    expect(rows).to_have_count(4)
    expect(rows.first.locator("th")).to_have_text("Stewardship 2027 (so far)")
    # The partial 2024 is unknown on day 12.
    expect(rows.nth(3).locator("td").nth(2)).to_have_text("—")
    expect(
        page.locator("caption", has_text="Stewardship 2027 (so far)")
    ).to_contain_text("median $1,000.00")
    expect(
        page.get_by_text("Converted from <three> invented exports").first
    ).to_be_visible()
    # An Administrator manages the imported years on Past campaigns.
    expect(
        page.get_by_role(
            "link", name="Add, replace or remove imported years on Past campaigns"
        )
    ).to_have_attribute("href", "/admin/parish/past-campaigns/")


def test_an_empty_comparison_says_what_is_missing(page, component_origin):
    """With no imported year the page says so, and shows no pledge tiles."""
    page.goto(component_origin + "/campaign-comparison-empty")
    expect(page.get_by_text("No past campaigns are imported")).to_be_visible()
    expect(
        page.get_by_role("link", name="Import them on Past campaigns")
    ).to_have_attribute("href", "/admin/parish/past-campaigns/")
    expect(page.locator(".stat-tile")).to_have_count(3)
    # The renewal "—" explains itself from the keyboard.
    button = page.locator(".stat-tile .toggletip-button")
    expect(button).to_have_count(1)
    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator(".toggletip-bubble").first).to_have_text("No past year.")
