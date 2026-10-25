"""Review one at a time's start page, run pages and card (#2123)."""

import pytest

from parishkit.stewardship.reports.review_wizard_views import page_url

from .review_wizard_components import (
    ACCEPTED_PATH,
    BACK_PATH,
    DECEASED,
    FOCUS_PATH,
    GREYED_CARD_PATH,
    GREYED_REASON,
    MEMBER_ACCEPTED_PATH,
    NO_CARD_PATH,
    PAGE_PATHS,
    PLEDGE_EDITED_PATH,
    PLEDGE_PATH,
    RUN,
    START_PATH,
)
from .review_work_components import HOME_PATH, PATH
from .test_charts import assert_clean, watch
from .test_family_response import expect
from .test_table_sorting import MARK, MARKED
from .waits import focused, has_attribute, has_text, hidden, visible

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
# Each row's Finish button box, to compare across pages (R52 Part W).
FINISH = """() => Object.fromEntries([...document.querySelectorAll(
  '[data-review-nav] [data-review-move="finish"]')].map(button => {
    const box = button.getBoundingClientRect();
    return [button.closest('[data-review-nav]').dataset.reviewNav,
      [Math.round(box.x), Math.round(box.y), Math.round(box.width),
       Math.round(box.height)]];
  }))"""
# The Review work split's parts, each its box as {x, y, width, height,
# bottom, right}, or null when it is not drawn (review-work-split.html).
SPLIT = """() => Object.fromEntries(Object.entries({
  split: '.review-work-split', pane: '[data-review-wizard-card]',
  text: '.review-work-wizard-text',
  action: '[data-review-wizard-card] > .button, [data-review-wizard-reason]',
  or: '.review-work-or', tables: '[data-review-work-tables]',
  heading: '#review-work-heading',
}).map(([name, selector]) => {
  const el = document.querySelector(selector);
  if (!el) return [name, null];
  const {x, y, width, height, bottom, right} = el.getBoundingClientRect();
  return [name, {x, y, width, height, bottom, right}];
}))"""


def assert_side_by_side(parts):
    """The pane, the "or" rule and the tables sit in one row, top-aligned and
    of one height; the pane is 13rem wide, its text at its top and its button
    (or reason) centred in the height left below the text."""
    pane, rule, tables, text, action = (
        parts[name] for name in ("pane", "or", "tables", "text", "action")
    )
    assert pane["right"] < rule["x"] < rule["right"] < tables["x"]
    assert pane["y"] == rule["y"] == tables["y"]
    assert pane["height"] == rule["height"] == tables["height"]
    assert round(pane["width"]) == 13 * 16
    # 1px border and 1rem padding: the label and help sit at the top.
    assert round(text["y"] - pane["y"]) == 17
    above = action["y"] - text["bottom"]
    below = pane["bottom"] - 17 - action["bottom"]
    assert above > 100
    assert abs(above - below) <= 1.5


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
    shown ones are sent; Basic's search says what it matches, per stream; a
    greyed stream says why; no runs are listed (R52 Part W); nothing scrolls
    sideways on a phone."""
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + START_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    expect(page.get_by_role("heading", level=1)).to_have_text("Review one at a time")
    expect(page.get_by_role("table")).to_have_count(0)
    expect(page.get_by_text("Unfinished reviews")).to_have_count(0)
    expect(page.locator("#review-sort option").first).to_have_text("Last name (A–Z)")
    # Basic's help, under the search and naming it, is the stream's own.
    search = page.locator("#census-basic-search")
    expect(search).to_have_accessible_description(
        "Family name, a Member's name, or the Family DUID. Leave blank for every "
        "waiting change."
    )
    pledges = page.get_by_role("radio", name="Pledges")
    expect(pledges).to_be_disabled()
    expect(page.get_by_text("Pledges: This campaign does not include")).to_be_visible()
    # The wizard's own stream order: Additional information last (R52 Part W2).
    assert page.locator("#review-stream label").all_text_contents() == [
        "Census changes",
        "Ministry requests",
        "Pledges",
        "Limitations",
        "Additional information",
    ]
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
    page.get_by_role("radio", name="Basic").check()
    expect(page.locator("#ministry-basic-search-help")).to_have_text(
        "Member's name or the Ministry. Leave blank for every waiting request."
    )
    expect(page.locator("#census-basic-search-help")).to_be_hidden()


@pytest.mark.parametrize("width", [320, 1366])
def test_the_navigation_rows_keep_their_place_on_every_page(
    page, component_origin, axe_source, width
):
    """Previous, Finish and Next sit in the same spots on a Family page, a
    Member page and the last page (a New Member's), whose sections differ in
    height, in both rows, on a phone and a desktop; page 1 keeps Previous's
    slot empty, the last page keeps Next's empty and Finish stays in the
    middle, at the same box on all three pages (R52 Part W); the bottom row is at
    the bottom of the screen; every page passes axe and scrolls no wider
    than the screen."""
    page.set_viewport_size({"width": width, "height": 700})
    found, heights, finishes = [], set(), []
    for path in PAGE_PATHS:
        page.goto(component_origin + path)
        found.append(page.evaluate(SLOTS))
        finishes.append(page.evaluate(FINISH))
        heights.add(page.locator("#review-section").bounding_box()["height"])
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == [], path
    # The pages' sections differ in height, so equal spots mean something.
    assert len(heights) == len(PAGE_PATHS), heights

    # Finish: one button per row, at the same box on the first, a middle
    # and the last page, and centred in its row.
    assert all(sorted(f) == ["bottom", "top"] for f in finishes), finishes
    assert finishes[0] == finishes[1] == finishes[2], finishes
    for row in ("top", "bottom"):
        bar = page.locator(f'[data-review-nav="{row}"] .review-wizard-actions')
        left = bar.bounding_box()
        x, _y, w, _h = finishes[2][row]
        assert abs((x + w / 2) - (left["x"] + left["width"] / 2)) <= 1, row

    # Every control's spot, wherever its slot shows one: Finish on every
    # page, Next on the first two, Previous on the last two.
    def spot(slots, name):
        """A slot's (x, y), ignoring its width (an empty slot has none)."""
        return slots[name][:2]

    for row in ("top", "bottom"):
        # The Next slot is right-aligned: Next ends at the same spot.
        ends = {
            (f[f"{row}:next"][0] + f[f"{row}:next"][2], f[f"{row}:next"][1])
            for f in found[:2]
        }
        assert len(ends) == 1, (row, ends)
        assert len({tuple(spot(f, f"{row}:action")) for f in found}) == 1, row
        assert spot(found[1], f"{row}:back") == spot(found[2], f"{row}:back")
    first, middle, last = (component_origin + path for path in PAGE_PATHS)
    page.goto(first)
    top = page.locator('[data-review-nav="top"]')
    expect(top.get_by_role("button", name="Previous")).to_have_count(0)
    expect(top.get_by_role("button", name="Finish")).to_be_visible()
    expect(top.get_by_role("button", name="Next")).to_be_visible()
    expect(top.locator("[data-review-progress]")).to_have_text("Family 1 of 1")
    # The header: the Family's line, the Member line's place kept empty.
    heading = page.locator("[data-review-heading]")
    expect(heading).to_have_text(
        "Family: Squyres, Jeff and Tracy (DUID 4123, envelope 312)"
    )
    # (The fixture has no signed-in request, so its Family name is plain
    # text; the database tests check the hub link.)
    expect(page.locator("[data-review-member]")).to_have_text("")
    bottom = page.locator('[data-review-nav="bottom"]').bounding_box()
    assert abs(bottom["y"] + bottom["height"] - 700) <= 1
    page.goto(last)
    top = page.locator('[data-review-nav="top"]')
    expect(top.get_by_role("button", name="Next")).to_have_count(0)
    expect(top.get_by_role("button", name="Finish")).to_have_count(1)
    # Finish stays in the middle slot; Next's slot is kept, empty.
    expect(
        top.locator('[data-nav-slot="action"] [data-review-move="finish"]')
    ).to_have_count(1)
    expect(top.locator('[data-nav-slot="next"]')).to_be_empty()
    member = page.locator("[data-review-member]")
    expect(member).to_contain_text("New Member")
    expect(member).not_to_contain_text("DUID")
    expect(page.locator("#review-section")).to_contain_text("Adding this Member:")
    page.goto(middle)
    member = page.locator("[data-review-member]")
    expect(member).to_have_text("Member: Squyres, Jeff (DUID 77001)")
    # No envelope: the parenthetical names the DUID alone.
    expect(page.locator("[data-review-heading]")).to_have_text(
        "Family: Squyres, Jeff and Tracy (DUID 4123)"
    )
    expect(member.get_by_role("link", name="Squyres, Jeff")).to_have_attribute(
        "href",
        "/admin/reports/families/00000000-0000-0000-0000-000000000007/?tab=member-2",
    )
    expect(page.locator("[data-review-heading]")).to_contain_text("Family: ")
    # Only the Family's place: no "Member 1 of 2" suffix (R52 Part W).
    expect(page).to_have_title("Review one at a time: Census changes, Family 1 of 1")


def test_phone_rows_stay_one_line(page, component_origin, axe_source):
    """On a phone the three slots stay on one line, so the last page (Finish
    in the middle, Next's slot empty) is no shorter, and the page passes axe."""
    page.set_viewport_size({"width": 320, "height": 640})
    heights = []
    for path in PAGE_PATHS:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        heights.append(page.locator('[data-review-nav="top"]').bounding_box()["height"])
    assert heights[0] == heights[1] == heights[2]
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


@pytest.mark.parametrize("path", [HOME_PATH, PATH])
def test_review_work_puts_one_at_a_time_beside_the_tables(
    page, component_origin, axe_source, path
):
    """At 1280px with the menu open (R52 Part W), Home's Review work panel and
    My review work put the One at a time pane, then "or", then the In bulk
    stream groups side by side, below the heading with a gap; the pane's
    button is the portal's primary button, centred below its text; the
    "or" is hidden from screen readers; the page passes axe."""
    page.set_viewport_size({"width": 1280, "height": 768})
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    parts = page.evaluate(SPLIT)
    assert_side_by_side(parts)
    if parts["heading"]:
        # Home: the row starts below the Review work heading, with a gap.
        assert parts["pane"]["y"] - parts["heading"]["bottom"] >= 8
    pane = page.locator("[data-review-wizard-card]")
    expect(pane).to_contain_text("One at a time")
    expect(pane.locator(".review-work-wizard-help")).to_have_text(
        "Every stream's waiting items, one Family or Member per page."
    )
    link = pane.get_by_role("link", name="Review one at a time")
    expect(link).to_have_class("button")
    expect(link).to_have_attribute("href", "/admin/reports/review/")
    expect(page.locator(".review-work-or")).to_have_attribute("aria-hidden", "true")
    expect(page.locator("[data-review-work-tables]")).to_contain_text("In bulk")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


@pytest.mark.parametrize("path", [HOME_PATH, PATH, GREYED_CARD_PATH])
def test_review_work_stacks_one_at_a_time_on_a_phone(
    page, component_origin, axe_source, path
):
    """At 320px the pane is on top at full width with its button (or reason)
    after its text, then a horizontal "or" rule, then the tables; nothing
    scrolls sideways."""
    page.set_viewport_size({"width": 320, "height": 640})
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    parts = page.evaluate(SPLIT)
    pane, rule, tables = parts["pane"], parts["or"], parts["tables"]
    assert pane["bottom"] < rule["y"] < rule["bottom"] < tables["y"]
    assert pane["width"] == rule["width"] == tables["width"] == parts["split"]["width"]
    assert rule["height"] < 30
    assert parts["action"]["y"] >= parts["text"]["bottom"]
    assert parts["action"]["right"] <= pane["right"]
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_a_greyed_pane_shows_the_menus_reason_not_a_link(
    page, component_origin, axe_source
):
    """While the menu greys the wizard out, the pane shows the menu's reason
    as muted text where the button would be (centred the same way), never a
    link."""
    page.set_viewport_size({"width": 1280, "height": 768})
    page.goto(component_origin + GREYED_CARD_PATH)
    page.evaluate("document.fonts.ready.then(() => true)")
    assert_side_by_side(page.evaluate(SPLIT))
    pane = page.locator("[data-review-wizard-card]")
    expect(pane.get_by_role("link")).to_have_count(0)
    reason = pane.locator("[data-review-wizard-reason]")
    expect(reason).to_have_text(GREYED_REASON)
    assert reason.evaluate("el => getComputedStyle(el).color") == page.evaluate(
        "getComputedStyle(document.querySelector('.review-work-wizard-help')).color"
    )
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


@pytest.mark.parametrize("width", [320, 1280])
def test_without_the_wizard_the_tables_take_the_full_width(
    page, component_origin, width
):
    """Without the menu's wizard entry there is no pane, no "or" and no By
    table label: the stream panels take the full width."""
    page.set_viewport_size({"width": width, "height": 768})
    page.goto(component_origin + NO_CARD_PATH)
    parts = page.evaluate(SPLIT)
    assert parts["pane"] is None and parts["or"] is None
    assert parts["tables"]["width"] == parts["split"]["width"]
    expect(page.locator("[data-review-work-tables]")).not_to_contain_text("In bulk")
    expect(page.locator("[data-review-stream]")).to_have_count(5)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_accept_on_a_family_page_redraws_its_section_in_place(page, component_origin):
    """The Family page's section is the hub's: Accept opens Census changes'
    own dialog and posts there, the section is redrawn in place from the
    page's own address with Accept pressed and focused, and neither
    navigation row moves; nothing reloads."""
    errors = watch(page)
    page.set_viewport_size({"width": 1024, "height": 700})
    page.goto(component_origin + PAGE_PATHS[0])
    page.evaluate(MARK)
    sent = []

    def census(route):
        """Keep the decision's body; answer as Census changes does."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(route.request.post_data)
        route.fulfill(
            content_type="text/html",
            body="<!doctype html><p data-confirm-announce>Accepted.</p>",
        )

    def here(route):
        """The page's own address after the decision: the accepted page."""
        route.fulfill(response=route.fetch(url=component_origin + ACCEPTED_PATH))

    page.route("**/admin/reports/census/", census)
    page.route("**" + page_url(RUN, 1), here)
    section = page.locator("#review-section")
    expect(section.get_by_text("1 more in this stream for this Family")).to_be_visible()
    before = page.evaluate(SLOTS)
    row = section.get_by_role("table", name="Changed fields").get_by_role(
        "row", name="Home address"
    )
    accept = row.get_by_role("button", name="Accept Home address for Family")
    has_attribute(accept, "aria-pressed", "false")
    accept.click()
    dialog = page.locator("#review-family-census-decide-dialog")
    visible(dialog)
    has_text(dialog.locator("h2"), "Accept Home address for Family?")
    dialog.get_by_role("button", name="Record").click()
    hidden(dialog)
    row = section.get_by_role("table", name="Changed fields").get_by_role(
        "row", name="Home address"
    )
    has_text(row.locator(".stage-chip .status-word"), "To enter by hand")
    accept = row.locator("[data-row-action='accept']")
    has_attribute(accept, "aria-pressed", "true")
    focused(accept)
    assert "decide=accept" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    assert page.url.endswith(PAGE_PATHS[0])
    assert page.evaluate(SLOTS) == before
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1366])
def test_accept_on_a_member_page_keeps_both_rows_in_place(
    page, component_origin, width
):
    """A Member page's section is the hub Member tab's: Accept opens Census
    changes' own dialog and posts there, the section is redrawn in place
    from the page's own address with Accept pressed and focused, and
    neither navigation row moves nor the page scrolls, on a page taller
    than the screen, scrolled to the change."""
    errors = watch(page)
    page.set_viewport_size({"width": width, "height": 600})
    page.goto(component_origin + PAGE_PATHS[1])
    assert page.evaluate("document.documentElement.scrollHeight > innerHeight")
    page.evaluate(MARK)
    sent = []

    def census(route):
        """Keep the decision's body; answer as Census changes does."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(route.request.post_data)
        route.fulfill(
            content_type="text/html",
            body="<!doctype html><p data-confirm-announce>Accepted.</p>",
        )

    def here(route):
        """The page's own address after the decision: the accepted page."""
        route.fulfill(response=route.fetch(url=component_origin + MEMBER_ACCEPTED_PATH))

    page.route("**/admin/reports/census/", census)
    page.route("**" + page_url(RUN, 2), here)
    section = page.locator("#review-section")
    row = section.locator(f"#review-member-census-row-{DECEASED}")
    row.scroll_into_view_if_needed()
    scrolled = page.evaluate("scrollY")
    before = page.evaluate(SLOTS)
    accept = row.locator("[data-row-action='accept']")
    has_attribute(accept, "aria-pressed", "false")
    accept.click()
    dialog = page.locator("#review-member-census-decide-dialog")
    visible(dialog)
    dialog.get_by_role("button", name="Record").click()
    hidden(dialog)
    accept = section.locator(
        f"#review-member-census-row-{DECEASED} [data-row-action='accept']"
    )
    has_attribute(accept, "aria-pressed", "true")
    focused(accept)
    assert "decide=accept" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    assert page.url.endswith(PAGE_PATHS[1])
    assert page.evaluate("scrollY") == scrolled
    assert page.evaluate(SLOTS) == before
    assert_clean(page, errors)


@pytest.mark.parametrize("width", [320, 1366])
@pytest.mark.parametrize(
    ("path", "row", "name"),
    [(FOCUS_PATH, "bottom", "Next"), (BACK_PATH, "top", "Previous")],
)
def test_a_move_puts_focus_on_the_control_used(
    page, component_origin, path, row, name, width
):
    """The page Next or Previous opens puts focus on that control, in the
    row it was used in, so Enter moves on again; the page opens at its top,
    with the control on screen and both rows where an unfocused page has
    them (an autofocus would scroll a phone's page to the bottom)."""
    page.set_viewport_size({"width": width, "height": 700})
    page.goto(component_origin + PAGE_PATHS[1])
    unfocused = page.evaluate(SLOTS)
    page.goto(component_origin + path)
    control = page.locator(f'[data-review-nav="{row}"]').get_by_role(
        "button", name=name
    )
    focused(control)
    assert page.evaluate("scrollY") == 0
    box = control.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 700
    assert page.evaluate(SLOTS) == unfocused


def test_phone_rows_never_overlap_with_larger_text(page, component_origin):
    """With text a quarter larger on a 320 px phone, Previous, Finish and
    Next stay side by side: the side columns never shrink below their
    labels, so the buttons never overlap."""
    page.set_viewport_size({"width": 320, "height": 700})
    page.goto(component_origin + PAGE_PATHS[1])
    page.evaluate("document.documentElement.style.fontSize = '125%'")
    for row in ("top", "bottom"):
        boxes = [
            page.locator(
                f'[data-review-nav="{row}"] [data-review-move="{move}"]'
            ).bounding_box()
            for move in ("previous", "finish", "next")
        ]
        for left, right in zip(boxes, boxes[1:], strict=False):
            assert left["x"] + left["width"] <= right["x"], (row, boxes)


PLEDGES = "**/admin/reports/financial/pledges/"


def pledge_answers(page, origin, redrawn):
    """Answer a POST to Pledges as its page does (a notice), and the page's
    own address, which the section redraws from, with ``redrawn``. Returns
    the POST bodies sent."""
    sent = []

    def pledges(route):
        """Keep the decision's body; answer as Pledges does, with a notice."""
        sent.append(route.request.post_data)
        route.fulfill(
            content_type="text/html",
            body="<!doctype html><p data-confirm-announce>Pledge recorded.</p>",
        )

    page.route(PLEDGES, pledges)
    page.route(
        "**" + page_url(RUN, 1),
        lambda route: route.fulfill(response=route.fetch(url=origin + redrawn)),
    )
    return sent


def pledge_editor(page):
    """Open the pledge's Edit and return its dialog."""
    section = page.locator("#review-section")
    section.get_by_role("table", name="The Family's pledge").locator(
        "[data-row-action='edit']"
    ).click()
    editor = page.locator("dialog[open]")
    visible(editor)
    return editor


@pytest.mark.parametrize("redrawn", [PLEDGE_EDITED_PATH, PLEDGE_PATH])
def test_a_saved_pledge_edit_says_nothing_was_lost(page, component_origin, redrawn):
    """A Pledges page's Edit: Save posts what was typed and the section
    redraws in place: no "Changes you typed ... were not kept" line, since
    what was typed was saved (#2123, R52 Part W). Redrawn edited, the pledge
    has no Edit form any more (regression cover); redrawn still To review
    (a read that does not show the edit yet), its Edit form is drawn anew,
    the case the line was wrongly shown for."""
    errors = watch(page)
    page.goto(component_origin + PLEDGE_PATH)
    page.evaluate(MARK)
    sent = pledge_answers(page, component_origin, redrawn)
    editor = pledge_editor(page)
    editor.get_by_label("Annual pledge in dollars").fill("1,300")
    editor.get_by_label("Frequency").select_option("monthly")
    editor.get_by_role("button", name="Save").click()
    hidden(editor)
    has_text(
        page.locator("main > div[role=status].visually-hidden"), "Pledge recorded."
    )
    assert "decide=edit" in sent[-1] and "value_amount=1%2C300" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    assert page.locator("[data-edits-lost]").count() == 0
    assert_clean(page, errors)


def test_unsaved_edit_typing_is_still_noted_when_redrawn(page, component_origin):
    """What was typed into Edit and not saved is still noted as lost when
    another decision redraws the section and draws the editor anew: only
    the applied dialog's own form forgets its typing."""
    page.goto(component_origin + PLEDGE_PATH)
    sent = pledge_answers(page, component_origin, PLEDGE_PATH)
    editor = pledge_editor(page)
    editor.get_by_label("Annual pledge in dollars").fill("1,300")
    editor.get_by_role("button", name="Cancel").click()
    hidden(editor)
    page.locator("#review-section").get_by_role(
        "table", name="The Family's pledge"
    ).locator("[data-row-action='accept']").click()
    dialog = page.locator("dialog[open]")
    visible(dialog)
    dialog.get_by_role("button", name="Record").click()
    hidden(dialog)
    assert "decide=accept" in sent[-1]
    visible(page.locator("[data-edits-lost]"))
