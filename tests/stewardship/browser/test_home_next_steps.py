"""Home's Next steps across engines and widths (ADM-12.21, NAV-18).

The panel is a grid of link cards (#1673) and static: its cards link their
pages, a greyed card names the menu's reason as text, and pointing at or
focusing a card moves nothing (the no-layout-shift rule).
"""

import re

import pytest

from .home_steps_components import CLOSED_PATH, LEADER_PATH, PATH
from .test_family_response import expect
from .waits import same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
# Ministry requests at To enter by hand, linked from the campaign-end steps
# (#1590; Roster changes to enter until R39 finding 2 folded it in).
ROSTER = "/admin/reports/ministries/?stage=to_enter_by_hand"
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Every step's box in page coordinates (hovering scrolls it into view),
# to compare before and after pointing and focusing. Compared with
# same_place (half a pixel): Firefox's fractional scroll offset adds float
# noise to the same box, and rounding to a tenth can flip 1409.95 between
# 1409.9 and 1410; a real shift moves at least a whole pixel.
BOXES = """() => [...document.querySelectorAll('[data-link-card]')].map(e => {
  const r = e.getBoundingClientRect();
  return [r.x + scrollX, r.y + scrollY, r.width, r.height];
})"""


@pytest.mark.parametrize("width", [320, 1280])
def test_next_steps_link_their_pages_and_never_shift(
    page, component_origin, axe_source, width
):
    """Each card links its page or names its reason; nothing moves on hover."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    panel = page.get_by_role("region", name="Next steps")
    expect(panel).to_be_visible()
    # The 24-hour count is a Responses tile now, not a Today line (#1673),
    # and the Response dashboard is linked from the campaign panel.
    expect(panel.locator("[data-today]")).to_have_count(0)
    expect(panel.get_by_role("link", name="Response dashboard")).to_have_count(0)
    listed = panel.locator("[data-link-card]")
    # Neither the census nor Ministry stewardship is on: Census changes,
    # Ministry requests to enter by hand (following Ministry requests' entry)
    # and Ministry requests are text with the menu's reason (#1590).
    expect(listed.nth(0)).to_have_text(
        "Census changes This campaign does not include the census"
    )
    expect(listed.nth(1)).to_have_text(
        "Ministry requests to enter by hand This campaign does not include "
        "Ministry stewardship"
    )
    expect(listed.nth(2)).to_have_text("Additional information 3 open")
    expect(listed.nth(2)).to_have_class("link-card link-card-attention")
    greyed = listed.nth(3)
    expect(greyed).to_have_text(
        "Ministry requests This campaign does not include Ministry stewardship"
    )
    for index in (0, 1, 3):
        assert listed.nth(index).get_by_role("link").count() == 0
        expect(listed.nth(index)).to_have_class("link-card link-card-off")
    mail = panel.get_by_role("link", name="Sent mail 2 may not have arrived")
    expect(mail).to_have_attribute(
        "href", "/admin/mail/sent/?state=needs_review&mode=production"
    )
    # The other mode's emails: a second, small link in the same card, with
    # its own count and list (#1576).
    other = panel.get_by_role("link", name="1 in Testing may not have arrived")
    expect(other).to_have_attribute(
        "href", "/admin/mail/sent/?state=needs_review&mode=testing"
    )
    expect(other).to_have_text("1 in Testing may not have arrived")
    before = page.evaluate(BOXES)
    for step in panel.get_by_role("link").all():
        step.hover()
        step.focus()
        assert same_place(page.evaluate(BOXES), before)


@pytest.mark.parametrize("width", [320, 1280])
def test_closed_campaign_lists_the_campaign_end_work_with_counts(
    page, component_origin, axe_source, width
):
    """At campaign end Staff get a card for each work list with its count,
    linking its page; a Ministry leader gets Ministry requests with its open
    count, and nothing moves on hover or focus (#1590, #1673)."""
    page.set_viewport_size({"width": width, "height": 900})
    for path, expected, links in (
        (
            CLOSED_PATH,
            [
                "Census changes 4 to do",
                "Ministry requests to enter by hand 5 to enter by hand",
                "Additional information 3 open",
                "Ministry requests 2 open",
            ],
            {
                "Census changes": "/admin/reports/census/",
                "Ministry requests to enter by hand": ROSTER,
                "Additional information": "/admin/reports/information/",
                "Ministry requests": "/admin/reports/ministries/",
            },
        ),
        (
            LEADER_PATH,
            ["Ministry requests 2 open"],
            {"Ministry requests": "/admin/reports/ministries/"},
        ),
    ):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == []
        panel = page.get_by_role("region", name="Next steps")
        expect(panel.locator("[data-link-card]")).to_have_text(expected)
        for name, href in links.items():
            # Each card's name, then its count: "Ministry requests" is also
            # the start of "Ministry requests to enter by hand".
            card = panel.get_by_role("link", name=re.compile(rf"^{re.escape(name)} \d"))
            expect(card).to_have_attribute("href", href)
        before = page.evaluate(BOXES)
        for step in panel.get_by_role("link").all():
            step.hover()
            step.focus()
            assert same_place(page.evaluate(BOXES), before)
