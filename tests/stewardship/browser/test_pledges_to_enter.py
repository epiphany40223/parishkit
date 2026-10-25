"""Pledges reviews pledges in place (#1686).

The page is the view's own context over sample rows
(``pledges_components``); each POST is answered with the page after the
decision, as the view answers with its page redrawn, or, for an Edit value
the server refuses, with the view's own JSON refusal. A mark outside every
region shows the page never reloaded. In Chromium and WebKit.
"""

import json

import pytest

from .pledges_components import (
    ACCEPTED,
    ACCEPTED_NOTICE,
    ADDRESS,
    CHECKED,
    EDITED,
    EDITED_NOTICE,
    EVERY,
    EVERY_ADDRESS,
    PATH,
    UNDONE,
    UNDONE_NOTICE,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

ACTION = "**/admin/reports/financial/pledges/"

# The row editor's own body, which the Edit icon must not replace (R40c F1).
BODY = (
    "Record this value as the reviewer's value. The item is Accepted (edited), "
    "and the Family's answer stays beside it. The Family will see this value "
    "the next time it signs in."
)


def row(page, index):
    """Family ``index``'s row (its response id is ``UUID(int=index)``)."""
    return page.locator(f"#pledges-row-00000000-0000-0000-0000-{index:012x}")


ACTIONS = {
    "Accept": "accept",
    "Edit": "edit",
    "Ignore": "ignore",
    "Mark": "entered_by_hand",
}


def icon(page, index, label):
    """Row ``index``'s ``label`` icon (Accept, Edit, Ignore or Mark), by its
    action: a pressed icon's name says what choosing it does (#2131)."""
    return row(page, index).locator(f'button[data-row-action="{ACTIONS[label]}"]')


def undo(page, index):
    """Row ``index``'s Back to review: its pressed icon (#2131), or the icon
    of its own on a row with nothing pressed."""
    return row(page, index).locator(
        'button[data-row-undo], button[data-row-action="back_to_review"]'
    )


def offered(locator):
    """The decisions a row's available icons record, in order; a pressed
    one records Back to review."""
    return [
        "back_to_review"
        if node.get_attribute("data-row-undo") is not None
        else node.get_attribute("data-row-action")
        for node in locator.locator(".table-actions button:not([aria-disabled])").all()
    ]


def posts_to(page, origin, answer):
    """Answer every POST to the page's action with the fixture ``answer``."""
    sent = []

    def choose(request):
        """Keep the body and answer with the page after the decision."""
        sent.append(request.post_data)
        return answer

    fulfil_post_with(page, ACTION, origin, choose)
    return sent


@pytest.mark.parametrize("width", [320, 1280])
def test_the_worklist_lists_pledges_to_review(
    page, component_origin, axe_source, width
):
    """Stage leads the always-open filters, with each stage's count; a To
    review row offers Accept, Edit and Ignore; the export buttons sit below
    the table; the page fits a phone and has no WCAG violation."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    filters = page.locator("#table-filters")
    assert filters.locator("xpath=ancestor::details").count() == 0
    assert filters.locator("label").first.inner_text() == "Stage"
    stage = filters.get_by_label("Stage")
    assert stage.locator("option").all_inner_texts() == [
        "To review (3)",
        "Conflict (0)",
        "To enter by hand (1)",
        "Entered by hand (1)",
        "Entered incorrectly (0)",
        "Confirmed (0)",
        "Ignored (1)",
        "Every stage (6)",
    ]
    visible(filters.get_by_label("Search Family name or Family DUID"))
    assert page.locator("#table tbody tr").count() == 3
    assert [
        node.get_attribute("data-row-action")
        for node in row(page, 1).locator(".table-actions button").all()
    ] == ["accept", "edit", "ignore", "entered_by_hand"]
    assert offered(row(page, 1)) == ["accept", "edit", "ignore"]
    assert "$1,200.00, Weekly" in row(page, 1).inner_text()
    assert "$0.00, No frequency" in row(page, 2).inner_text()
    bar = page.locator(".bulk-bar")
    assert [node.inner_text() for node in bar.locator("[data-bulk-action]").all()] == [
        "Accept",
        "Ignore",
        "Back to review",
        "Entered by hand",
    ]
    assert bar.get_by_role("button", name="Add").count() == 0
    table = page.locator(".table-scroll").bounding_box()
    export = page.locator(".export-pane").bounding_box()
    assert export["y"] >= table["y"] + table["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)


def test_every_stage_shows_each_decision_in_force(page, component_origin):
    """An accepted pledge offers Entered by hand and Back to review, its Accept
    drawn pressed as the undo (#2131); a marked one's pressed mark is its
    Back to review (#1840); an ignored one can be reopened."""
    page.goto(component_origin + EVERY)
    chosen = icon(page, 4, "Accept")
    assert chosen.get_attribute("aria-pressed") == "true"
    assert undo(page, 4).get_attribute("data-row-action") == "accept"
    visible(row(page, 4).get_by_role("button", name="Mark Family 04 entered by hand"))
    marked = row(page, 5)
    visible(marked.locator("input[data-select-row]"))
    assert undo(page, 5).get_attribute("data-row-action") == "entered_by_hand"
    assert icon(page, 5, "Mark").get_attribute("aria-pressed") == "true"
    assert "Entered by staff@example.org" in marked.inner_text()
    assert undo(page, 6).get_attribute("data-row-action") == "ignore"


def test_a_reload_keeps_the_applied_stage(page, component_origin):
    """Changing the Stage in place puts the view's own address in the address
    bar, so a reload draws the Stage on screen, not the old one (#1820)."""
    page.goto(component_origin + ADDRESS)
    posts_to(page, component_origin, EVERY)
    page.get_by_label("Stage").select_option("all")
    page.get_by_role("button", name="Apply filters").click()
    page.wait_for_url(component_origin + EVERY_ADDRESS)
    page.reload()
    assert page.url == component_origin + EVERY_ADDRESS
    assert page.get_by_label("Stage").input_value() == "all"
    assert page.locator("#table tbody tr").count() == 6


def test_back_to_review_undoes_a_mark_with_a_note_in_place(page, component_origin):
    """Back to review on an Entered by hand pledge (#1840), its pressed mark
    chosen again (#2131): the dialog says
    the mark is undone and the decision kept, Record waits for the required
    note, and the answer redraws the row in place at To enter by hand."""
    page.goto(component_origin + EVERY)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, UNDONE)
    undo(page, 5).click()
    dialog = page.locator("#pledges-decide-dialog")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Entered by hand is undone: the item goes back to To enter by hand, "
        "with its decision kept. Say why.",
    )
    record = dialog.get_by_role("button", name="Record")
    assert record.get_attribute("aria-disabled") == "true" or record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Not typed yet")
    available(record)
    record.click()
    has_text(page.locator("#pledges-notice"), UNDONE_NOTICE)
    hidden(dialog)
    body = sent[-1]
    assert "decide=back_to_review" in body and "note=Not+typed+yet" in body
    has_text(row(page, 5).locator(".stage-chip .status-word"), "To enter by hand")
    visible(row(page, 5).get_by_role("button", name="Mark Family 05 entered by hand"))
    assert icon(page, 5, "Mark").get_attribute("aria-pressed") == "false"
    assert page.evaluate(MARKED) == "kept"


def test_a_bulk_back_to_review_needs_a_note_while_a_marked_row_is_ticked(
    page, component_origin
):
    """A bulk Back to review over an accepted and a marked pledge asks for
    the note, as the marked row needs one (#1840)."""
    page.goto(component_origin + EVERY)
    page.get_by_label("Select Family 04").check()
    page.get_by_label("Select Family 05").check()
    back = page.locator(".bulk-bar").get_by_role(
        "button", name="Back to review", exact=True
    )
    available(back)
    back.click()
    dialog = page.locator("#pledges-bulk-dialog")
    visible(dialog.get_by_label("Note (required)"))
    record = dialog.get_by_role("button", name="Record")
    assert record.get_attribute("aria-disabled") == "true" or record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Both wrong")
    available(record)


def test_bulk_accept_records_in_place(page, component_origin):
    """Tick the pledges, Accept them together: the dialog counts them, the
    post names each with its version, and the answer redraws in place."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, ACCEPTED)
    page.get_by_label("Select Family 01").check()
    page.get_by_label("Select Family 02").check()
    accept = page.locator(".bulk-bar").get_by_role("button", name="Accept", exact=True)
    available(accept)
    accept.click()
    dialog = page.locator("#pledges-bulk-dialog")
    has_text(dialog.locator("h2"), "Accept 2 pledges?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#pledges-notice"), ACCEPTED_NOTICE)
    hidden(dialog)
    body = sent[-1]
    assert "bulk=accept" in body and "bulk_key=" in body and "stage=to_review" in body
    assert "item=00000000-0000-0000-0000-000000000001%3A0" in body
    assert "item=00000000-0000-0000-0000-000000000002%3A0" in body
    assert page.locator("#table tbody tr").count() == 1
    assert page.evaluate(MARKED) == "kept"


def test_edit_saves_the_reviewers_pledge(page, component_origin):
    """Edit opens the row's pledge editor holding the Family's pledge; Save
    posts the amount and frequency, and the row shows the reviewer's value."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, EDITED)
    icon(page, 3, "Edit").click()
    editor = page.locator("#pledges-edit-dialog-00000000-0000-0000-0000-000000000003")
    has_text(editor.locator("h2"), "Edit Family 03")
    has_text(editor.locator("[data-confirm-body]"), BODY)
    amount = editor.get_by_label("Annual pledge, in dollars")
    frequency = editor.get_by_label("Frequency")
    assert amount.input_value() == "1200.00"
    assert frequency.input_value() == "weekly"
    save = editor.get_by_role("button", name="Save")
    amount.fill("12.345")
    assert save.is_disabled()
    amount.fill("1,500")
    frequency.select_option("annual")
    available(save)
    save.click()
    has_text(page.locator("#pledges-notice"), EDITED_NOTICE)
    body = sent[-1]
    assert "decide=edit" in body and "value_amount=1%2C500" in body
    assert "value_frequency=annual" in body and "version=0" in body
    # Accepted (edited) rows leave To review; the page answered in place.
    assert page.locator("#table tbody tr").count() == 2
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_edit_is_shown_at_its_field(page, component_origin):
    """A pledge above zero with no frequency is refused at Frequency, and
    Save waits until the field is changed."""
    page.goto(component_origin + PATH)

    def refuse(route):
        """The view's answer to that Edit (``_edit_refusal``)."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps(
                {
                    "errors": [
                        {
                            "code": "invalid",
                            "field_id": "value_frequency",
                            "message": "Check this value.",
                        }
                    ],
                    "refusal": {
                        "message": "Choose how often the Family gives: a pledge "
                        "above zero needs one.",
                        "fix": "Correct the value, then save it again.",
                    },
                }
            ),
        )

    page.route(ACTION, refuse)
    icon(page, 1, "Edit").click()
    editor = page.locator("#pledges-edit-dialog-00000000-0000-0000-0000-000000000001")
    editor.get_by_label("Frequency").select_option("")
    save = editor.get_by_role("button", name="Save")
    save.click()
    error = editor.locator(".errorlist")
    has_text(
        error,
        "Choose how often the Family gives: a pledge above zero needs one. "
        "Correct the value, then save it again.",
    )
    frequency = editor.get_by_label("Frequency")
    assert frequency.get_attribute("aria-invalid") == "true"
    assert save.is_disabled()
    frequency.select_option("monthly")
    available(save)


@pytest.mark.parametrize("width", [320, 1280])
def test_refresh_checks_show_on_the_worklist(page, component_origin, axe_source, width):
    """What a refresh found (#1704): Entered incorrectly in its danger chip
    with ParishSoft now beside the Reviewer's value; Not seen in ParishSoft
    as a flag on Entered by hand, chosen on its own; Confirmed; each
    offering only Back to review (#1840); an Ignored pledge only noted; and
    (#1747) a Conflict offering Accept, Edit and Ignore beside ParishSoft
    now, its earlier Accept shown not in force, and a To review and a To
    enter by hand pledge ParishSoft already holds, which keep their stage
    and say so (#1890). The page fits a phone and has no WCAG violation."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CHECKED)
    stage = page.locator("#table-filters").get_by_label("Stage")
    assert stage.locator("option").all_inner_texts() == [
        "To review (1)",
        "Conflict (2)",
        "To enter by hand (1)",
        "Entered by hand (1)",
        "Entered incorrectly (1)",
        "Confirmed (1)",
        "Ignored (1)",
        "Not seen in ParishSoft (1)",
        "Every stage (8)",
    ]
    headings = page.locator("#table thead th").all_inner_texts()
    assert "ParishSoft now" in [text.strip() for text in headings]
    incorrect = row(page, 7)
    chip = incorrect.locator(".stage-chip.status-error")
    assert chip.inner_text().strip() == "Entered incorrectly"
    assert "$1,100.00" in incorrect.inner_text()
    assert offered(incorrect) == ["back_to_review"]
    flagged = row(page, 8)
    flag = flagged.locator(".stage-chip.status-warning")
    assert flag.inner_text().strip() == "Not seen in ParishSoft"
    assert "$0.00" in flagged.inner_text()
    confirmed = row(page, 9)
    assert confirmed.locator(".stage-chip").inner_text().strip() == "Confirmed"
    visible(undo(page, 9))
    assert "ParishSoft now matches the Family's answer." in row(page, 6).inner_text()
    # #1747: a Conflict is decided again against ParishSoft now. #1890: a
    # pledge ParishSoft already holds keeps its stage and says so: To review
    # offers the decisions (never Entered by hand), To enter by hand its
    # mark.
    for index, amount in ((10, "$900.00"), (11, "$950.00")):
        conflict = row(page, index)
        assert conflict.locator(".stage-chip").inner_text().strip() == "Conflict"
        assert amount in conflict.inner_text()
        for label in ("Accept", "Edit", "Ignore"):
            visible(icon(page, index, label))
    assert "Earlier: Accepted, not in force." in row(page, 11).inner_text()
    assert "Earlier:" not in row(page, 10).inner_text()
    for index, stage_word, actions in (
        (12, "To review", ["accept", "edit", "ignore"]),
        (13, "To enter by hand", ["back_to_review", "entered_by_hand"]),
    ):
        already = row(page, index)
        assert already.locator(".stage-chip").inner_text().strip() == stage_word
        assert "ParishSoft already holds this value." in already.inner_text()
        assert offered(already) == actions
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert_accessible(page, axe_source)


# --- Share methods on an Edit (#2177) --------------------------------------

ONLINE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def share_editor(page, index):
    """Row ``index``'s pledge editor, opened, and its share-method group."""
    icon(page, index, "Edit").click()
    editor = page.locator(f"#pledges-edit-dialog-00000000-0000-0000-0000-{index:012x}")
    visible(editor)
    return editor, editor.locator("fieldset.choice-group")


def test_edit_changes_the_share_methods_in_the_dialog(page, component_origin):
    """The editor shows the Family form's share methods, ticked as the Family
    answered, Other's details shown while it is ticked; unticking every one
    of a pledge above zero holds Save; Save posts the ticked ones, and the
    row shows the reviewer's share methods beside the Family's answer."""
    from .pledges_components import SHARES, SHARES_EVERY

    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, SHARES)
    editor, group = share_editor(page, 3)
    visible(group)
    online = group.get_by_label("Online giving")
    other = group.get_by_label("Other", exact=True)
    details = group.get_by_label("Details for Other")
    assert online.is_checked() and other.is_checked()
    assert details.input_value() == "Stock gift"
    save = editor.get_by_role("button", name="Save")
    # Cancel and reopen: the boxes start again ticked as drawn.
    other.uncheck()
    hidden(details)
    editor.get_by_role("button", name="Cancel").click()
    editor, group = share_editor(page, 3)
    online = group.get_by_label("Online giving")
    other = group.get_by_label("Other", exact=True)
    details = group.get_by_label("Details for Other")
    assert online.is_checked() and other.is_checked()
    visible(details)
    save = editor.get_by_role("button", name="Save")
    # Other ticked without details: Save waits, saying what the box needs.
    details.fill("")
    assert save.is_disabled()
    has_text(
        page.locator("#" + save.get_attribute("aria-describedby")),
        "Enter the details this share method asks for, in at most 2,000 characters.",
    )
    details.fill("Stock gift")
    other.uncheck()
    hidden(details)
    online.uncheck()
    # None ticked for a pledge above zero: the Family form's rule.
    assert save.is_disabled()
    online.check()
    available(save)
    save.click()
    has_text(page.locator("#pledges-notice"), EDITED_NOTICE)
    body = sent[-1]
    assert "value_shares=1" in body and f"value_share_{ONLINE}=on" in body
    assert f"value_share_{OTHER}" not in body and "value_share_text_" not in body
    assert "value_amount=1200.00" in body and "value_frequency=weekly" in body
    assert page.evaluate(MARKED) == "kept"
    # The value in force, with the Family's answer beside it (an editor
    # drawn from it is database/test_pledge_share_methods_postgresql's).
    from playwright.sync_api import expect

    page.goto(component_origin + SHARES_EVERY)
    cells = row(page, 3).locator("td")
    expect(cells.filter(has_text="Stock gift")).to_have_text(
        "$1,200.00, WeeklyOnline giving, Other: Stock gift"
    )
    expect(cells.filter(has_text="Weekly").last).to_have_text(
        "$1,200.00, WeeklyOnline giving"
    )


def test_a_zero_pledge_hides_and_sends_no_share_methods(page, component_origin):
    """No frequency (a zero pledge) hides the share methods, as the Family
    form does; the post says none were sent for it."""
    page.goto(component_origin + PATH)
    sent = posts_to(page, component_origin, EDITED)
    editor, group = share_editor(page, 3)
    editor.get_by_label("Annual pledge, in dollars").fill("0")
    editor.get_by_label("Frequency").select_option("")
    hidden(group)
    editor.get_by_role("button", name="Save").click()
    has_text(page.locator("#pledges-notice"), EDITED_NOTICE)
    body = sent[-1]
    assert "value_shares=1" in body and "value_share_" not in body


def test_a_stale_share_method_edit_is_refused_in_place(page, component_origin):
    """An Edit someone else's change made stale is answered with the page as
    it is, saying nothing was recorded; the row stays To review."""
    from .pledges_components import STALE, STALE_NOTICE

    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    posts_to(page, component_origin, STALE)
    editor, group = share_editor(page, 3)
    group.get_by_label("Online giving").uncheck()
    editor.get_by_role("button", name="Save").click()
    has_text(page.locator("#pledges-notice"), STALE_NOTICE)
    assert offered(row(page, 3)) == ["accept", "edit", "ignore"]
    assert page.evaluate(MARKED) == "kept"
