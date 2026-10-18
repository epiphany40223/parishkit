"""Charts follow the Admin colour scheme (#1264 slice 2).

Every chart page draws its charts' background, axes, gridlines, labels,
legend, marks and tooltip in the tokens of the page's scheme: the light
values of ``COLORS`` and ``CHART_COLORS`` in light, and their dark values
in dark (ui-conventions "Colour scheme"). A change of scheme on an open
page redraws each chart in place, with no reload, no page error and no
layout shift, and a page printed from dark prints its charts light. The
token values and their contrast are unit-tested in
``tests/stewardship/test_design_tokens.py``.
"""

import pytest

from parishkit.stewardship.web.design_tokens import (
    CHART_COLORS,
    COLORS,
    DARK_CHART_COLORS,
    DARK_COLORS,
    hex6,
)

from .test_charts import assert_clean, watch
from .test_dark_scheme import choose, rgb
from .waits import eventually

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Every page with a chart: the response charts, the Response dashboard (its
# daily chart, #1537), Financial stewardship
# summary, Campaign comparison, Additional information, Ministry requests
# and a saved daily digest.
PAGES = (
    "/charts",
    "/participation",
    "/financial-report",
    "/campaign-comparison",
    "/information",
    "/followup-queue",
    "/daily-digest-comparison",
)
SCHEMES = {
    "light": COLORS | CHART_COLORS,
    "dark": DARK_COLORS | DARK_CHART_COLORS,
}
# Every chart that can be drawn is drawn: none is waiting outside a closed
# disclosure, and none is still drawing.
SETTLED = """() => [...document.querySelectorAll('[data-chart]')].every((chart) =>
    chart.dataset.chartState === 'rendered'
    || chart.dataset.chartState === 'failed'
    || (chart.dataset.chartState === 'pending'
        && chart.closest('details:not([open])')))"""
# Each drawn chart's background, and every fill and stroke Vega gave a
# visible element of it. Vega's pointer targets are transparent (opacity 0)
# and keep its default colour, which nobody sees.
PAINTS = """() => {
  const backgrounds = [], paints = new Set();
  for (const svg of document.querySelectorAll('[data-chart-view] svg')) {
    backgrounds.push(getComputedStyle(svg).backgroundColor);
    for (const el of svg.querySelectorAll('*')) {
      if (el.closest('[opacity="0"]')) continue;
      for (const prop of ['fill', 'stroke']) {
        const value = el.getAttribute(prop);
        if (value && value !== 'none' && value !== 'transparent') paints.add(value);
      }
    }
  }
  return {backgrounds, paints: [...paints]};
}"""
AXIS_LABELS = """() => [...new Set([...document.querySelectorAll(
    '[data-chart-view] svg g.role-axis-label text')]
    .map((el) => el.getAttribute('fill')))]"""


def settle(page):
    """Wait until every chart that can be drawn has been drawn."""
    eventually(page, SETTLED)


def assert_in_scheme(page, scheme):
    """Every drawn chart is in ``scheme``'s tokens, and only those."""
    colors = SCHEMES[scheme]
    tokens = {hex6(value) for value in colors.values()}
    found = page.evaluate(PAINTS)
    assert found["backgrounds"]
    assert set(found["backgrounds"]) == {rgb(colors["paper"])}
    assert found["paints"]
    assert {hex6(paint) for paint in found["paints"]} <= tokens, found["paints"]
    assert page.evaluate(AXIS_LABELS) == [colors["muted"]]


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
@pytest.mark.parametrize("path", PAGES)
def test_charts_draw_in_the_page_schemes_tokens(page, component_origin, path, scheme):
    """Each chart's background, text and marks are the scheme's tokens."""
    errors = watch(page)
    page.emulate_media(color_scheme=scheme)
    page.goto(component_origin + path)
    settle(page)
    assert page.evaluate("document.documentElement.dataset.theme") == scheme
    assert_in_scheme(page, scheme)
    assert_clean(page, errors)


@pytest.mark.parametrize("scheme", sorted(SCHEMES))
def test_chart_tooltip_reads_the_tokens(page, component_origin, scheme):
    """The tooltip's text, key, panel and edge are the scheme's tokens."""
    colors = SCHEMES[scheme]
    page.emulate_media(color_scheme=scheme)
    page.goto(component_origin + "/charts")
    settle(page)
    bar = page.locator("[data-chart='response-funnel-spec'] svg g.mark-rect path").first
    bar.hover()
    tooltip = page.locator("#vg-tooltip-element.visible")
    tooltip.wait_for()
    style = "el => [getComputedStyle(el).color, getComputedStyle(el).backgroundColor]"
    assert tooltip.evaluate(style) == [rgb(colors["ink"]), rgb(colors["paper"])]
    assert tooltip.evaluate("el => getComputedStyle(el).borderTopColor") == rgb(
        colors["border"]
    )
    key = tooltip.locator("td.key").first
    assert key.evaluate("el => getComputedStyle(el).color") == rgb(colors["muted"])


# Records every height each chart view takes from now on, so a view that
# collapses for a moment while it is redrawn is caught.
WATCH_HEIGHTS = """() => {
  const views = [...document.querySelectorAll('[data-chart-view]')];
  window.__viewHeights = [];
  const observer = new ResizeObserver((entries) => entries.forEach((entry) =>
    window.__viewHeights.push([views.indexOf(entry.target),
                               Math.round(entry.target.offsetHeight)])));
  views.forEach((view) => observer.observe(view));
}"""
VIEW_HEIGHTS = """() => [...document.querySelectorAll('[data-chart-view]')]
    .map((view) => Math.round(view.offsetHeight))"""


@pytest.mark.parametrize("path", ["/charts", "/campaign-comparison"])
def test_a_scheme_change_redraws_the_charts_in_place(page, component_origin, path):
    """Choosing Dark, then Light, redraws every chart in that scheme with no
    reload, no page error and no view changing height on the way."""
    errors = watch(page)
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + path)
    settle(page)
    assert_in_scheme(page, "light")
    heights = page.evaluate(VIEW_HEIGHTS)
    for scheme in ("dark", "light"):
        page.evaluate(WATCH_HEIGHTS)
        # choose() checks that the page did not reload and nothing moved.
        choose(page, scheme)
        paper = rgb(SCHEMES[scheme]["paper"])
        eventually(page, BACKGROUNDS_ARE, arg=paper)
        settle(page)
        assert_in_scheme(page, scheme)
        assert page.evaluate(VIEW_HEIGHTS) == heights
        moved = [
            (index, height)
            for index, height in page.evaluate("window.__viewHeights")
            if height != heights[index]
        ]
        assert moved == []
    assert page.evaluate("window.__samePage") is True
    assert_clean(page, errors)


BACKGROUNDS_ARE = """(paper) => [...document.querySelectorAll('[data-chart-view] svg')]
    .every((svg) => getComputedStyle(svg).backgroundColor === paper)"""
# Each drawn chart whose width differs from its panel's by more than a
# pixel: a chart left at the printed page's width is drawn too short.
MISFITS = """() => [...document.querySelectorAll('[data-chart-view]')]
    .filter((view) => view.querySelector('svg'))
    .filter((view) => Math.abs(
        Number(view.querySelector('svg').getAttribute('width')) - view.clientWidth) > 1)
    .map((view) => view.dataset.chartTitle)"""


def print_and_return(page):
    """Emulate the print medium, check the charts are light, then go back
    to the screen."""
    page.emulate_media(media="print")
    eventually(page, BACKGROUNDS_ARE, arg=rgb(COLORS["paper"]))
    assert_in_scheme(page, "light")
    # Chromium sends a resize for the print layout a moment later, and Vega
    # draws each chart again at the printed width; it must stay light.
    page.wait_for_timeout(500)
    assert_in_scheme(page, "light")
    page.emulate_media(media="screen")


@pytest.mark.parametrize("path", PAGES)
def test_a_chart_drawn_dark_prints_light(page, component_origin, path):
    """A printed page is light, charts too (ui-conventions "Colour scheme").

    The charts are drawn dark first. The print medium turns every one light
    at once, without waiting for Vega to draw again, and the screen gets its
    dark charts back afterwards, each refitted to its panel; so do the
    beforeprint and afterprint events a real print sends.
    """
    errors = watch(page)
    page.emulate_media(color_scheme="dark")
    page.goto(component_origin + path)
    settle(page)
    assert_in_scheme(page, "dark")
    print_and_return(page)
    eventually(page, BACKGROUNDS_ARE, arg=rgb(DARK_COLORS["paper"]))
    eventually(page, f"({MISFITS})().length === 0")
    assert_in_scheme(page, "dark")
    # The events alone swap synchronously: the check runs in the same task.
    assert page.evaluate(
        """(paper) => { window.dispatchEvent(new Event('beforeprint'));
            const svgs = [...document.querySelectorAll('[data-chart-view] svg')];
            return svgs.every((svg) =>
                getComputedStyle(svg).backgroundColor === paper); }""",
        rgb(COLORS["paper"]),
    )
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    assert_in_scheme(page, "dark")
    eventually(page, f"({MISFITS})().length === 0")
    assert_clean(page, errors)


def test_a_scheme_chosen_while_printing_is_drawn_after(page, component_origin):
    """A scheme change while the page prints waits for the print to end,
    then draws the charts in the new scheme."""
    errors = watch(page)
    page.emulate_media(color_scheme="dark")
    page.goto(component_origin + "/charts")
    settle(page)
    page.emulate_media(media="print")
    eventually(page, BACKGROUNDS_ARE, arg=rgb(COLORS["paper"]))
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    page.emulate_media(media="screen")
    eventually(page, SETTLED)
    assert_in_scheme(page, "light")
    eventually(page, f"({MISFITS})().length === 0")
    assert_clean(page, errors)
