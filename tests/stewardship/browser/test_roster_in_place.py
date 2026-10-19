"""Roster changes to enter marks in place (#528 step 4, #1687).

The list is the Ministry stream's manual handling report on the shared
review worklist, served by ``roster_components`` with the view's own page
context; each POST is answered with the fixture page after the decision, as
the view answers with its page redrawn. The request page is served by
``followup_components``. A mark outside every region shows the page never
reloaded. In Chromium and WebKit.
"""

import pytest

from .followup_components import TICK_ITEM
from .roster_components import (
    BULK_NOTICE,
    BULKED,
    EMPTY,
    ENTERED,
    HELD,
    MARKED_NOTICE,
    MARKED_ONE,
    PATH,
    STALE,
    STALE_NOTICE,
    request_id,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


def row(page, index):
    """Row ``index`` of the list (29 is the newest)."""
    return page.locator(f"#roster-row-{request_id(index)}")


def mark_icon(page, index):
    """Row ``index``'s Entered by hand icon."""
    return row(page, index).get_by_role(
        "button", name=f"Mark Member {index:02d} — Food pantry entered by hand"
    )


def top(locator):
    """A locator's top edge on the page, to the pixel."""
    return locator.evaluate(
        "node => Math.round(node.getBoundingClientRect().top + scrollY)"
    )


def posts_to(page, origin, answer):
    """Answer every POST to the list with the fixture ``answer``; the bodies."""
    sent = []

    def choose(request):
        """Keep the body and answer with the list after the decision."""
        sent.append(request.post_data)
        return answer

    fulfil_post_with(page, "**" + PATH, origin, choose)
    return sent


@pytest.mark.parametrize("width", [320, 1280])
def test_the_list_is_a_worklist_with_stage_first(
    page, component_origin, axe_source, width
):
    """The filters are always open, Stage first with To enter by hand chosen
    and counted, then Ministry; the Show pair is gone. Each row offers only
    Entered by hand, the bulk bar only the same, and the export buttons sit
    below the table. No WCAG violation and no sideways scroll at a phone or
    a desktop width."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    filters = page.locator("#table-filters")
    assert filters.locator("xpath=ancestor::details").count() == 0
    labels = [node.inner_text() for node in filters.locator("label").all()]
    assert labels == ["Stage", "Ministry"]
    stage = filters.get_by_label("Stage")
    assert stage.input_value() == "to_enter_by_hand"
    assert [node.inner_text() for node in stage.locator("option").all()] == [
        "To enter by hand (30)",
        "Entered by hand (0)",
        "Every stage (30)",
    ]
    assert page.locator("#roster-show").count() == 0
    assert page.get_by_text("Not yet entered", exact=True).count() == 0
    assert [
        node.get_attribute("data-row-action")
        for node in row(page, 29).locator(".table-actions button").all()
    ] == ["entered_by_hand"]
    assert mark_icon(page, 29).get_attribute("aria-pressed") == "false"
    bar = page.locator(".bulk-bar")
    assert [node.inner_text() for node in bar.locator("[data-bulk-action]").all()] == [
        "Entered by hand"
    ]
    assert bar.get_by_role("button", name="Add").count() == 0
    export = page.locator(".export-pane").get_by_role("button", name="Export XLSX")
    table = page.locator("#table .table-scroll")
    assert top(export) > top(table) + table.bounding_box()["height"]
    assert page.locator("#table-export input[name=stage]").input_value() == (
        "to_enter_by_hand"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)


def test_entered_by_hand_records_in_place(page, component_origin):
    """A row's Entered by hand asks in the shared dialog naming the row, with
    no note to fill, posts the row's tick sequence with the list's view, and
    the answer redraws the list in place: the notice is announced, the row
    leaves To enter by hand, and nothing above the table moved."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, MARKED_ONE)
    table = page.locator(".table-scroll")
    before = top(table)
    mark_icon(page, 29).click()
    dialog = page.locator("#roster-decide-dialog")
    has_text(dialog.locator("h2"), "Mark Member 29 — Food pantry entered by hand?")
    assert dialog.get_by_role("textbox").count() == 0
    record = dialog.get_by_role("button", name="Record")
    available(record)
    record.click()
    has_text(page.locator("#roster-notice"), MARKED_NOTICE)
    hidden(dialog)
    assert row(page, 29).count() == 0
    body = sent[-1]
    assert "decide=entered_by_hand" in body and f"item={request_id(29)}" in body
    assert "version=1" in body and "request_key=" in body and "note=&" in body
    assert "stage=to_enter_by_hand" in body
    assert top(table) == before
    assert page.evaluate(MARKED) == "kept"


def test_bulk_marks_the_ticked_changes_in_place(page, component_origin):
    """Entered by hand in the bulk bar waits for a ticked row, asks naming
    the count, and posts each ticked row's tick sequence, all in place."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, BULKED)
    button = page.locator(".bulk-bar").get_by_role("button", name="Entered by hand")
    assert button.is_disabled()
    page.get_by_label("Select Member 29 — Food pantry").check()
    page.get_by_label("Select Member 28 — Food pantry").check()
    available(button)
    button.click()
    dialog = page.locator("#roster-bulk-dialog")
    has_text(dialog.locator("h2"), "Mark 2 changes entered by hand?")
    assert dialog.get_by_role("textbox").count() == 0
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#roster-notice"), BULK_NOTICE)
    body = sent[-1]
    assert "bulk=entered_by_hand" in body and "bulk_key=" in body
    assert f"item={request_id(29)}%3A1" in body
    assert f"item={request_id(28)}%3A1" in body and "bulk_note=" in body
    assert row(page, 28).count() == 0 and row(page, 27).count() == 1
    assert page.evaluate(MARKED) == "kept"


def test_a_mark_someone_made_first_says_so(page, component_origin):
    """A row someone else marked first records nothing: the list answers with
    its current state and the notice naming the row, in place."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    posts_to(page, component_origin, STALE)
    mark_icon(page, 27).click()
    page.locator("#roster-decide-dialog").get_by_role("button", name="Record").click()
    has_text(page.locator("#roster-notice"), STALE_NOTICE)
    assert page.evaluate(MARKED) == "kept"


def test_the_stage_filter_applies_in_place(page, component_origin):
    """Choosing Entered by hand and Apply filters posts the view and redraws
    the list in place: the marked change shows its mark selected, with no
    undo here, who marked it under its stage, and the export follows."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    sent = posts_to(page, component_origin, ENTERED)
    page.get_by_label("Stage").select_option("entered_by_hand")
    page.get_by_role("button", name="Apply filters").click()
    selected = mark_icon(page, 29)
    visible(selected)
    assert "stage=entered_by_hand" in sent[-1]
    assert selected.get_attribute("aria-pressed") == "true"
    assert selected.get_attribute("aria-disabled") == "true"
    assert selected.get_attribute("title") == "Entered by hand."
    assert "Entered by staff@example.org" in row(page, 29).inner_text()
    assert row(page, 29).locator("input[data-select-row]").count() == 0
    assert page.locator("#table-export input[name=stage]").input_value() == (
        "entered_by_hand"
    )
    assert page.get_by_label("Stage").input_value() == "entered_by_hand"
    assert page.evaluate(MARKED) == "kept"


def test_a_held_list_offers_no_mark(page, component_origin):
    """While other campaign work holds the list, it says why, and no row or
    bulk bar offers Entered by hand."""
    page.goto(component_origin + HELD)
    visible(page.get_by_text("Other campaign work is under way"))
    assert page.locator(".table-actions button").count() == 0
    assert page.locator(".bulk-bar").count() == 0


def test_an_empty_list_points_to_another_stage(page, component_origin):
    """With nothing at the chosen Stage, the table says so and where to look,
    in one row spanning every column."""
    page.goto(component_origin + EMPTY)
    cell = page.locator("#table tbody td")
    visible(cell)
    assert cell.inner_text().startswith(
        "No roster changes to enter. A change is listed here"
    )
    columns = page.locator("#table thead th").count()
    assert cell.get_attribute("colspan") == str(columns)


def test_the_request_page_ticks_in_place(page, component_origin):
    """The tick on a resolved request's own page answers in place, and it is
    where a mistaken mark is cleared."""
    page.goto(component_origin + TICK_ITEM)
    page.evaluate(MARK)
    visible(page.get_by_text("Not yet entered in ParishSoft"))
    page.get_by_role("button", name="Mark entered in ParishSoft").click()
    visible(page.get_by_text("Entered in ParishSoft by staff@example.org"))
    visible(page.get_by_role("button", name="Clear Entered in ParishSoft"))
    assert page.evaluate(MARKED) == "kept"
