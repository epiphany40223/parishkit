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
    page.mouse.move(0, 0)
    assert tip.is_hidden()
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
    assert slider.evaluate(offset) == resting
    page.keyboard.press("Escape")
    assert tip.is_hidden()
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

    At 15% of the image height the plot of the email drawing (#720) begins;
    the older Participation drawing's plot starts lower, at 26%, so a hover
    there only shows a tooltip when the page uses the retained drawing's layout.
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
    """The funnel table and figures are readable, accessible and fit (#477)."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/daily-digest-funnel")
    funnel = page.locator("[data-digest-funnel]")
    visible(funnel.get_by_role("heading", name="Response funnel"))
    assert funnel.locator("tbody tr").count() == 5
    text = funnel.inner_text()
    assert "includes mail-scanner prefetches" in text and "40%" in text
    assert "Submitted more than once" in text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
