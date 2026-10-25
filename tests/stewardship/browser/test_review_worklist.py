"""The shared review worklist decides in place (#1681).

The component is served on ``review_worklist_components``' fixture pages;
each POST is answered with the fixture page after the decision, as a
stream's view will answer with its page redrawn. A mark outside every
region shows the page never reloaded. In Chromium and WebKit (WebKit's
click does not focus, so focus is checked only after the script moves it).
"""

import pytest

from .review_worklist_components import (
    ACCEPTED,
    ACCEPTED_NOTICE,
    BULK_NOTICE,
    BULKED,
    CONFIRMED,
    CONFIRMED_NOTICE,
    CONFIRMED_UNDONE,
    FILTERED,
    FILTERED_ADDRESS,
    HUB,
    LEADER,
    PATH,
    REOPENED,
    REOPENED_NOTICE,
    STALE_NOTICE,
    STALED,
    UNDONE,
    UNDONE_NOTICE,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, closes, focused, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

ROWS = "#table tbody tr"


def name(index):
    """How the fixture names row ``index``."""
    return f"Mobile phone for Member {index:02d}"


def row(page, index):
    """Row ``index`` of the table."""
    return page.locator(f"#worklist-row-{index}")


# The row action each test names an icon by. A pressed icon's name says
# what choosing it does (#2131), so the tests find icons by their action.
ACTIONS = {
    "Accept": "accept",
    "Edit": "edit",
    "Ignore": "ignore",
    "Mark": "entered_by_hand",
}


def icon(page, index, label):
    """Row ``index``'s ``label`` icon (Accept, Edit, Ignore or Mark)."""
    return row(page, index).locator(f'button[data-row-action="{ACTIONS[label]}"]')


def boxes(locator):
    """The bounding box of each button under ``locator``, to the pixel, from
    its table's corner, so scrolling the table into view does not count."""
    return locator.locator("button").evaluate_all(
        """nodes => nodes.map(node => {
            const box = node.getBoundingClientRect();
            const table = node.closest("table").getBoundingClientRect();
            return [box.left - table.left, box.top - table.top, box.width,
                    box.height].map(Math.round);
        })"""
    )


def top(locator):
    """A locator's top edge on the page, to the pixel."""
    return locator.evaluate(
        "node => Math.round(node.getBoundingClientRect().top + scrollY)"
    )


def posts_to(page, origin, answer):
    """Answer every POST to the page with the fixture ``answer``; the bodies."""
    sent = []

    def choose(request):
        """Keep the body and answer with the page after the decision."""
        sent.append(request.post_data)
        return answer

    fulfil_post_with(page, "**" + PATH, origin, choose)
    return sent


@pytest.mark.parametrize("width", [320, 1280])
def test_icons_show_the_decision_in_force(page, component_origin, axe_source, width):
    """Each row draws the same icons at every stage, the decision or mark in
    force pressed and filled, the latest one its undo (#2131), and the rest
    unavailable with their reason.
    The filters are always open, Stage first, and the page has no WCAG
    violation at a phone or a desktop width."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    filters = page.locator("#table-filters")
    assert filters.locator("xpath=ancestor::details").count() == 0
    assert filters.locator("label").first.inner_text() == "Stage"
    assert "To review (2)" in filters.get_by_label("Stage").inner_text()
    # To review: the decisions, none pressed; Edit is no toggle.
    accept = icon(page, 0, "Accept")
    assert accept.get_attribute("aria-pressed") == "false"
    assert icon(page, 0, "Edit").get_attribute("aria-pressed") is None
    assert page.locator('[data-row-action="back_to_review"]').count() == 0
    # Ready to update: Accept pressed, and it is the undo; no Back to review
    # icon beside it.
    chosen = icon(page, 2, "Accept")
    assert chosen.get_attribute("aria-pressed") == "true"
    assert chosen.get_attribute("aria-disabled") is None
    undo = f"Accepted; select to send {name(2)} back to review"
    assert chosen.get_attribute("title") == undo
    visible(row(page, 2).get_by_role("button", name=undo, exact=True))
    # Ignore waits for the undo, saying why; choosing it does nothing.
    ignore = icon(page, 2, "Ignore")
    assert ignore.get_attribute("aria-disabled") == "true"
    assert ignore.get_attribute("title") == (
        f"Ignore {name(2)}; send it back to review first"
    )
    sent = []
    page.on("request", lambda r: sent.append(r) if r.method == "POST" else None)
    ignore.click(force=True)
    assert page.locator("dialog[open]").count() == 0 and sent == []
    fill = "node => getComputedStyle(node).backgroundColor"
    assert chosen.evaluate(fill) != accept.evaluate(fill)
    # An edited item's Accept is selected and says so.
    assert (
        icon(page, 3, "Accept").get_attribute("title").startswith("Accepted (edited);")
    )
    assert icon(page, 4, "Ignore").get_attribute("aria-pressed") == "true"
    # Entered by hand: Accept and the mark pressed; the mark is the undo
    # (#1840), its box's one kind, and Accept waits for it; Not seen flagged.
    marked = row(page, 6)
    assert icon(page, 6, "Mark").get_attribute("aria-pressed") == "true"
    assert icon(page, 6, "Mark").get_attribute("aria-disabled") is None
    assert icon(page, 6, "Accept").get_attribute("aria-disabled") == "true"
    assert icon(page, 6, "Accept").get_attribute("title") == (
        "Accepted; undo Entered by hand first"
    )
    box = marked.locator("input[data-select-row]")
    assert box.get_attribute("data-confirm-kind") == "back_to_review"
    visible(marked.locator(".stage-chip.status-warning"))
    # Entered incorrectly: the Error look and ParishSoft now beside it.
    wrong = row(page, 5)
    has_text(wrong.locator(".stage-chip.status-error"), "Entered incorrectly")
    assert "555-0177" in wrong.inner_text()
    assert "555-0177" not in row(page, 0).inner_text()
    # Every row with a bulk action has a box (UX conventions, "Several rows"),
    # the Entered by hand row too since its pressed mark undoes it (#1840).
    assert page.locator("input[data-select-row]").count() == 8
    visible(page.locator(".bulk-bar").get_by_role("button", name="Add"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)


def test_a_row_decision_records_in_place(page, component_origin):
    """Accept asks in the shared dialog naming the row, posts the row's
    fields with the page's view, and the answer redraws the page in place:
    the notice is announced, the row's Accept is now selected and focused,
    and nothing above the table moved."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, ACCEPTED)
    table = page.locator(".table-scroll")
    before = top(table)
    icon(page, 0, "Accept").click()
    dialog = page.locator("#worklist-decide-dialog")
    has_text(dialog.locator("h2"), f"Accept {name(0)}?")
    dialog.get_by_label("Note (optional)").fill("Fine")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#worklist-notice"), ACCEPTED_NOTICE)
    has_text(page.get_by_role("status").filter(has_text="accepted."), ACCEPTED_NOTICE)
    hidden(dialog)
    selected = icon(page, 0, "Accept")
    assert selected.get_attribute("aria-pressed") == "true"
    assert page.evaluate("document.activeElement.dataset.rowAction") == "accept"
    assert page.evaluate("document.activeElement.closest('tr').id") == (
        "worklist-row-0"
    )
    body = sent[-1]
    assert "decide=accept" in body and "item=0" in body and "version=1" in body
    assert "request_key=" in body and "note=Fine" in body and "stage=all" in body
    assert top(table) == before
    assert page.evaluate(MARKED) == "kept"


def test_bulk_buttons_wait_for_rows_that_all_offer_them(page, component_origin):
    """A bulk decision is available only while every ticked row offers it,
    the bar says why otherwise, and the dialog counts the rows it records,
    all in place."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, BULKED)
    bar = page.locator(".bulk-bar")
    accept = bar.get_by_role("button", name="Accept", exact=True)
    assert accept.is_disabled()
    page.get_by_label(f"Select {name(0)}").check()
    page.get_by_label(f"Select {name(1)}").check()
    available(accept)
    # A Ready to update row cannot be accepted again.
    page.get_by_label(f"Select {name(2)}").check()
    assert accept.is_disabled()
    has_text(
        bar.locator("[data-selection-hint]"),
        "Not every ticked row offers that action. Tick changes at the same "
        "stage, or use each row's own icons.",
    )
    page.get_by_label(f"Select {name(2)}").uncheck()
    available(accept)
    accept.click()
    dialog = page.locator("#worklist-bulk-dialog")
    has_text(dialog.locator("h2"), "Accept 2 changes?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#worklist-notice"), BULK_NOTICE)
    body = sent[-1]
    assert "bulk=accept" in body and "bulk_key=" in body
    assert "item=0%3A1" in body and "item=1%3A1" in body and "item=2" not in body
    assert icon(page, 1, "Accept").get_attribute("aria-pressed") == "true"
    assert page.evaluate(MARKED) == "kept"


def test_reopening_an_ignored_item_takes_an_optional_note(page, component_origin):
    """Back to review on an Ignored item (its pressed Ignore chosen again,
    #2131), as on an accepted one, records with no note (#1889). A bulk Back
    to review asks for one only while a ticked row is at a terminal stage,
    not while only an Ignored row is."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, REOPENED)
    dialog = page.locator("#worklist-decide-dialog")
    record = dialog.get_by_role("button", name="Record")
    icon(page, 2, "Accept").click()
    has_text(dialog.locator("h2"), f"Send {name(2)} back to review?")
    available(record)
    # The note's label follows the action in place (R38 finding 3).
    has_text(dialog.locator("label[for$=-note]"), "Note (optional)")
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    icon(page, 4, "Ignore").click()
    has_text(
        dialog.locator("[data-confirm-body]"),
        "The ignored item goes back to To review, to be decided again.",
    )
    has_text(dialog.locator("label[for$=-note]"), "Note (optional)")
    available(record)
    record.click()
    has_text(page.locator("#worklist-notice"), REOPENED_NOTICE)
    assert "decide=back_to_review" in sent[-1] and "note=&" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    # The bulk dialog: an accepted and an Ignored row ticked take an
    # optional note; a terminal row ticked as well makes it required.
    page.goto(component_origin + PATH)
    back = page.locator(".bulk-bar").get_by_role("button", name="Back to review")
    bulk = page.locator("#worklist-bulk-dialog")
    page.get_by_label(f"Select {name(2)}").check()
    page.get_by_label(f"Select {name(4)}").check()
    back.click()
    available(bulk.get_by_role("button", name="Record"))
    has_text(bulk.locator("label[for$=-note]"), "Note (optional)")
    bulk.get_by_role("button", name="Cancel").click()
    hidden(bulk)
    page.get_by_label(f"Select {name(6)}").check()
    back.click()
    assert bulk.get_by_role("button", name="Record").is_disabled()
    has_text(bulk.locator("label[for$=-note]"), "Note (required)")


def test_a_pressed_mark_undoes_with_its_note_in_place(page, component_origin):
    """Choosing the pressed Entered by hand opens Back to review's own
    dialog, which requires a note on a terminal stage (#1840); recording
    posts Back to review, and the row is redrawn in place with the same
    buttons in the same places, the mark now unpressed and focused
    (#2131)."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, UNDONE)
    actions = row(page, 6).locator(".table-actions")
    before = boxes(actions)
    icon(page, 6, "Mark").click()
    dialog = page.locator("#worklist-decide-dialog")
    has_text(dialog.locator("h2"), f"Send {name(6)} back to review?")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Entered by hand is undone: the item goes back to To enter by hand, "
        "with its decision kept. Say why.",
    )
    has_text(dialog.locator("label[for$=-note]"), "Note (required)")
    record = dialog.get_by_role("button", name="Record")
    assert record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Typed in the wrong Member")
    available(record)
    record.click()
    has_text(page.locator("#worklist-notice"), UNDONE_NOTICE)
    hidden(dialog)
    body = sent[-1]
    assert "decide=back_to_review" in body and "item=6" in body
    assert "note=Typed+in+the+wrong+Member" in body
    mark = icon(page, 6, "Mark")
    assert mark.get_attribute("aria-pressed") == "false"
    assert page.evaluate("document.activeElement.dataset.rowAction") == (
        "entered_by_hand"
    )
    assert boxes(actions) == before
    assert page.evaluate(MARKED) == "kept"


def test_a_confirmed_rows_pressed_accept_undoes_in_place(page, component_origin):
    """A change ParishSoft already held has no decision behind it, so its
    Accept is drawn pressed as its undo, with no Back to review button of
    its own (#2131). Choosing it opens Back to review's dialog, which needs
    a note on a terminal stage; the row is redrawn To review with every
    button in its place and Accept unpressed and focused."""
    page.goto(component_origin + CONFIRMED)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, CONFIRMED_UNDONE)
    actions = row(page, 8).locator(".table-actions")
    assert actions.locator('[data-row-action="back_to_review"]').count() == 0
    accept = icon(page, 8, "Accept")
    assert accept.get_attribute("aria-pressed") == "true"
    undo = f"Already in ParishSoft; select to send {name(8)} back to review"
    visible(row(page, 8).get_by_role("button", name=undo, exact=True))
    before = boxes(actions)
    accept.click()
    dialog = page.locator("#worklist-decide-dialog")
    has_text(dialog.locator("h2"), f"Send {name(8)} back to review?")
    dialog.get_by_label("Note (required)").fill("ParishSoft was wrong")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#worklist-notice"), CONFIRMED_NOTICE)
    hidden(dialog)
    assert "decide=back_to_review" in sent[-1] and "item=8" in sent[-1]
    assert icon(page, 8, "Accept").get_attribute("aria-pressed") == "false"
    assert page.evaluate("document.activeElement.dataset.rowAction") == "accept"
    assert boxes(actions) == before
    assert page.evaluate(MARKED) == "kept"


def test_a_decision_keeps_every_button_in_place(page, component_origin):
    """Accept fills its own button and nothing appears or disappears: every
    button of the row keeps its bounding box (UX conventions, "No layout
    shift"; #2131)."""
    page.goto(component_origin + PATH)
    sent = posts_to(page, component_origin, ACCEPTED)
    actions = row(page, 0).locator(".table-actions")
    before = boxes(actions)
    icon(page, 0, "Accept").click()
    page.locator("#worklist-decide-dialog").get_by_role("button", name="Record").click()
    has_text(page.locator("#worklist-notice"), ACCEPTED_NOTICE)
    assert "decide=accept" in sent[-1]
    assert icon(page, 0, "Accept").get_attribute("aria-pressed") == "true"
    assert boxes(actions) == before


@pytest.mark.parametrize("key", ["Space", "Enter"])
def test_space_and_enter_press_and_unpress(page, component_origin, key):
    """From the keyboard, Space and Enter choose a decision and choose a
    pressed one again, each opening its own dialog; an unavailable button
    opens nothing.

    Each Escape waits for the dialog's close event, where the page returns
    focus to the opener, before the test moves focus on (#2160)."""
    page.goto(component_origin + PATH)
    dialog = page.locator("#worklist-decide-dialog")
    icon(page, 0, "Accept").focus()
    page.keyboard.press(key)
    has_text(dialog.locator("h2"), f"Accept {name(0)}?")
    closes(dialog, lambda: page.keyboard.press("Escape"))
    focused(icon(page, 0, "Accept"))
    icon(page, 2, "Accept").focus()
    page.keyboard.press(key)
    has_text(dialog.locator("h2"), f"Send {name(2)} back to review?")
    closes(dialog, lambda: page.keyboard.press("Escape"))
    focused(icon(page, 2, "Accept"))
    icon(page, 2, "Ignore").focus()
    page.keyboard.press(key)
    assert page.locator("dialog[open]").count() == 0


def test_edit_and_add_open_the_value_editor(page, component_origin):
    """Edit opens the row's value editor holding the value carried forward,
    and Add the stream's own; each posts its decision and value."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, ACCEPTED)
    icon(page, 0, "Edit").click()
    editor = page.locator("#worklist-edit-dialog-0")
    has_text(editor.locator("h2"), f"Edit {name(0)}")
    value = editor.get_by_label("Reviewer's value")
    assert value.input_value() == "555-0000"
    # Save waits until the value changes, saying why (R38 finding 4): the
    # same value would record only a plain Accept.
    save = editor.get_by_role("button", name="Save")
    assert save.is_disabled()
    has_text(
        editor.locator("[data-complete-hint]"),
        "Change the value to save it; Accept keeps it as it is.",
    )
    value.fill("555-0199")
    available(save)
    save.click()
    has_text(page.locator("#worklist-notice"), ACCEPTED_NOTICE)
    assert "decide=edit" in sent[-1] and "value=555-0199" in sent[-1]
    # The edit is drawn on the row's Accept, which takes focus.
    assert page.evaluate("document.activeElement.dataset.rowAction") == "accept"
    assert page.evaluate("document.activeElement.getAttribute('aria-pressed')") == (
        "true"
    )
    page.locator(".bulk-bar").get_by_role("button", name="Add").click()
    add = page.locator("#worklist-add-dialog")
    confirm = add.get_by_role("button", name="Add")
    assert confirm.is_disabled()
    add.get_by_label("Change to add").fill("Join Adult Choir")
    available(confirm)
    confirm.click()
    hidden(add)
    assert "decide=add" in sent[-1] and "value=Join+Adult+Choir" in sent[-1]
    assert "request_key=" in sent[-1] and "item=" not in sent[-1]
    assert page.evaluate(MARKED) == "kept"


def test_a_leader_sees_only_their_actions(page, component_origin):
    """A Ministry leader decides requests for Ministries they lead, never
    marks Entered by hand or Adds, and has no box on another Ministry's."""
    page.goto(component_origin + LEADER)
    assert [
        node.get_attribute("data-row-action")
        for node in row(page, 0).locator(".table-actions button").all()
    ] == ["accept", "edit", "ignore"]
    assert row(page, 1).locator(".table-actions button").count() == 0
    assert row(page, 1).locator("input[data-select-row]").count() == 0
    # Accepted: the same icons, Accept pressed as the undo (#2131).
    assert [
        node.get_attribute("data-row-action")
        for node in row(page, 2).locator(".table-actions button").all()
    ] == ["accept", "edit", "ignore"]
    assert icon(page, 2, "Accept").get_attribute("data-row-undo") is not None
    bar = page.locator(".bulk-bar")
    assert [node.inner_text() for node in bar.locator("[data-bulk-action]").all()] == [
        "Accept",
        "Ignore",
        "Back to review",
    ]
    assert bar.get_by_role("button", name="Add").count() == 0


@pytest.mark.parametrize("width", [390, 700, 1024, 1280])
def test_the_notice_line_sits_under_the_filters(page, component_origin, width):
    """The answer to the reader's own decision sits between the filters and
    the table on one reserved line, the same height empty or filled, so the
    table never moves; nothing reserves room above the filters (R38 finding
    10)."""
    page.set_viewport_size({"width": width, "height": 900})
    measure = """() => {
        const box = (selector) =>
            document.querySelector(selector).getBoundingClientRect();
        const heading = box("main h1"), form = box("#table-filters");
        const notice = box("#worklist-notice"), table = box("#table");
        return {gap: form.top - heading.bottom, formBottom: form.bottom,
                top: notice.top, height: notice.height, table: table.top};
    }"""
    page.goto(component_origin + PATH)
    empty = page.evaluate(measure)
    for path, notice in ((ACCEPTED, ACCEPTED_NOTICE), (STALED, STALE_NOTICE)):
        page.goto(component_origin + path)
        has_text(page.locator("#worklist-notice"), notice)
        filled = page.evaluate(measure)
        for shown in (empty, filled):
            assert shown["formBottom"] <= shown["top"] < shown["table"]
            assert shown["gap"] <= 40
        assert filled["height"] == empty["height"], notice
        assert filled["table"] == empty["table"], notice


def test_a_reload_keeps_the_applied_filters(page, component_origin):
    """Apply puts the view's own address in the address bar, so a reload
    draws the same view; the plain address stays the default view (R38
    finding 6)."""
    page.goto(component_origin + PATH)
    sent = posts_to(page, component_origin, FILTERED)
    page.select_option("#worklist-stage", "ignored")
    page.get_by_role("button", name="Apply filters").click()
    page.wait_for_url(component_origin + FILTERED_ADDRESS)
    assert "stage=ignored" in sent[-1]
    page.reload()
    assert page.url == component_origin + FILTERED_ADDRESS
    assert page.locator("#worklist-stage").input_value() == "ignored"
    assert [node.get_attribute("id") for node in page.locator(ROWS).all()] == [
        "worklist-row-4"
    ]


@pytest.mark.parametrize(
    ("path", "hub"), [(PATH, True), (LEADER, False)], ids=["staff", "leader"]
)
def test_names_open_the_hub_and_items_their_page(page, component_origin, path, hub):
    """A Member's name opens the Family hub for Staff and Administrators
    and is plain text for a Ministry leader, who may not open it; the
    item's cell opens its own page for every viewer (R38 finding 13)."""
    page.goto(component_origin + path)
    first = row(page, 0)
    name = first.locator("th[scope=row]")
    if hub:
        assert name.locator("a").get_attribute("href") == HUB
    else:
        assert name.locator("a").count() == 0 and name.inner_text() == "Member 00"
    item = first.locator("td a")
    assert item.count() == 1 and item.get_attribute("href") == "/review-item/0"
