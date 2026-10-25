"""Review one at a time's start page, run pages and card (#2123)."""

import pytest

from .review_wizard_components import CARD_PATH, PAGE_PATHS, START_PATH
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Each navigation control's box, by row and slot, to compare across pages.
SLOTS = """() => Object.fromEntries([...document.querySelectorAll(
  '[data-review-nav] [data-nav-slot]')].map(slot => {
    const row = slot.closest('[data-review-nav]').dataset.reviewNav;
    const box = slot.getBoundingClientRect();
    return [row + ':' + slot.dataset.navSlot,
      [Math.round(box.x), Math.round(box.y), Math.round(box.width)]];
  }))"""


def visible_names(page):
    """The name of every enabled, shown form control of the start form."""
    return page.locator("#review-wizard-start").evaluate(
        """form => [...form.elements].filter(el => el.name && !el.disabled
          && el.offsetParent !== null).map(el => el.name)"""
    )


@pytest.mark.parametrize("width", [320, 1366])
def test_start_page_choices_show_only_the_chosen_streams_filters(
    page, component_origin, axe_source, width
):
    """The stream and the mode show their own filters in place, and only the
    shown ones are sent; a greyed stream says why; runs list Continue and
    Discard; nothing scrolls sideways on a phone."""
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + START_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    expect(page.get_by_role("heading", level=1)).to_have_text("Review one at a time")
    runs = page.get_by_role("table", name="Your unfinished reviews")
    expect(runs.get_by_role("row")).to_have_count(3)
    continue_ = runs.get_by_role(
        "link", name="Continue Census changes, Family 12 of 87, Member 2 of 3"
    )
    expect(continue_).to_have_attribute(
        "href",
        "/admin/reports/review/40/?run=" + ("00000000-0000-0000-0000-00000000084b"),
    )
    expect(
        runs.get_by_role("button", name="Discard Ministry requests, Member 3 of 9")
    ).to_be_visible()
    pledges = page.get_by_role("radio", name="Pledges")
    expect(pledges).to_be_disabled()
    expect(page.get_by_text("Pledges: This campaign does not include")).to_be_visible()
    # Census changes, Basic: one name search.
    expect(page.get_by_role("radio", name="Census changes")).to_be_checked()
    assert sorted(set(visible_names(page))) == [
        "mode",
        "search",
        "sort",
        "start",
        "stream",
    ]
    # Advanced: the Stage, Waiting first, and Census changes' own filters.
    page.get_by_role("radio", name="Advanced").check()
    stage = page.locator("#census-stage")
    expect(stage).to_be_visible()
    assert stage.locator("option").first.text_content() == "Waiting"
    assert sorted(set(visible_names(page))) == [
        "kind",
        "mode",
        "route",
        "search",
        "sort",
        "stage",
        "start",
        "stream",
    ]
    # Another stream redraws the filters in place with its own.
    page.get_by_role("radio", name="Ministry requests").check()
    expect(page.locator("#census-stage")).to_be_hidden()
    expect(page.locator("#filter-ministry")).to_be_visible()
    assert "members" in visible_names(page)
    expect(page.get_by_role("button", name="Start")).to_be_enabled()


def test_the_navigation_rows_keep_their_place_on_every_page(page, component_origin):
    """Previous, Finish and Next sit in the same spots on a Family page, a
    Member page and the last page, in both rows; page 1 keeps Previous's
    slot empty and the last page's Next slot is Finish; the bottom row is
    at the bottom of the screen."""
    page.set_viewport_size({"width": 1024, "height": 700})
    found = []
    for path in PAGE_PATHS:
        page.goto(component_origin + path)
        found.append(page.evaluate(SLOTS))

    # Every control's spot, wherever its slot shows one: Finish and Next on
    # the first two pages, Previous on the last two, the Next slot on all.
    def spot(slots, name):
        """A slot's (x, y), ignoring its width (an empty slot has none)."""
        return slots[name][:2]

    for row in ("top", "bottom"):
        # The Next slot is right-aligned: Next, or Finish on the last page,
        # ends at the same spot.
        ends = {
            (f[f"{row}:next"][0] + f[f"{row}:next"][2], f[f"{row}:next"][1])
            for f in found
        }
        assert len(ends) == 1, (row, ends)
        assert spot(found[0], f"{row}:action") == spot(found[1], f"{row}:action")
        assert spot(found[1], f"{row}:back") == spot(found[2], f"{row}:back")
    first, middle, last = (component_origin + path for path in PAGE_PATHS)
    page.goto(first)
    top = page.locator('[data-review-nav="top"]')
    expect(top.get_by_role("button", name="Previous")).to_have_count(0)
    expect(top.get_by_role("button", name="Finish")).to_be_visible()
    expect(top.get_by_role("button", name="Next")).to_be_visible()
    expect(top.locator("[data-review-progress]")).to_have_text("Family 1 of 1")
    bottom = page.locator('[data-review-nav="bottom"]').bounding_box()
    assert abs(bottom["y"] + bottom["height"] - 700) <= 1
    page.goto(last)
    top = page.locator('[data-review-nav="top"]')
    expect(top.get_by_role("button", name="Next")).to_have_count(0)
    expect(top.get_by_role("button", name="Finish")).to_have_count(1)
    expect(page.locator("[data-review-heading]")).to_contain_text("New Member")
    page.goto(middle)
    expect(page.locator("[data-review-family]")).to_contain_text(
        "Member of Squyres, Jeff and Tracy"
    )
    expect(page).to_have_title(
        "Review one at a time: Census changes, Family 1 of 1, Member 1 of 2"
    )


def test_phone_rows_stay_one_line(page, component_origin, axe_source):
    """On a phone the three slots stay on one line, so the last page (Finish
    in Next's slot) is no shorter, and the page passes axe."""
    page.set_viewport_size({"width": 320, "height": 640})
    heights = []
    for path in PAGE_PATHS:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        heights.append(page.locator('[data-review-nav="top"]').bounding_box()["height"])
    assert heights[0] == heights[1] == heights[2]
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_my_review_work_card_comes_first_and_continues_a_run(page, component_origin):
    """The Review one at a time card is the first card, ahead of the stream
    panels; with one unfinished run it continues that run."""
    page.goto(component_origin + CARD_PATH)
    card = page.locator("[data-review-wizard-card]")
    link = card.get_by_role("link")
    expect(link).to_have_text("Continue: Census changes, Family 12 of 87")
    expect(link).to_have_attribute(
        "href", "/admin/reports/review/12/?run=00000000-0000-0000-0000-00000000084b"
    )
    first = page.locator("[data-link-card]").first
    expect(first).to_contain_text("Continue: Census changes")
