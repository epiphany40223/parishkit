"""Pledges to enter reviews pledges in place (#1686).

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
    CHECKED,
    EDITED,
    EDITED_NOTICE,
    EVERY,
    PATH,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

ACTION = "**/admin/reports/financial/pledges/"


def row(page, index):
    """Family ``index``'s row (its response id is ``UUID(int=index)``)."""
    return page.locator(f"#pledges-row-00000000-0000-0000-0000-{index:012x}")


def icon(page, index, label):
    """Row ``index``'s icon whose name is ``label`` followed by the Family."""
    return row(page, index).get_by_role(
        "button", name=f"{label} Family {index:02d}", exact=True
    )


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
    ] == ["accept", "edit", "ignore"]
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
    drawn selected; a marked one is final; an ignored one can be reopened."""
    page.goto(component_origin + EVERY)
    chosen = icon(page, 4, "Accept")
    assert chosen.get_attribute("aria-pressed") == "true"
    visible(row(page, 4).get_by_role("button", name="Mark Family 04 entered by hand"))
    marked = row(page, 5)
    assert marked.locator("input[data-select-row]").count() == 0
    assert (
        marked.get_by_role(
            "button", name="Mark Family 05 entered by hand"
        ).get_attribute("aria-pressed")
        == "true"
    )
    assert "Entered by staff@example.org" in marked.inner_text()
    visible(row(page, 6).get_by_role("button", name="Send Family 06 back to review"))


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
    with ParishSoft now beside the Reviewer's value and no action; Not seen
    in ParishSoft as a flag on Entered by hand, chosen on its own; Confirmed
    with nothing offered; an Ignored pledge only noted; and (#1747) a
    Conflict offering Accept, Edit and Ignore beside ParishSoft now, its
    earlier Accept shown not in force, and a To review pledge already in
    ParishSoft. The page fits a phone and has no WCAG violation."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CHECKED)
    stage = page.locator("#table-filters").get_by_label("Stage")
    assert stage.locator("option").all_inner_texts() == [
        "To review (0)",
        "Conflict (2)",
        "To enter by hand (0)",
        "Entered by hand (1)",
        "Entered incorrectly (1)",
        "Confirmed (2)",
        "Ignored (1)",
        "Not seen in ParishSoft (1)",
        "Every stage (7)",
    ]
    headings = page.locator("#table thead th").all_inner_texts()
    assert "ParishSoft now" in [text.strip() for text in headings]
    incorrect = row(page, 7)
    chip = incorrect.locator(".stage-chip.status-error")
    assert chip.inner_text().strip() == "Entered incorrectly"
    assert "$1,100.00" in incorrect.inner_text()
    assert incorrect.locator(".table-actions button:not([aria-disabled])").count() == 0
    flagged = row(page, 8)
    flag = flagged.locator(".stage-chip.status-warning")
    assert flag.inner_text().strip() == "Not seen in ParishSoft"
    assert "$0.00" in flagged.inner_text()
    confirmed = row(page, 9)
    assert confirmed.locator(".stage-chip").inner_text().strip() == "Confirmed"
    assert confirmed.locator(".table-actions button:not([aria-disabled])").count() == 0
    assert "ParishSoft now matches the Family's answer." in row(page, 6).inner_text()
    # #1747: a Conflict is decided again against ParishSoft now; a To review
    # pledge already in ParishSoft is Confirmed with nothing offered.
    for index, amount in ((10, "$900.00"), (11, "$950.00")):
        conflict = row(page, index)
        assert conflict.locator(".stage-chip").inner_text().strip() == "Conflict"
        assert amount in conflict.inner_text()
        for label in ("Accept", "Edit", "Ignore"):
            visible(icon(page, index, label))
    assert "Earlier: Accepted, not in force." in row(page, 11).inner_text()
    assert "Earlier:" not in row(page, 10).inner_text()
    already = row(page, 12)
    assert "Already in ParishSoft." in already.inner_text()
    assert already.locator(".table-actions button:not([aria-disabled])").count() == 0
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert_accessible(page, axe_source)
