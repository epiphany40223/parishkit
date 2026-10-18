"""Home's Next steps across engines and widths (ADM-12.21, NAV-18).

The panel is static: its links open their pages, a greyed step names the
menu's reason as text, and pointing at or focusing a step moves nothing
(the no-layout-shift rule).
"""

import pytest

from .home_steps_components import CLOSED_PATH, LEADER_PATH, PATH
from .test_family_response import expect
from .waits import same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
# Roster changes to enter, linked from the campaign-end steps (#1590).
ROSTER = "/admin/reports/ministries/follow-up/roster/"
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
    listed = panel.locator("[data-next-step]")
    # Neither the census nor Ministry stewardship is on: Census changes,
    # Roster changes to enter (a menu entry of its own, #1660) and Ministry
    # requests are text with the menu's reason (#1590).
    expect(listed.nth(1)).to_have_text(
        "Census changes — This campaign does not include the census"
    )
    expect(listed.nth(2)).to_have_text(
        "Roster changes to enter — This campaign does not include Ministry stewardship"
    )
    expect(listed.nth(3)).to_have_text("Additional information — 3 open")
    greyed = listed.nth(4)
    expect(greyed).to_have_text(
        "Ministry requests — This campaign does not include Ministry stewardship"
    )
    for index in (1, 2, 4):
        assert listed.nth(index).get_by_role("link").count() == 0
    mail = panel.get_by_role("link", name="Sent mail", exact=True)
    expect(mail).to_have_attribute("href", "/admin/mail/sent/?state=delivery_unknown")
    before = page.evaluate(BOXES)
    for step in panel.get_by_role("link").all():
        step.hover()
        step.focus()
        assert same_place(page.evaluate(BOXES), before)


@pytest.mark.parametrize("width", [320, 1280])
def test_closed_campaign_lists_the_campaign_end_work_with_counts(
    page, component_origin, axe_source, width
):
    """At campaign end Staff get each work list with its count, linking its
    page; a Ministry leader gets Ministry requests with its open count, and
    nothing moves on hover or focus (#1590)."""
    page.set_viewport_size({"width": width, "height": 900})
    for path, expected, links in (
        (
            CLOSED_PATH,
            [
                "Response dashboard",
                "Census changes — 4 to do",
                "Roster changes to enter — 5 not yet entered",
                "Additional information — 3 open",
                "Ministry requests — 2 open",
            ],
            {
                "Census changes": "/admin/reports/census/",
                "Roster changes to enter": ROSTER,
                "Additional information": "/admin/reports/information/",
                "Ministry requests": "/admin/reports/ministries/",
            },
        ),
        (
            LEADER_PATH,
            ["Ministry requests — 2 open"],
            {"Ministry requests": "/admin/reports/ministries/"},
        ),
    ):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == []
        panel = page.get_by_role("region", name="Next steps")
        expect(panel.locator("[data-next-step]")).to_have_text(expected)
        for name, href in links.items():
            expect(panel.get_by_role("link", name=name, exact=True)).to_have_attribute(
                "href", href
            )
        before = page.evaluate(BOXES)
        for step in panel.get_by_role("link").all():
            step.hover()
            step.focus()
            assert same_place(page.evaluate(BOXES), before)
