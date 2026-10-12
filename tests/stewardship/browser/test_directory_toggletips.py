"""Directory filter-bar toggletips float without moving the bar (#957)."""

import pytest
from django.urls import reverse

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Page coordinates of every filter label and control, so scrolling the open
# bubble into view does not count as a layout change.
LAYOUT = """() => [...document.querySelectorAll(
    '#table-filters :is(label, input:not([type=hidden]), select, button)'
)].map((item) => {
    const box = item.getBoundingClientRect();
    return [box.x + scrollX, box.y + scrollY, box.width, box.height];
})"""


# The directory as an Administrator sees a Testing preset, with the
# Responses shown switch and its toggletip (#933).
TESTING = reverse("admin:family_directory") + (
    "?response=submitted&responses=yes&sort=submitted&mode=testing"
)


@pytest.mark.parametrize("path", ["/family-directory", TESTING])
@pytest.mark.parametrize("width", [400, 1280])
def test_directory_toggletips_float_without_moving_the_filter_bar(
    page, component_origin, width, path
):
    """Opening a filter-bar toggletip moves no label or control.

    The bar aligns its fields at the bottom, so an in-flow bubble would push
    the label and its "i" button up under the pointer. Each bubble floats
    instead, visible and inside the viewport, at desktop and phone widths.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    buttons = page.locator("#table-filters .toggletip-button")
    # At least Email availability, Campaign mail can reach and Include
    # mailing columns; later filters add their own.
    assert buttons.count() >= 3
    for index in range(buttons.count()):
        button = buttons.nth(index)
        bubble = page.locator("#" + button.get_attribute("aria-controls"))
        before = page.evaluate(LAYOUT)
        button.click()
        visible(bubble)
        assert page.evaluate(LAYOUT) == before
        bubble.scroll_into_view_if_needed()
        box = bubble.bounding_box()
        width_now, height_now = page.evaluate(
            "[document.documentElement.clientWidth, innerHeight]"
        )
        # One pixel of slack: scrolling lands on whole pixels, the bubble's
        # edges on fractions.
        assert box["x"] >= 0 and box["x"] + box["width"] <= width_now + 1
        assert box["y"] >= 0 and box["y"] + box["height"] <= height_now + 1
        page.keyboard.press("Escape")
        assert bubble.is_hidden()
