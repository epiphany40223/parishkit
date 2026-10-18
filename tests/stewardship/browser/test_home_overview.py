"""Home as an overview dashboard (#1673; admin-portal spec "Home page").

At a laptop window (1366 x 768) with the menu open, a busy Administrator's
Home shows the Needs attention row, the campaign strip, the response tiles
and every Next steps card without scrolling; at 390 px nothing scrolls
sideways. Each count line links the page that lists its items.

The fold depends on the fonts: CI's Linux engines draw DejaVu Sans, wider
than macOS's system font, so text wraps onto more lines there. Measure a
layout change in the Linux engines too (the playwright/python image with
fonts-dejavu-core), not only on macOS.
"""

import pytest

from .home_overview_components import PATH
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# The bottom of each overview region, in page coordinates.
BOTTOMS = """() => Object.fromEntries([
  ['alerts', '.home-alerts'], ['campaign', '[data-home-campaign]'],
  ['tiles', '[data-home-participation]'], ['steps', '[data-next-steps]'],
].map(([name, selector]) => {
  const box = document.querySelector(selector).getBoundingClientRect();
  return [name, box.bottom + scrollY];
}))"""


def test_overview_fits_a_laptop_window(page, component_origin, axe_source):
    """Every overview region ends above the fold at 1366 x 768, menu open."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + PATH)
    assert page.locator(".admin-nav, nav").first.is_visible()
    bottoms = page.evaluate(BOTTOMS)
    assert all(bottom <= 768 for bottom in bottoms.values()), bottoms
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_overview_counts_link_their_pages(page, component_origin):
    """Each count line and link card names its count and links its page."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + PATH)
    alerts = page.get_by_role("list", name="Needs attention")
    expect(alerts.locator("[data-home-health]")).to_have_text(
        "Error 2 system problems. See System health"
    )
    expect(alerts.get_by_role("link", name="See System health")).to_have_attribute(
        "href", "/admin/system/health/"
    )
    expect(alerts.locator("[data-home-failed-tasks]")).to_contain_text(
        "3 background tasks failed in the past 24 hours."
    )
    expect(alerts.get_by_role("link", name="See automation notices")).to_have_attribute(
        "href", "/admin/users/automation/#automation-notices"
    )
    campaign = page.get_by_role("region", name="Stewardship 2027")
    expect(campaign.locator("[data-days-left] dd")).to_have_text("51 days")
    tiles = page.get_by_role("list", name="Family participation")
    expect(tiles.locator("[data-stat]")).to_have_count(3)
    expect(campaign.get_by_role("link", name="Response dashboard")).to_have_attribute(
        "href", "/admin/reports/responses/"
    )
    steps = page.get_by_role("list", name="Next steps")
    roster = steps.get_by_role(
        "link", name="Roster changes to enter 12 not yet entered"
    )
    expect(roster).to_be_visible()
    assert "link-card-attention" in roster.locator("..").get_attribute("class")
    # Sent mail's other-mode link sits beside the card's own link, so the
    # card keeps its two lines (#1576).
    mail = steps.get_by_role("link", name="Sent mail 2 may not have arrived")
    other = steps.get_by_role("link", name="1 in Testing may not have arrived")
    expect(other).to_have_attribute(
        "href", "/admin/mail/sent/?state=needs_review&mode=testing"
    )
    main_box, other_box = mail.bounding_box(), other.bounding_box()
    assert other_box["x"] >= main_box["x"] + main_box["width"] - 0.5
    assert other_box["y"] < main_box["y"] + main_box["height"]


def test_overview_has_no_sideways_scroll_on_a_phone(page, component_origin):
    """At 390 px the count lines, strip, tiles and cards stack."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(component_origin + PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
