"""My review work across engines and widths (#1690)."""

import pytest

from .review_work_components import HOME_PATH, LEADER_PATH, PATH
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Each card's box, to check that hovering or focusing one moves nothing.
BOXES = """() => [...document.querySelectorAll('[data-link-card]')].map(card => {
  const r = card.getBoundingClientRect();
  return [r.x, r.y, r.width, r.height];
})"""


@pytest.mark.parametrize("width", [320, 1366])
def test_staff_panels_link_each_stage_and_fit(
    page, component_origin, axe_source, width
):
    """One panel per stream, named and ordered as the Review work menu
    (#2108), each stage a link card read as its
    stage, count and what is counted, linking its page at that Stage
    (#1818; Limitations, Entered incorrectly and Not seen in
    ParishSoft since #2101); no sideways scroll on a phone, and the whole
    page fits a laptop window."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    expect(page.get_by_role("heading", level=1)).to_have_text("My review work")
    assert page.locator("[data-review-stream]").evaluate_all(
        "panels => panels.map(p => p.dataset.reviewStream)"
    ) == [
        "census",
        "ministry",
        "financial",
        "additional_information",
        "cannot_participate",
    ]
    census = page.get_by_role("region", name="Census changes")
    expect(census.get_by_role("link")).to_have_count(4)
    link = census.get_by_role("link", name="To review 1,234 changes")
    expect(link).to_have_attribute("href", "/admin/reports/census/?stage=to_review")
    expect(census.get_by_role("link", name="Conflict 2 changes")).to_have_attribute(
        "href", "/admin/reports/census/?stage=conflict"
    )
    expect(
        census.get_by_role("link", name="Entered incorrectly 0 changes")
    ).to_have_attribute("href", "/admin/reports/census/?stage=entered_incorrectly")
    # Ready to update is the Administrator's; Staff do not see it.
    expect(census.get_by_text("Ready to update")).to_have_count(0)
    ministry = page.get_by_role("region", name="Ministry requests")
    expect(ministry.get_by_role("link", name="To review 8 requests")).to_have_attribute(
        "href", "/admin/reports/ministries/?stage=to_review"
    )
    expect(
        ministry.get_by_role("link", name="To enter by hand 3 requests")
    ).to_have_attribute("href", "/admin/reports/ministries/?stage=to_enter_by_hand")
    # Not seen in ParishSoft shows while a request carries the flag (#2101).
    expect(
        ministry.get_by_role("link", name="Not seen in ParishSoft 1 requests")
    ).to_have_attribute("href", "/admin/reports/ministries/?stage=not_seen")
    # A stream the menu greys out keeps its cards, greyed with the reason.
    financial = page.get_by_role("region", name="Pledges")
    expect(financial.get_by_role("link")).to_have_count(0)
    expect(financial).to_contain_text("does not include Financial stewardship")
    information = page.get_by_role("region", name="Additional information")
    expect(information.get_by_role("link", name="To handle 1 items")).to_have_attribute(
        "href", "/admin/reports/information/?stage=to_handle"
    )
    # Limitations links its To review and To handle stages.
    limitations = page.get_by_role("region", name="Limitations")
    expect(
        limitations.get_by_role("link", name="To review 4 answers")
    ).to_have_attribute("href", "/admin/reports/limitations/?stage=to_review")
    expect(
        limitations.get_by_role("link", name="To handle 0 answers")
    ).to_have_attribute("href", "/admin/reports/limitations/?stage=to_handle")
    if width == 1366:
        # An overview page fits a laptop window without scrolling.
        assert page.evaluate(
            "document.querySelector('[data-review-stream=cannot_participate]')"
            ".getBoundingClientRect().bottom <= innerHeight"
        )


def test_cards_do_not_move_on_hover_or_focus(page, component_origin):
    """Hover and focus change only the underline (no layout shift)."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + PATH)
    before = page.evaluate(BOXES)
    link = page.get_by_role("link", name="Conflict 2 changes")
    link.hover()
    assert page.evaluate(BOXES) == before
    link.focus()
    assert page.evaluate(BOXES) == before


@pytest.mark.parametrize("width", [320, 1366])
def test_a_leader_sees_only_their_ministries(page, component_origin, axe_source, width):
    """A leader's one panel has a To review card per Ministry they lead,
    each linking the queue filtered to it; no other stream is shown."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + LEADER_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert page.locator("[data-review-stream]").evaluate_all(
        "panels => panels.map(p => p.dataset.reviewStream)"
    ) == ["ministry"]
    panel = page.get_by_role("region", name="Ministry requests")
    expect(
        panel.get_by_role(
            "link", name="Extraordinary Ministers of Holy Communion 3 to review"
        )
    ).to_have_attribute("href", "/admin/reports/ministries/?ministry=12")
    expect(panel.get_by_role("link", name="Food pantry 0 to review")).to_have_attribute(
        "href", "/admin/reports/ministries/?ministry=9"
    )


def test_an_administrators_home_has_the_review_work_panel(
    page, component_origin, axe_source
):
    """Home keeps its own panels and adds Review work, with Ready to update
    opening Update ParishSoft."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + HOME_PATH)
    expect(page.get_by_role("heading", level=1)).to_have_text("Home")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    review = page.get_by_role("region", name="Review work")
    census = review.get_by_role("region", name="Census changes")
    expect(
        census.get_by_role("link", name="Ready to update 5 changes")
    ).to_have_attribute("href", "/admin/reports/census/update-parishsoft/")
    expect(review.get_by_role("heading", level=3)).to_have_count(5)
