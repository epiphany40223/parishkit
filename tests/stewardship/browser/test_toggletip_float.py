"""Every "i" toggletip floats beside its button and moves nothing (#1289).

One rule for the shared toggletip wherever its button sits: a field's label
row (alone or in a .field-row), a table heading and a filter bar. Opening a
bubble must not move the control that follows its button, or any other label
or control on the page (UI conventions, "No layout shift"), and the bubble
must lie inside the viewport at desktop and phone widths: below its button,
or above it when only there it fits.
"""

import pytest
from django.urls import reverse

from .waits import has_attribute, painted, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Page coordinates of every label and control, so that the page scrolling
# while a bubble opens does not count as a layout change.
LAYOUT = """() => [...document.querySelectorAll(
    'label, legend, th, input:not([type=hidden]), select, textarea, button'
)].map((item) => {
    const box = item.getBoundingClientRect();
    return [box.x + scrollX, box.y + scrollY, box.width, box.height];
})"""

# Where the open bubble sits against the viewport and its own button: whether
# it is wholly inside the viewport (one pixel of slack, since its edges land
# on fractions), and whether it is just below or just above the button.
PLACE = """id => {
    const bubble = document.getElementById(id).getBoundingClientRect();
    const button = document.querySelector(`[aria-controls="${id}"]`)
        .getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    return {
        inside: bubble.left >= -1 && bubble.top >= -1
            && bubble.right <= width + 1 && bubble.bottom <= innerHeight + 1,
        below: Math.abs(bubble.top - button.bottom - 6) < 1,
        above: Math.abs(button.top - 6 - bubble.bottom) < 1,
        scrolls: document.documentElement.scrollWidth > innerWidth,
    };
}"""

# Representative Admin pages, the toggletip buttons each one checks (shown
# ones only: the directory's closed export form holds one more), and how many
# of them the page has at least, so that losing tips cannot quietly shrink
# what the test covers.
CASES = {
    # A setup form's field help (accounts/field_tips.py) in a .label-row. The
    # ParishSoft connection form has only the key's tip since the expected
    # organization ID field was removed (#1554).
    "label-row": ("/setup-credential", ".label-row .toggletip-button", 1),
    # A follow-up edit form's short fields share a .field-row (#1014):
    # Outcome and How.
    "field-row": ("/followup-item", ".field-row .label-row .toggletip-button", 2),
    # A report table's column headings (#932): Pledged and Contributed.
    "table-heading": ("/financial-report", "th .toggletip-button", 2),
    # The Family directory's filter bar (#957, #1158): at least Email
    # availability, Campaign mail can reach and Include mailing columns.
    "filter-bar": ("/family-directory", ".filter-bar .toggletip-button:visible", 3),
    # The directory as an Administrator sees a Testing preset, which adds the
    # Responses shown switch (#933; a binary choice above the filter bar
    # since #1535) and its toggletip.
    "filter-bar-testing": (
        reverse("admin:family_directory")
        + "?response=submitted&responses=yes&sort=submitted&mode=testing",
        "#directory-mode .toggletip-button, .filter-bar .toggletip-button:visible",
        4,
    ),
}


@pytest.mark.parametrize("width", [400, 1280])
@pytest.mark.parametrize("case", CASES)
def test_toggletip_floats_without_moving_the_page(page, component_origin, case, width):
    """Each tip opens a floating bubble in the viewport and moves no control."""
    path, selector, least = CASES[case]
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    buttons = page.locator(selector)
    assert buttons.count() >= least, (selector, buttons.count())
    for index in range(buttons.count()):
        button = buttons.nth(index)
        tip = button.get_attribute("aria-controls")
        bubble = page.locator("#" + tip)
        button.scroll_into_view_if_needed()
        painted(page)
        before = page.evaluate(LAYOUT)
        button.click()
        visible(bubble)
        has_attribute(button, "aria-expanded", "true")
        assert same_place(page.evaluate(LAYOUT), before), (case, tip)
        place = page.evaluate(PLACE, tip)
        assert place["inside"] and (place["below"] or place["above"]), (tip, place)
        assert not place["scrolls"], tip
        # A second click closes it again, still moving nothing.
        button.click()
        has_attribute(button, "aria-expanded", "false")
        assert bubble.is_hidden()
        assert same_place(page.evaluate(LAYOUT), before), (case, tip)
    assert not failures


@pytest.mark.parametrize("width", [400, 1280])
def test_toggletip_flips_above_when_there_is_no_room_below(
    page, component_origin, width
):
    """A button near the window's bottom opens its bubble above it."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/setup-credential")
    button = page.locator(".label-row .toggletip-button").first
    tip = button.get_attribute("aria-controls")
    # End the window just under the button, leaving no room below it.
    bottom = page.evaluate(
        "id => document.querySelector(`[aria-controls='${id}']`)"
        ".getBoundingClientRect().bottom + scrollY",
        tip,
    )
    page.set_viewport_size({"width": width, "height": int(bottom) + 20})
    page.evaluate("window.scrollTo(0, 0)")
    painted(page)
    button.click()
    visible(page.locator("#" + tip))
    place = page.evaluate(PLACE, tip)
    assert place["inside"] and place["above"], place


def test_right_edge_toggletip_adds_no_sideways_scroll(page, component_origin):
    """No System health tip, right-edge ones included, widens the page (#1316).

    At a 320 px phone width the bubble must stay inside the viewport, or
    WebKit grows the document sideways and lets the whole page pan. Check
    when the bubble opens and again after it has settled.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/system-health")
    buttons = page.locator(".toggletip-button:visible")
    assert buttons.count() >= 1
    for index in range(buttons.count()):
        button = buttons.nth(index)
        tip = button.get_attribute("aria-controls")
        button.scroll_into_view_if_needed()
        painted(page)
        button.click()
        visible(page.locator("#" + tip))
        assert not page.evaluate(PLACE, tip)["scrolls"], tip
        # The page must also stay put once the bubble has been open a while.
        page.wait_for_timeout(500)
        assert not page.evaluate(PLACE, tip)["scrolls"], tip
        button.click()
        has_attribute(button, "aria-expanded", "false")
