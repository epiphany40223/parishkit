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
    FILTERED,
    FILTERED_ADDRESS,
    HUB,
    LEADER,
    PATH,
    REOPENED,
    REOPENED_NOTICE,
    STALE_NOTICE,
    STALED,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text, hidden, visible

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


def icon(page, index, label):
    """Row ``index``'s icon whose name is ``label`` followed by the row."""
    return row(page, index).get_by_role("button", name=f"{label} {name(index)}")


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
    """Each row draws the icons its stage offers a Staff reader, and the
    decision or mark in force selected: pressed, unavailable and filled.
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
    assert icon(page, 0, "Send").count() == 0
    # Ready to update: Accept selected beside Back to review.
    chosen = icon(page, 2, "Accept")
    assert chosen.get_attribute("aria-pressed") == "true"
    assert chosen.get_attribute("aria-disabled") == "true"
    assert chosen.get_attribute("title") == (
        "Accepted; choose Back to review to change it"
    )
    visible(row(page, 2).get_by_role("button", name=f"Send {name(2)} back to review"))
    # Choosing it again is no undo: nothing opens and nothing is sent.
    sent = []
    page.on("request", lambda r: sent.append(r) if r.method == "POST" else None)
    chosen.click(force=True)
    assert page.locator("dialog[open]").count() == 0 and sent == []
    fill = "node => getComputedStyle(node).backgroundColor"
    assert chosen.evaluate(fill) != accept.evaluate(fill)
    # An edited item's Accept is selected and says so.
    assert (
        icon(page, 3, "Accept").get_attribute("title").startswith("Accepted (edited);")
    )
    assert icon(page, 4, "Ignore").get_attribute("aria-pressed") == "true"
    # Entered by hand: Accept and the mark selected, final; Not seen flagged.
    marked = row(page, 6)
    assert icon(page, 6, "Mark").get_attribute("aria-pressed") == "true"
    assert marked.locator("input[data-select-row]").count() == 0
    visible(marked.locator(".stage-chip.status-warning"))
    # Entered incorrectly: the Error look and ParishSoft now beside it.
    wrong = row(page, 5)
    has_text(wrong.locator(".stage-chip.status-error"), "Entered incorrectly")
    assert "555-0177" in wrong.inner_text()
    assert "555-0177" not in row(page, 0).inner_text()
    # Every row with a bulk action has a box (UX conventions, "Several rows").
    assert page.locator("input[data-select-row]").count() == 7
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


def test_reopening_an_ignored_item_needs_a_note(page, component_origin):
    """Back to review on an Ignored item waits for a reason; on an accepted
    one it does not. A bulk Back to review asks only while an Ignored row
    is ticked."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, REOPENED)
    dialog = page.locator("#worklist-decide-dialog")
    record = dialog.get_by_role("button", name="Record")
    row(page, 2).get_by_role("button", name=f"Send {name(2)} back to review").click()
    available(record)
    # The note's label follows the action in place (R38 finding 3).
    has_text(dialog.locator("label[for$=-note]"), "Note (optional)")
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    row(page, 4).get_by_role("button", name=f"Send {name(4)} back to review").click()
    has_text(
        dialog.locator("[data-confirm-body]"),
        "The ignored item goes back to To review. Say why it is reopened.",
    )
    assert record.is_disabled()
    has_text(
        dialog.locator("[data-complete-hint]"), "Say why this ignored item is reopened."
    )
    dialog.get_by_label("Note (required)").fill("Asked")
    available(record)
    record.click()
    has_text(page.locator("#worklist-notice"), REOPENED_NOTICE)
    assert "decide=back_to_review" in sent[-1] and "note=Asked" in sent[-1]
    assert page.evaluate(MARKED) == "kept"
    # The bulk dialog: only an Ignored row ticked makes the note required.
    page.goto(component_origin + PATH)
    back = page.locator(".bulk-bar").get_by_role("button", name="Back to review")
    bulk = page.locator("#worklist-bulk-dialog")
    page.get_by_label(f"Select {name(2)}").check()
    back.click()
    available(bulk.get_by_role("button", name="Record"))
    bulk.get_by_role("button", name="Cancel").click()
    hidden(bulk)
    page.get_by_label(f"Select {name(4)}").check()
    back.click()
    assert bulk.get_by_role("button", name="Record").is_disabled()


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
    assert [
        node.get_attribute("data-row-action")
        for node in row(page, 2).locator(".table-actions button").all()
    ] == ["accept", "back_to_review"]
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
