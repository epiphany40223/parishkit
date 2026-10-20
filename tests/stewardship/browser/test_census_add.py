"""Census Add on the review worklist, in place (#1710).

In Chromium and WebKit. The real page is served by ``census_components``;
each POST is answered with the fixture page after it. A mark outside every
region shows the page never reloaded. Add shows only the controls the
chosen field needs, sends only those, waits until they are complete, shows
a refusal at its field, and the added row offers only its own steps; once
Entered by hand, its Back to review undoes the mark with a required note
(#1840).
"""

import json

import pytest

from .census_components import (
    ADDED,
    ADDED_ENTERED,
    ADDED_NOTICE,
    ADDED_REOPENED,
    ADDED_REOPENED_NOTICE,
    PATH,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

REFUSAL = {
    "errors": [{"code": "invalid", "field_id": "value_member", "message": "Check."}],
    "refusal": {
        "message": (
            "ParishSoft's data does not list that Member in that Family, or lists "
            "them as inactive or deceased, so no change can be added for them."
        ),
        "fix": "Correct it, then choose Add again.",
        "link": None,
    },
}


def open_add(page):
    """Open the Add dialog from the selection bar; the dialog."""
    page.locator(".bulk-bar").get_by_role("button", name="Add", exact=True).click()
    dialog = page.locator("#census-add-dialog")
    visible(dialog)
    return dialog


def answer_posts(page, origin, refuse_first=False):
    """Answer each POST with the added page (the first with a refusal if
    ``refuse_first``); the bodies sent."""
    sent = []

    def answer(route):
        """Keep the body; refuse or answer with the fixture page."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(route.request.post_data)
        if refuse_first and len(sent) == 1:
            route.fulfill(
                status=400, content_type="application/json", body=json.dumps(REFUSAL)
            )
            return
        route.fulfill(response=route.fetch(url=origin + ADDED, method="GET"))

    page.route("**" + PATH, answer)
    return sent


def test_add_shows_only_the_chosen_fields_value(page, component_origin):
    """What changed comes first; a Member field asks for the Member DUID and
    its own value control, a Family address for its parts and no Member.
    Add waits until the shown fields are complete."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    dialog = open_add(page)
    member = dialog.get_by_label("Member DUID")
    visible(member)
    hidden(dialog.get_by_label("Street", exact=True))
    hidden(dialog.get_by_label("New value: Birth date"))
    add = dialog.get_by_role("button", name="Add", exact=True)
    assert add.is_disabled()
    dialog.get_by_label("What changed").select_option("home_address")
    visible(dialog.get_by_label("Street", exact=True))
    hidden(member)
    dialog.get_by_label("What changed").select_option("birth_date")
    birth = dialog.get_by_label("New value: Birth date")
    visible(birth)
    assert birth.get_attribute("type") == "date"
    assert birth.get_attribute("max") == "2054-10-06"
    hidden(dialog.get_by_label("Street", exact=True))
    visible(member)
    assert add.is_disabled()
    assert page.evaluate(MARKED) == "kept"


def test_an_add_posts_its_fields_and_answers_in_place(page, component_origin):
    """A filled Add posts decide=add with only the chosen field's controls and
    the page's view; a refusal is shown at its field, and the corrected Add
    answers with the page redrawn and the added row, which offers only
    Entered by hand and a Back to review that withdraws it."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = answer_posts(page, component_origin, refuse_first=True)
    dialog = open_add(page)
    dialog.get_by_label("What changed").select_option("nickname")
    dialog.get_by_label("Family DUID").fill("1003")
    member = dialog.get_by_label("Member DUID")
    member.fill("999")
    dialog.get_by_label("New value: Nickname").fill("Bo")
    add = dialog.get_by_role("button", name="Add", exact=True)
    available(add)
    add.click()
    refusal = REFUSAL["refusal"]
    has_text(dialog.locator(".errorlist"), f"{refusal['message']} {refusal['fix']}")
    assert member.get_attribute("aria-invalid") == "true"
    body = sent[0]
    assert "decide=add" in body and "value_field=nickname" in body
    assert "value_member=999" in body and "value_nickname=Bo" in body
    assert "value_first_name" not in body and "value_line1" not in body
    assert "stage=all" in body
    member.fill("103")
    available(add)
    add.click()
    has_text(page.locator("#census-notice"), ADDED_NOTICE)
    hidden(dialog)
    added = page.locator("#census-row-add-a1")
    has_text(added.locator(".stage-chip .status-word"), "To enter by hand")
    has_text(added.locator("span.help"), "Added by the parish.")
    actions = [
        node.get_attribute("data-row-action")
        for node in added.locator(".table-actions button").all()
    ]
    assert actions == ["accept", "back_to_review", "entered_by_hand"]
    assert added.locator('[data-row-action="accept"]').get_attribute(
        "aria-pressed"
    ) == ("true")
    added.locator('[data-row-action="back_to_review"]').click()
    decide = page.locator("#census-decide-dialog")
    has_text(decide.locator("h2"), "Withdraw Nickname for Member 03?")
    has_text(
        decide.locator("[data-confirm-body]"),
        "The added change is withdrawn: nothing is entered in ParishSoft, and it "
        "moves to History.",
    )
    assert page.evaluate(MARKED) == "kept"


def test_back_to_review_undoes_an_added_changes_mark_in_place(page, component_origin):
    """An added change Entered by hand offers Back to review (#1840): its
    dialog says the mark is undone and the change goes back to To enter by
    hand, and Record waits for the required note; the redrawn row is To
    enter by hand again, in place."""
    page.goto(component_origin + ADDED_ENTERED)
    page.evaluate(MARK)
    sent = []

    def answer(request):
        """Keep the body; answer with the page after the undo."""
        sent.append(request.post_data)
        return ADDED_REOPENED

    fulfil_post_with(page, "**" + PATH, component_origin, answer)
    added = page.locator("#census-row-add-a1")
    has_text(added.locator(".stage-chip .status-word"), "Entered by hand")
    added.locator('[data-row-action="back_to_review"]').click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), "Send Nickname for Member 03 back to review?")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Entered by hand is undone: the item goes back to To enter by hand, with "
        "its decision kept. Say why.",
    )
    record = dialog.get_by_role("button", name="Record")
    assert record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Typo")
    available(record)
    record.click()
    has_text(page.locator("#census-notice"), ADDED_REOPENED_NOTICE)
    hidden(dialog)
    assert "decide=back_to_review" in sent[-1] and "note=Typo" in sent[-1]
    assert "item=add-a1" in sent[-1] and "version=2" in sent[-1]
    has_text(added.locator(".stage-chip .status-word"), "To enter by hand")
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [320, 1280])
def test_the_add_dialog_fits_and_has_no_wcag_violation(
    page, component_origin, axe_source, width
):
    """The open Add dialog, with an address's parts shown, keeps the phone
    width without a page scroll and has no WCAG violation."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    dialog = open_add(page)
    dialog.get_by_label("What changed").select_option("mailing_address")
    visible(dialog.get_by_label("Street", exact=True))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)
