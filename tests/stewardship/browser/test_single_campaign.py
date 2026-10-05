"""Copy campaign, greyed out until the single-campaign change (ADM-12.05).

The page is the ``/campaign-settings`` component: Campaign settings for a
Testing draft. Copy campaign is an unavailable control
(components/disabled-control.html) with the #145 tip, shown and announced the
same way as an unavailable menu entry (admin-portal spec, navigation rule 10):
on hover, keyboard focus and tap, dismissed by Escape or a tap elsewhere. The
server's refusal is tested in ``test_clone_views_postgresql.py``.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

PATH = "/campaign-settings"
TIP = "Disabled; will be removed with the single-campaign change (#145)"


def _box(locator):
    """The element's box as (left, top, right, bottom)."""
    box = locator.bounding_box()
    return box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]


def _overlaps(first, second):
    """Whether two (left, top, right, bottom) boxes share any area."""
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def _copy(page):
    """The greyed Copy campaign control and its tip."""
    control = page.get_by_role("link", name="Copy campaign")
    return control, page.locator("#" + control.get_attribute("aria-describedby"))


def test_copy_campaign_is_a_described_control_that_goes_nowhere(page, component_origin):
    """No href; reachable by keyboard; the #145 tip is its description."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    control, tip = _copy(page)
    assert control.get_attribute("href") is None
    expect(control).to_have_attribute("aria-disabled", "true")
    expect(control).to_have_attribute("tabindex", "0")
    expect(control).to_have_accessible_description(TIP)
    hidden(tip)
    # Activating it shows the tip and nothing else: no navigation. (Playwright
    # treats aria-disabled as disabled, so its click must be forced.)
    control.focus()
    control.press("Enter")
    control.click(force=True)
    assert page.url == component_origin + PATH
    assert control.evaluate("node => getComputedStyle(node).cursor") == "not-allowed"


@pytest.mark.parametrize("width", [390, 1280])
def test_copy_campaign_tip_shows_on_hover(page, component_origin, width):
    """Resting the pointer shows the tip; moving onto it keeps it; Escape hides."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    control, tip = _copy(page)
    control.hover()
    # Passing over the control pops nothing: the tip waits a moment.
    page.wait_for_timeout(150)
    hidden(tip)
    visible(tip)
    assert tip.inner_text() == TIP
    # The tip sits beside the control, or above it where there is no room,
    # and never covers a control that comes after it. (Above, on a phone, it
    # may touch the line before, as a tip above any control does.)
    tip_box, own = _box(tip), _box(control)
    assert tip_box[0] >= own[2] or tip_box[3] <= own[1], (tip_box, own)
    controls = page.locator("main :is(a, button, input, select, textarea, summary)")
    for index in range(controls.count()):
        other = controls.nth(index)
        if not other.is_visible() or other.get_attribute(
            "aria-describedby"
        ) == control.get_attribute("aria-describedby"):
            continue
        if _box(other)[1] >= own[1]:
            assert not _overlaps(tip_box, _box(other)), other.inner_text()
    tip.hover()
    visible(tip)
    page.locator("main h1").hover()
    hidden(tip)
    control.hover()
    visible(tip)
    page.keyboard.press("Escape")
    hidden(tip)
    # The tip never widens the page on a phone.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_copy_campaign_tip_shows_on_keyboard_focus(page, component_origin):
    """Tabbing onto the control shows the tip; Escape hides it, focus stays."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    control, tip = _copy(page)
    control.focus()
    visible(tip)
    page.keyboard.press("Escape")
    hidden(tip)
    assert control.evaluate("node => node === document.activeElement")
    # Tabbing away hides it; tabbing back shows it again.
    page.keyboard.press("Tab")
    hidden(tip)
    page.keyboard.press("Shift+Tab")
    assert control.evaluate("node => node === document.activeElement")
    visible(tip)


def test_copy_campaign_tip_shows_on_tap(browser_engine, component_origin):
    """On a touch screen a tap shows the tip; tapping elsewhere hides it."""
    context = browser_engine.new_context(
        has_touch=True, viewport={"width": 390, "height": 844}
    )
    try:
        page = context.new_page()
        page.goto(component_origin + PATH)
        control, tip = _copy(page)
        control.tap(force=True)
        visible(tip)
        assert page.url == component_origin + PATH
        page.locator("main h1").tap()
        hidden(tip)
    finally:
        context.close()
