"""My review work across engines and widths (#1690), a Ministry leader's two
steps included (#2405)."""

import pytest

from .review_work_components import (
    HOME_PATH,
    LEADER_EMPTY_PATH,
    LEADER_GREYED_PATH,
    LEADER_LIMIT_PATH,
    LEADER_PATH,
    PATH,
)
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
        # An overview page fits a laptop window without scrolling (the
        # ui-conventions Overview pages rule). Measure with the page's own
        # fonts, not a wider fallback still showing while they load.
        page.evaluate("document.fonts.ready.then(() => true)")
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


# The menu's count beside Review in bulk (not the Step 2 button's link).
BULK_BADGE = 'nav a[href="/admin/reports/ministries/"] .admin-menu-count'
# Each of a leader's Step 2 choices' box, and each Step 1 download button's.
CHOICES = """() => [...document.querySelectorAll('[data-leader-choice]')].map(card => {
  const r = card.getBoundingClientRect();
  return [r.x, r.y, r.width, r.height];
})"""
LEADER_BOXES = """() => [...document.querySelectorAll(
  '[data-leader-choice], [data-leader-choice] .button, #leader-export button'
)].map(node => {
  const r = node.getBoundingClientRect();
  return [r.x, r.y, r.width, r.height];
})"""


# From the bottom of Step 1's download buttons to the top of the line
# under the row.
GAP = """() => {
  const box = (id) => document.getElementById(id).getBoundingClientRect();
  return box('leader-export-reason').top - box('leader-export-buttons').bottom;
}"""


def _choice(page, key):
    """One of a leader's Step 2 choices, by its key (one or bulk)."""
    return page.locator(f"[data-leader-choice={key}]")


@pytest.mark.parametrize("width", [320, 390, 768, 1366])
def test_a_leaders_page_is_two_steps(page, component_origin, axe_source, width):
    """A Ministry leader's My review work (#2405): Step 1 downloads Review in
    bulk's file in XLSX or PDF with large buttons; Step 2 offers Review one
    at a time or Review in bulk, named as the menu names them, each with its
    one line and count, the count the menu's badge shows. The two choices
    are equal: the same width and height, side by side with room and
    stacked on a phone. No stream panels, no sideways scroll, and the steps
    fit a laptop window."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + LEADER_PATH)
    # Measure with the page's own fonts, not a fallback still showing.
    page.evaluate("document.fonts.ready.then(() => true)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    expect(page.locator("[data-review-stream]")).to_have_count(0)
    for name in ("Step 1: Download the requests", "Step 2: Review the requests"):
        expect(
            page.get_by_role("heading", level=2, name=name, exact=True)
        ).to_be_visible()
    downloads = page.get_by_role("group", name="Download the requests")
    for name in ("Export XLSX", "Export PDF"):
        button = downloads.get_by_role("button", name=name, exact=True)
        expect(button).to_be_enabled()
        # Large: at least the portal's 44px button height.
        assert button.bounding_box()["height"] >= 44
    one, bulk = _choice(page, "one"), _choice(page, "bulk")
    expect(one.get_by_role("link", name="Review one at a time")).to_have_attribute(
        "href", "/admin/reports/review/"
    )
    expect(bulk.get_by_role("link", name="Review in bulk")).to_have_attribute(
        "href", "/admin/reports/ministries/"
    )
    expect(one).to_contain_text("One Member per page, with Previous and Next.")
    expect(bulk).to_contain_text("Every request in one table.")
    for choice in (one, bulk):
        expect(choice).to_contain_text("3 to review")
    # The menu's Review in bulk badge counts the same requests.
    expect(page.locator(BULK_BADGE)).to_have_text("3")
    (first, second) = page.evaluate(CHOICES)
    # The same width and height, side by side or stacked.
    assert abs(first[2] - second[2]) < 0.5 and abs(first[3] - second[3]) < 0.5
    if width >= 768:
        assert abs(first[1] - second[1]) < 0.5 and second[0] > first[0] + first[2]
    else:
        assert abs(first[0] - second[0]) < 0.5 and second[1] > first[1] + first[3]
    if width == 1366:
        # Both steps, the choices whole, fit a 1366 x 768 window.
        assert second[1] + second[3] <= 768


def test_a_leaders_page_does_not_move_on_hover_or_focus(page, component_origin):
    """Hovering or focusing a choice's button or a download button moves
    nothing (no layout shift)."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + LEADER_PATH)
    page.evaluate("document.fonts.ready.then(() => true)")
    before = page.evaluate(LEADER_BOXES)
    for control in (
        _choice(page, "one").get_by_role("link"),
        _choice(page, "bulk").get_by_role("link"),
        page.get_by_role("button", name="Export PDF", exact=True),
    ):
        control.hover()
        assert page.evaluate(LEADER_BOXES) == before
        control.focus()
        assert page.evaluate(LEADER_BOXES) == before


@pytest.mark.parametrize("width", [390, 1366])
def test_a_leader_with_nothing_to_review_is_told_so(
    page, component_origin, axe_source, width
):
    """With nothing To review Step 2 says so plainly, and both choices stay
    open: both pages still list the requests already decided. The longer
    line still leaves both steps inside a 1366 x 768 window."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + LEADER_EMPTY_PATH)
    page.evaluate("document.fonts.ready.then(() => true)")
    expect(page.locator(".leader-choices")).to_contain_text(
        "Nothing is waiting for you to review right now. Either one still shows "
        "the requests already decided."
    )
    # Nothing To review: no menu badge either.
    expect(page.locator(BULK_BADGE)).to_have_count(0)
    for key in ("one", "bulk"):
        expect(_choice(page, key)).to_contain_text("0 to review")
        expect(_choice(page, key).get_by_role("link")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width == 1366:
        (_first, second) = page.evaluate(CHOICES)
        assert second[1] + second[3] <= 768
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_greyed_pages_leave_every_button_unavailable_with_a_reason(
    page, component_origin, axe_source
):
    """While the menu greys the leader's pages out, the choices' buttons and
    the downloads are unavailable, each with the menu's reason, and the two
    cards keep their shape. The downloads' reason sits right under their
    buttons, with no empty status line between (no export can start)."""
    page.set_viewport_size({"width": 1366, "height": 768})
    page.goto(component_origin + LEADER_GREYED_PATH)
    for key, name in (("one", "Review one at a time"), ("bulk", "Review in bulk")):
        button = _choice(page, key).get_by_role("button", name=name)
        expect(button).to_be_disabled()
        reason = page.locator(f"#leader-choice-{key}-reason")
        expect(reason).not_to_be_empty()
        expect(button).to_have_accessible_description(reason.inner_text())
    for name in ("Export XLSX", "Export PDF"):
        expect(page.get_by_role("button", name=name, exact=True)).to_be_disabled()
    expect(page.locator("#leader-export-reason")).not_to_be_empty()
    gap = page.evaluate(GAP)
    assert 0 <= gap <= 16
    (first, second) = page.evaluate(CHOICES)
    assert abs(first[2] - second[2]) < 0.5 and abs(first[3] - second[3]) < 0.5
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


@pytest.mark.parametrize("width", [390, 1366])
def test_a_file_too_large_for_a_pdf_offers_only_xlsx(
    page, component_origin, axe_source, width
):
    """While Step 1's file would hold more requests than one PDF may, Export
    PDF is unavailable, described by the line below the row in this page's
    words; Export XLSX stays."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + LEADER_LIMIT_PATH)
    line = page.locator("#leader-export-pdf-limit")
    expect(line).to_have_text("The PDF is limited to 1,000 requests: choose XLSX.")
    pdf = page.get_by_role("button", name="Export PDF", exact=True)
    expect(pdf).to_be_disabled()
    expect(pdf).to_have_accessible_description(line.inner_text())
    expect(page.get_by_role("button", name="Export XLSX", exact=True)).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


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
