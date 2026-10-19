"""Census changes on the review worklist decides in place (#1683).

In Chromium and WebKit. The real page is served by ``census_components``;
each POST is answered with the fixture page after it. A mark outside every
region shows the page never reloaded. The shared worklist's general
behaviour (selected icons, bulk gates, the reopen note) is tested on its
own fixture page (``test_review_worklist``); these tests check what Census
changes adds: its filters and sorts, By hand rows reviewed like the rest,
Entered by hand, Entered incorrectly's two fixes, the address editor and
a refusal at its fields.
"""

import json

import pytest

from .census_components import (
    ACCEPTED,
    ACCEPTED_NOTICE,
    BULK_NOTICE,
    BULKED,
    EDITED,
    EDITED_NOTICE,
    EDITED_PHONE,
    EDITED_PHONE_NOTICE,
    ENTERED,
    ENTERED_NOTICE,
    INCORRECT,
    PATH,
    REENTERED,
    REENTERED_NOTICE,
    RETURNED,
    RETURNED_NOTICE,
    STALE,
    STALE_NOTICE,
    WRONG,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with, sort_heading
from .waits import available, has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


def name(index):
    """How the page names row ``index``'s change (its Who is the Member)."""
    if index == 2:
        return "Home address for Family"
    return f"Mobile phone for Member {index:02d}"


def row(page, index):
    """Row ``index`` of the table."""
    return page.locator(f"#census-row-{index}")


def icon(page, index, label):
    """Row ``index``'s icon whose name is ``label`` followed by the row."""
    return row(page, index).get_by_role("button", name=f"{label} {name(index)}")


def posts(page, origin, answer):
    """Answer every POST to the page with the fixture ``answer``; the bodies."""
    sent = []

    def choose(request):
        """Keep the body and answer with the fixture page."""
        sent.append(request.post_data)
        return answer(request.post_data) if callable(answer) else answer

    fulfil_post_with(page, "**" + PATH, origin, choose)
    return sent


def test_filters_page_and_sort_in_place(page, component_origin):
    """The filters are open above the table, Stage first; Next and a heading
    page and sort in place, with the filters in the POST body only, and the
    download sits below the table."""
    page.goto(component_origin + "/census-changes")
    page.evaluate(MARK)
    filters = page.locator("#table-filters")
    assert filters.locator("xpath=ancestor::details").count() == 0
    assert filters.locator("label").first.inner_text() == "Stage"
    export = page.locator("#table-export").bounding_box()
    assert export["y"] > page.locator(".table-scroll").bounding_box()["y"]
    sent = posts(
        page,
        component_origin,
        lambda body: (
            "/census-changes-page-2" if "page=2" in body else "/census-changes-who"
        ),
    )
    page.locator("#table").get_by_role("button", name="Next").first.click()
    has_text(page.locator("#table tbody th").first, "Family 25")
    assert "page=2" in sent[-1] and "stage=all" in sent[-1]
    page.locator("#table").get_by_role("button", name="Who (sort ascending)").click()
    has_attribute(sort_heading(page, "who"), "aria-sort", "descending")
    assert page.evaluate(MARKED) == "kept"
    assert "stage" not in page.url and page.url.endswith("/census-changes")


def test_staff_accept_an_automatic_change_in_place(page, component_origin):
    """Accept asks in the shared dialog, posts the row's decision with the
    page's filters, and the redrawn row shows Accept selected beside Back to
    review, with the notice announced."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts(page, component_origin, ACCEPTED)
    icon(page, 0, "Accept").click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"Accept {name(0)}?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), ACCEPTED_NOTICE)
    hidden(dialog)
    assert icon(page, 0, "Accept").get_attribute("aria-pressed") == "true"
    has_text(row(page, 0).locator(".stage-chip .status-word"), "Ready to update")
    body = sent[-1]
    assert "decide=accept" in body and "item=0" in body and "version=1" in body
    assert "stage=all" in body and "route=any" in body
    assert page.evaluate(MARKED) == "kept"


def test_entered_by_hand_on_a_to_review_row_is_one_step(page, component_origin):
    """A To review By hand row offers Entered by hand beside Accept, Edit and
    Ignore; its dialog says it is final, and the redrawn row is Entered by
    hand with its mark selected and no box."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts(page, component_origin, ENTERED)
    actions = [
        node.get_attribute("data-row-action")
        for node in row(page, 1).locator(".table-actions button").all()
    ]
    assert actions == ["accept", "edit", "ignore", "entered_by_hand"]
    icon(page, 1, "Mark").click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"Mark {name(1)} entered by hand?")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Record that you typed this into ParishSoft. Check ParishSoft first: "
        "this is final.",
    )
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), ENTERED_NOTICE)
    assert "decide=entered_by_hand" in sent[-1]
    has_text(row(page, 1).locator(".stage-chip .status-word"), "Entered by hand")
    assert row(page, 1).locator("input[data-select-row]").count() == 0
    assert page.evaluate(MARKED) == "kept"


def test_bulk_accept_covers_automatic_and_by_hand_rows(page, component_origin):
    """Every To review row has a box, By hand ones too, so one bulk Accept
    covers both kinds; the dialog counts them."""
    page.goto(component_origin + PATH)
    sent = posts(page, component_origin, BULKED)
    page.get_by_label(f"Select {name(0)}").check()
    page.get_by_label(f"Select {name(1)}").check()
    accept = page.locator(".bulk-bar").get_by_role("button", name="Accept", exact=True)
    available(accept)
    accept.click()
    dialog = page.locator("#census-bulk-dialog")
    has_text(dialog.locator("h2"), "Accept 2 changes?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), BULK_NOTICE)
    assert "bulk=accept" in sent[-1] and "item=0%3A1" in sent[-1]
    assert "item=1%3A1" in sent[-1]


def test_a_stale_decision_is_announced_as_such(page, component_origin):
    """A row someone else changed first answers with the current page and a
    notice naming the row; that notice is announced."""
    page.goto(component_origin + PATH)
    posts(page, component_origin, STALE)
    icon(page, 0, "Ignore").click()
    page.locator("#census-decide-dialog").get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), STALE_NOTICE)
    has_text(
        page.get_by_role("status").filter(
            has_text="not recorded, changed by someone else"
        ),
        STALE_NOTICE,
    )


def test_an_address_is_edited_part_by_part(page, component_origin):
    """A Family address's Edit holds its parts, starting from the Family's
    answer; a refusal is shown in the dialog at the refused part, and the
    accepted edit answers with the page redrawn."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    refusal = {
        "errors": [
            {
                "code": "invalid",
                "field_id": "value_postal_code",
                "message": "Check this value.",
            }
        ],
        "refusal": {
            "message": "Enter a five-digit ZIP code or ZIP+4.",
            "fix": "Correct the value, then save it again.",
            "link": None,
        },
    }
    sent = []

    def answer(route):
        """Refuse the first save at its ZIP code; answer the next with the page."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(route.request.post_data)
        if len(sent) == 1:
            route.fulfill(
                status=400, content_type="application/json", body=json.dumps(refusal)
            )
            return
        route.fulfill(response=route.fetch(url=component_origin + EDITED, method="GET"))

    page.route("**" + PATH, answer)
    icon(page, 2, "Edit").click()
    editor = page.locator("#census-edit-dialog-2")
    street = editor.get_by_label("Street", exact=True)
    assert street.input_value() == "9 Census Way"
    assert editor.get_by_label("Country").input_value() == "US"
    zip_code = editor.get_by_label("ZIP or postal code")
    zip_code.fill("402")
    editor.get_by_role("button", name="Save").click()
    has_text(
        editor.locator(".errorlist"),
        "Enter a five-digit ZIP code or ZIP+4. Correct the value, then save it again.",
    )
    assert zip_code.get_attribute("aria-invalid") == "true"
    assert "value_postal_code=402" in sent[0] and "decide=edit" in sent[0]
    zip_code.fill("40224")
    save = editor.get_by_role("button", name="Save")
    available(save)
    save.click()
    has_text(page.locator("#census-notice"), EDITED_NOTICE)
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [320, 1280])
def test_the_page_fits_and_has_no_wcag_violation(
    page, component_origin, axe_source, width
):
    """The census worklist, filters and download pane included, keeps the
    phone width without a page scroll and has no WCAG violation; so does an
    open address editor."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)
    icon(page, 2, "Edit").click()
    visible(page.locator("#census-edit-dialog-2"))
    assert_accessible(page, axe_source)


def actions(page, index):
    """Row ``index``'s icons: (action, pressed) in drawing order."""
    return [
        (node.get_attribute("data-row-action"), node.get_attribute("aria-pressed"))
        for node in row(page, index).locator(".table-actions button").all()
    ]


def test_entered_by_hand_again_on_an_entered_incorrectly_row(page, component_origin):
    """An Entered incorrectly row shows ParishSoft's value and offers its two
    fixes beside the Accept in force (#1737): the mark is worded "again", its
    dialog says the next refresh checks it, and the redrawn row is Entered by
    hand, in place."""
    page.goto(component_origin + INCORRECT)
    page.evaluate(MARK)
    sent = posts(page, component_origin, REENTERED)
    has_text(row(page, 3).locator(".stage-chip"), "Entered incorrectly")
    visible(row(page, 3).get_by_text(WRONG, exact=True))
    assert actions(page, 3) == [
        ("accept", "true"),
        ("back_to_review", None),
        ("entered_by_hand", "false"),
    ]
    # Both fixes are bulk kinds of its box.
    assert (
        row(page, 3)
        .locator("input[data-select-row]")
        .get_attribute("data-confirm-kind")
        == "back_to_review entered_by_hand"
    )
    icon(page, 3, "Mark").click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"Mark {name(3)} entered by hand again?")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Record that you corrected this in ParishSoft. Check ParishSoft first: "
        "the next refresh checks it again.",
    )
    available(dialog.get_by_role("button", name="Record"))
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), REENTERED_NOTICE)
    hidden(dialog)
    assert "decide=entered_by_hand" in sent[-1] and "item=3" in sent[-1]
    assert "version=2" in sent[-1]
    has_text(row(page, 3).locator(".stage-chip .status-word"), "Entered by hand")
    assert page.evaluate(MARKED) == "kept"


def test_back_to_review_on_an_entered_incorrectly_row(page, component_origin):
    """Back to review on an Entered incorrectly row needs no note; its dialog
    says to edit the item to ParishSoft's value (no confirmation is promised:
    a refresh before the Edit makes it a Conflict), and the redrawn row is
    To review, in place."""
    page.goto(component_origin + INCORRECT)
    page.evaluate(MARK)
    sent = posts(page, component_origin, RETURNED)
    icon(page, 3, "Send").click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"Send {name(3)} back to review?")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "The item goes back to To review. If ParishSoft's value is right, edit "
        "the item to that value.",
    )
    record = dialog.get_by_role("button", name="Record")
    available(record)
    record.click()
    has_text(page.locator("#census-notice"), RETURNED_NOTICE)
    hidden(dialog)
    assert "decide=back_to_review" in sent[-1] and "item=3" in sent[-1]
    has_text(row(page, 3).locator(".stage-chip .status-word"), "To review")
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [390, 1280])
def test_apply_follows_the_last_filter_with_no_line_between(
    page, component_origin, width
):
    """No zone line sits under Census changes' filters (R38 finding 8), and
    Apply filters with Clear filters follow Through, beside it at 1280 px
    (R38 finding 9); the page never scrolls sideways."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    form = page.locator("#table-filters")
    assert form.locator("[data-browser-zone-note]").count() == 0
    through = form.locator("#census-end").locator("xpath=..").bounding_box()
    actions = form.locator(".filter-actions").bounding_box()
    if width == 1280:
        assert actions["x"] >= through["x"] + through["width"]
        assert (
            abs((actions["y"] + actions["height"]) - (through["y"] + through["height"]))
            <= 2
        )
    else:
        assert through["y"] + through["height"] <= actions["y"]
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_an_edited_value_shows_as_the_reviewers_value(page, component_origin):
    """Edit's Save waits until the value changes (saving the Family's answer
    would record a plain Accept), and the answer shows the edited value in
    Reviewer's value, not only in Last decision (R38 finding 4)."""
    page.goto(component_origin + PATH)
    sent = posts(page, component_origin, EDITED_PHONE)
    icon(page, 0, "Edit").click()
    editor = page.locator("#census-edit-dialog-0")
    save = editor.get_by_role("button", name="Save")
    assert save.is_disabled()
    has_text(
        editor.locator("[data-complete-hint]"),
        "Change the value to save it; Accept keeps it as it is.",
    )
    editor.get_by_label("Reviewer's value").fill("555-9999")
    available(save)
    save.click()
    has_text(page.locator("#census-notice"), EDITED_PHONE_NOTICE)
    assert "decide=edit" in sent[-1] and "value=555-9999" in sent[-1]
    headings = [
        node.inner_text().strip() for node in page.locator("#table thead th").all()
    ]
    cells = row(page, 0).locator("th, td")
    reviewer = cells.nth(headings.index("Reviewer's value")).inner_text()
    assert "555-9999" in reviewer
