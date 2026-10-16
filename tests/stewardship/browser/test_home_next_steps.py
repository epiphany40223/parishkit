"""Home's Next steps across engines and widths (ADM-12.21, NAV-18).

The panel is static: its links open their pages, a greyed step names the
menu's reason as text, and pointing at or focusing a step moves nothing
(the no-layout-shift rule).
"""

import pytest

from .home_steps_components import PATH
from .test_family_response import expect
from .waits import same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Every step's box in page coordinates (hovering scrolls it into view),
# to compare before and after pointing and focusing. Compared with
# same_place (half a pixel): Firefox's fractional scroll offset adds float
# noise to the same box, and rounding to a tenth can flip 1409.95 between
# 1409.9 and 1410; a real shift moves at least a whole pixel.
BOXES = """() => [...document.querySelectorAll('[data-next-step]')].map(e => {
  const r = e.getBoundingClientRect();
  return [r.x + scrollX, r.y + scrollY, r.width, r.height];
})"""


@pytest.mark.parametrize("width", [320, 1280])
def test_next_steps_link_their_pages_and_never_shift(
    page, component_origin, axe_source, width
):
    """Each step links its page or names its reason; nothing moves on hover."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    panel = page.get_by_role("region", name="Next steps")
    expect(panel).to_be_visible()
    expect(panel.locator("[data-today]")).to_have_text(
        "Today: 12 Families responded in the past 24 hours."
    )
    expect(
        panel.get_by_role("link", name="Response dashboard", exact=True)
    ).to_have_attribute("href", "/admin/reports/responses/")
    expect(panel.locator("[data-next-step]").nth(1)).to_have_text(
        "Additional information — 3 open"
    )
    # Ministry stewardship is off: the step is text with the menu's reason.
    greyed = panel.locator("[data-next-step]").nth(2)
    expect(greyed).to_have_text(
        "Ministry requests — This campaign does not include Ministry stewardship"
    )
    assert greyed.get_by_role("link").count() == 0
    mail = panel.get_by_role("link", name="Outgoing mail", exact=True)
    expect(mail).to_have_attribute(
        "href", "/admin/mail/outgoing/?state=delivery_unknown"
    )
    before = page.evaluate(BOXES)
    for step in panel.get_by_role("link").all():
        step.hover()
        step.focus()
        assert same_place(page.evaluate(BOXES), before)
