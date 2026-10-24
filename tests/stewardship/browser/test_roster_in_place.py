"""Roster entry on Ministry requests, in place (#528 step 4, R39 finding 2).

Roster changes to enter was folded into Ministry requests: Staff mark the
roster changes at its Stage To enter by hand, served by
``followup_components`` with the real worklist context; each POST is
answered with the fixture page after the decision, as the view answers with
its page redrawn. A mark outside every region shows the page never
reloaded. In Chromium and WebKit. Clearing a mark on the request page is
Back to review of Entered by hand (#1840): its dialog needs the note.
"""

import pytest

from .followup_components import (
    ENTRY,
    ENTRY_BULK_NOTICE,
    ENTRY_BULKED,
    ENTRY_IDS,
    ENTRY_MARKED,
    ENTRY_NOTICE,
    REQUESTS,
    TICK_ITEM,
    TICKED,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
ACCEPTED, DECEASED, REPLACED = ENTRY_IDS


def row(page, request_id):
    """The worklist row of one request."""
    return page.locator(f"#requests-row-{request_id}")


def actions(page, request_id):
    """The decisions the row's available icons record, in order; a pressed
    one records Back to review (#2131)."""
    return [
        "back_to_review"
        if node.get_attribute("data-row-undo") is not None
        else node.get_attribute("data-row-action")
        for node in row(page, request_id)
        .locator(".table-actions button:not([aria-disabled=true])")
        .all()
    ]


def posts_to(page, origin, answer):
    """Answer every POST to the page with the fixture ``answer``; the bodies."""
    sent = []

    def choose(request):
        """Keep the body and answer with the page after the decision."""
        sent.append(request.post_data)
        return answer

    fulfil_post_with(page, "**" + REQUESTS, origin, choose)
    return sent


@pytest.mark.parametrize("width", [320, 1280])
def test_to_enter_by_hand_lists_the_roster_rows(
    page, component_origin, axe_source, width
):
    """At To enter by hand an accepted request offers Back to review and
    Entered by hand; a Member no longer in ParishSoft's, and one a later
    response replaced (which says so), offer Entered by hand alone; the bulk
    bar offers Entered by hand. No WCAG violation and no sideways scroll at a
    phone or a desktop width."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + ENTRY)
    assert page.get_by_label("Stage").input_value() == "to_enter_by_hand"
    assert actions(page, ACCEPTED) == ["back_to_review", "entered_by_hand"]
    assert actions(page, DECEASED) == ["entered_by_hand"]
    assert actions(page, REPLACED) == ["entered_by_hand"]
    assert "Replaced by a later response" in row(page, REPLACED).inner_text()
    bar = page.locator(".bulk-bar")
    assert "Entered by hand" in [
        node.inner_text() for node in bar.locator("[data-bulk-action]").all()
    ]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)


def test_entered_by_hand_asks_with_no_note_and_records_in_place(page, component_origin):
    """A row's Entered by hand asks in the shared dialog naming the row with
    no note to fill, posts an empty note with the view, and the answer
    redraws the page in place; Back to review's dialog still has its note."""
    page.goto(component_origin + ENTRY)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, ENTRY_MARKED)
    dialog = page.locator("#requests-decide-dialog")
    row(page, ACCEPTED).locator("[data-row-undo]").click()
    visible(dialog.get_by_label("Note (optional)"))
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    row(page, ACCEPTED).locator("[data-row-action=entered_by_hand]").click()
    has_text(
        dialog.locator("h2"),
        "Mark Accepted <Member> — Example <Ministry> entered by hand?",
    )
    hidden(dialog.locator("[data-note-field]"))
    record = dialog.get_by_role("button", name="Record")
    available(record)
    record.click()
    has_text(page.locator("#requests-notice"), ENTRY_NOTICE)
    hidden(dialog)
    body = sent[-1]
    assert "decide=entered_by_hand" in body and f"item={ACCEPTED}" in body
    assert "note=&" in body and "stage=to_enter_by_hand" in body
    assert page.evaluate(MARKED) == "kept"


def test_bulk_entered_by_hand_asks_with_no_note(page, component_origin):
    """The bulk bar's Entered by hand covers ticked roster rows of either
    kind, asks naming the count with no note, and records in place."""
    page.goto(component_origin + ENTRY)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, ENTRY_BULKED)
    button = page.locator(".bulk-bar").get_by_role("button", name="Entered by hand")
    row(page, ACCEPTED).locator("input[data-select-row]").check()
    row(page, REPLACED).locator("input[data-select-row]").check()
    available(button)
    button.click()
    dialog = page.locator("#requests-bulk-dialog")
    has_text(dialog.locator("h2"), "Mark 2 requests entered by hand?")
    hidden(dialog.locator("[data-note-field]"))
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#requests-notice"), ENTRY_BULK_NOTICE)
    body = sent[-1]
    assert "bulk=entered_by_hand" in body and "bulk_note=" in body
    assert f"item={ACCEPTED}" in body and f"item={REPLACED}" in body
    assert page.evaluate(MARKED) == "kept"


def test_the_request_page_ticks_in_place(page, component_origin):
    """The tick on a resolved request's own page is one Entered by hand
    toggle (#2131): it answers in place, pressed and in the same place, and
    is where a mistaken mark is cleared."""
    page.goto(component_origin + TICK_ITEM)
    page.evaluate(MARK)
    visible(
        page.locator("#followup-roster").get_by_text("To enter by hand", exact=True)
    )
    # Its accessible name says what choosing it does, so it changes with
    # the press (#2131); the button itself stays put.
    tick = page.locator("#followup-roster button.toggle-button")
    assert tick.get_attribute("aria-label") == "Mark this request entered by hand"
    assert tick.get_attribute("aria-pressed") == "false"
    left = tick.evaluate("node => Math.round(node.getBoundingClientRect().left)")
    tick.click()
    visible(page.get_by_text("Entered by hand by staff@example.org"))
    has_attribute(tick, "aria-pressed", "true")
    assert tick.evaluate("node => Math.round(node.getBoundingClientRect().left)") == (
        left
    )
    assert page.evaluate(MARKED) == "kept"


def test_the_request_page_clear_asks_for_the_note(page, component_origin):
    """The pressed Entered by hand toggle is Back to review (#1893: the
    worklist's dialog; #2131: no button of its own), which opens
    the shared dialog, which says the mark is undone and waits for the
    required note; Record posts it with the clear, and the page is redrawn
    in place, not yet entered."""
    page.goto(component_origin + TICKED)
    page.evaluate(MARK)
    sent = []

    def answer(route):
        """Keep the clear's body; afterwards the page reads as cleared."""
        if route.request.method == "POST":
            sent.append(route.request.post_data)
            route.fulfill(
                response=route.fetch(url=component_origin + TICK_ITEM, method="GET")
            )
        elif sent:
            route.fulfill(
                response=route.fetch(url=component_origin + TICK_ITEM, method="GET")
            )
        else:
            route.continue_()

    page.route(lambda url: TICK_ITEM in url, answer)
    # Its accessible name and tooltip say choosing it again undoes the mark.
    title = (
        "Entered by hand; select to undo Entered by hand for Example <Member> — "
        "Example <Ministry>"
    )
    undo = page.locator("#followup-roster").get_by_role(
        "button", name=title, exact=True
    )
    assert undo.get_attribute("title") == title
    assert undo.get_attribute("aria-pressed") == "true"
    undo.click()
    dialog = page.locator("#followup-roster-clear-dialog")
    visible(dialog)
    has_text(
        dialog.locator("h2"),
        "Send Example <Member> — Example <Ministry> back to review?",
    )
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Entered by hand is undone: the item goes back to To enter by hand, "
        "with its decision kept. Say why.",
    )
    record = dialog.get_by_role("button", name="Record")
    assert record.is_disabled()
    has_text(dialog.locator("[data-complete-hint]"), "Say why the mark is cleared.")
    dialog.get_by_label("Note (required)").fill("Marked the wrong request")
    available(record)
    record.click()
    hidden(dialog)
    visible(
        page.locator("#followup-roster").get_by_text("To enter by hand", exact=True)
    )
    body = sent[-1]
    assert "entered=no" in body and "sequence=2" in body
    assert "note=Marked+the+wrong+request" in body
    assert page.evaluate(MARKED) == "kept"
