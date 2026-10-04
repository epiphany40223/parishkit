"""The chart engine draws under the real CSP, accessibly, with the table as record."""

import contextlib

import pytest

from .chart_components import HOSTILE_NAME
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Collects every Content Security Policy violation the document reports.
# Playwright injects this before any page script, outside the page's CSP.
CSP_OBSERVER = """
document.addEventListener("securitypolicyviolation", event => {
  (window.__cspViolations = window.__cspViolations || []).push(
    event.violatedDirective + " " + event.blockedURI + " " +
    event.sourceFile + ":" + event.lineNumber);
});
"""


def watch(page):
    """Observe CSP violations and collect page errors and console errors."""
    page.add_init_script(CSP_OBSERVER)
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
    return errors


def assert_clean(page, errors):
    """No policy violation and no script or console error so far."""
    assert page.evaluate("window.__cspViolations || []") == []
    assert errors == []


def test_charts_render_under_the_csp_without_a_single_violation(
    page, component_origin, axe_source
):
    """Both charts draw as SVG in interpreter mode; nothing the CSP forbids runs.

    The page is checked after each interaction that makes Vega do more work
    (a tooltip, the table, a resize that re-lays the chart out), and at the
    end a blocked inline script proves the observer reports violations.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/charts")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    assert page.locator("[data-chart-view] svg").count() == 2
    assert_clean(page, errors)
    # The funnel's bars and labels come from the embedded spec.
    funnel = page.locator("[data-chart='response-funnel-spec']")
    assert funnel.locator("svg g.mark-rect path").count() == 5
    assert "Link followed (includes mail-scanner prefetches)" in funnel.inner_text()
    # The view is the figure's image: named, described by the summary, focusable.
    view = funnel.locator("[data-chart-view]")
    assert view.get_attribute("role") == "graphics-document"
    assert view.get_attribute("aria-label") == "Response funnel"
    summary = funnel.locator("#" + view.get_attribute("aria-describedby"))
    assert summary.inner_text().startswith("Families by stage: 4 invited")
    view.focus()
    assert page.evaluate("document.activeElement.hasAttribute('data-chart-view')")
    # Pointing at a bar shows the vega-tooltip handler's tooltip, styled by
    # chart-v1.css rather than an injected stylesheet.
    funnel.locator("svg g.mark-rect path").first.hover()
    tooltip = page.locator("#vg-tooltip-element")
    visible(tooltip)
    assert "Invited" in tooltip.inner_text() and "100%" in tooltip.inner_text()
    assert page.locator("style").count() == 0
    assert_clean(page, errors)
    # The exact values stay on the page, one click away, in a region named by
    # the table's caption. The table follows its figure.
    table = page.locator("[data-chart='response-funnel-spec'] + .chart-table")
    table.locator("summary").click()
    assert table.locator("tbody tr").count() == 5
    region = table.locator("[role=region]")
    caption = table.locator("#" + region.get_attribute("aria-labelledby"))
    assert caption.inner_text() == "Response funnel — exact values"
    assert_clean(page, errors)
    # The page view follows its panel: a narrower window re-lays the chart
    # out, still without anything the CSP forbids.
    width = funnel.locator("svg").bounding_box()["width"]
    page.set_viewport_size({"width": 700, "height": 900})
    page.wait_for_function(
        "width => document.querySelector("
        "\"[data-chart='response-funnel-spec'] svg\").getBoundingClientRect()"
        ".width < width",
        arg=width,
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
    assert violations == []
    # Positive control: an inline script is exactly what the CSP refuses, so
    # the observer must report it, and only it. A broken observer fails here.
    with contextlib.suppress(Exception):
        page.add_script_tag(content="window.__inlineRan = 1")
    page.wait_for_function("(window.__cspViolations || []).length > 0")
    (violation,) = page.evaluate("window.__cspViolations")
    assert violation.startswith("script-src")
    assert page.evaluate("window.__inlineRan") is None


def test_markup_in_a_send_name_stays_text(page, component_origin):
    """A send name made of markup is shown as text everywhere, never parsed.

    The tooltip (vega-tooltip builds it from the spec's data), the chart's
    text mark, the summary and the notes all carry the literal name; no
    element is created from it and its handler never runs.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/charts/hostile")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    chart = page.locator("[data-chart]")
    # Vega draws the marker's label with textContent: the name, as text.
    assert HOSTILE_NAME in chart.locator("svg g.mark-text text").all_text_contents()
    assert HOSTILE_NAME in chart.locator("figcaption").inner_text()
    # The send marker's rule carries the send's tooltip (role-mark: the axis
    # grid lines are rule marks too).
    rule = chart.locator("svg g.mark-rule.role-mark line").first
    # WebKit does not hit-test a synthetic pointer onto a one-pixel dashed
    # rule, so the pointer event goes to the rule itself; it bubbles to
    # Vega's handler, which reads the item from the event's target. The test
    # is about what the tooltip shows, not about hit-testing.
    box = rule.bounding_box()
    point = {"clientX": box["x"] + box["width"] / 2, "clientY": box["y"] + 50}
    for event in ("pointerover", "pointermove"):
        rule.dispatch_event(event, point)
    tooltip = page.locator("#vg-tooltip-element")
    visible(tooltip)
    assert HOSTILE_NAME in tooltip.inner_text()
    assert tooltip.locator("img").count() == 0
    # The exact-values table follows the figure.
    page.locator("[data-chart] + .chart-table summary").click()
    assert HOSTILE_NAME in page.locator(".chart-notes").inner_text()
    assert page.locator("img[src=x]").count() == 0
    assert page.evaluate("window.__chartInjected") is None
    assert_clean(page, errors)


def test_tables_hold_every_value_without_scripts(browser_engine, component_origin):
    """With no JavaScript the summaries and tables are the complete report."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/charts")
        assert page.locator("[data-chart][data-chart-state=pending]").count() == 2
        assert page.locator("[data-chart-view] svg").count() == 0
        assert page.locator("[data-chart-view][tabindex]").count() == 0
        for table in page.locator(".chart-table").all():
            table.locator("summary").click()
        funnel, activity = page.locator(".chart-table table").all()
        assert funnel.locator("tbody tr").count() == 5
        assert "125%" in funnel.inner_text()
        assert activity.locator("tbody tr").count() == 1
        assert "Oct 3, 2026 10:00 AM" in activity.inner_text()
        assert page.locator(".chart-notes li").count() == 2
    finally:
        context.close()
