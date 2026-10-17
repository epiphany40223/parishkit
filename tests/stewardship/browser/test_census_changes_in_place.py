"""Census changes pages, sorts and acts in place (#528, #879).

In Chromium and WebKit. The real page is served by ``census_components``;
each POST from a heading or the navigator is answered with the fixture page
its body asks for. A mark outside every region shows the page never
reloaded.
"""

import pytest

from .census_components import (
    APPROVED,
    AUTOMATIC,
    BULK,
    BULK_APPROVED,
    PATH,
    RESOLVED,
    STALE,
)
from .test_table_sorting import MARK, MARKED, fulfil_post_with, sort_heading
from .waits import has_attribute, has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

ROWS = "#table tbody th[scope=row]"


def test_census_changes_page_and_sort_in_place(page, component_origin):
    """Next shows the second page and a heading re-sorts, both in place,
    with the filters carried in the POST body and never in the address."""
    page.goto(component_origin + "/census-changes")
    page.evaluate(MARK)
    assert page.locator(ROWS).first.inner_text().startswith("Member 00")
    posts = []

    def choose(request):
        """The fixture page the POST body asks for."""
        posts.append(request.post_data)
        if "sort=-submitted" in request.post_data:
            return "/census-changes-newest"
        return "/census-changes-page-2"

    fulfil_post_with(page, "**" + PATH, component_origin, choose)
    page.locator("#table").get_by_role("button", name="Next").first.click()
    has_text(page.locator(ROWS).first, "Member 25")
    assert "page=2" in posts[-1] and "status=open" in posts[-1]
    page.locator("#table").get_by_role(
        "button", name="Submitted (sort descending)"
    ).click()
    has_attribute(sort_heading(page, "submitted"), "aria-sort", "descending")
    has_text(page.locator(ROWS).first, "Member 29")
    assert page.evaluate(MARKED) == "kept"
    assert "status" not in page.url and page.url.endswith("/census-changes")


ROW = "Mobile phone for Member 00"


def focus_row(page):
    """The id of the table row holding the focused control, if any."""
    return page.evaluate("document.activeElement?.closest('tr')?.id || null")


def test_a_resolution_is_recorded_in_place(page, component_origin):
    """A row's Mark entered icon asks in the page's dialog, naming the row,
    posts the dialog's note with the row's fields and the page's filters, and
    the answer redraws the page in place with its notice; focus returns to
    the row (#879)."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    posts = []

    def choose(request):
        """Answer the resolution with the page after it."""
        posts.append(request.post_data)
        return "/census-changes-resolved"

    fulfil_post_with(page, "**" + PATH, component_origin, choose)
    row = page.locator("#table tbody tr").first
    mark = row.get_by_role("button", name=f"Mark {ROW} entered in ParishSoft")
    assert mark.get_attribute("title") == f"Mark {ROW} entered in ParishSoft"
    # No disclosure hides the actions any more.
    assert row.locator("details").count() == 0
    mark.click()
    # It cannot be undone, so the dialog says so, naming the row.
    dialog = page.locator("#census-enter-dialog")
    has_text(dialog.locator("h2"), f"Mark {ROW} entered in ParishSoft?")
    has_text(
        dialog.locator("p.notice"),
        "This cannot be undone. A mistake is corrected in ParishSoft itself.",
    )
    dialog.get_by_label("Note (optional)").fill("Typed it in")
    dialog.get_by_role("button", name="Entered in ParishSoft").click()
    has_text(page.locator("#census-notice"), RESOLVED)
    # The page's own notice is what is announced, never a fixed "Saved."
    has_text(
        page.get_by_role("status").filter(has_text="marked entered in ParishSoft"),
        RESOLVED,
    )
    assert not dialog.is_visible()
    assert focus_row(page) == "change-0"
    sent = posts[-1]
    assert "resolve=entered" in sent and "note=Typed+it+in" in sent
    assert "proposal=0" in sent and "version=1" in sent
    assert "status=open" in sent and "request_key=" in sent
    assert page.evaluate(MARKED) == "kept"


def test_a_stale_resolution_is_announced_as_such(page, component_origin):
    """A row someone else changed first answers with the current page and a
    notice naming the row; that notice, not "Saved.", is announced."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    fulfil_post_with(
        page, "**" + PATH, component_origin, lambda request: "/census-changes-stale"
    )
    row = page.locator("#table tbody tr").first
    row.get_by_role("button", name=f"Ignore {ROW}").click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"Ignore {ROW}?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), STALE)
    announced = page.get_by_role("status").filter(has_text="updated by someone else")
    has_text(announced, STALE)
    assert page.get_by_role("status").filter(has_text="Saved.").count() == 0
    assert page.evaluate(MARKED) == "kept"


def test_cancelling_the_confirmation_sends_nothing(page, component_origin):
    """Cancel in the confirmation dialog posts nothing and returns focus to
    the row's Mark entered icon."""
    page.goto(component_origin + PATH)
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request) if request.method == "POST" else None,
    )
    row = page.locator("#table tbody tr").first
    button = row.get_by_role("button", name=f"Mark {ROW} entered in ParishSoft")
    button.click()
    dialog = page.locator("#census-enter-dialog")
    dialog.get_by_role("button", name="Cancel").click()
    assert not dialog.is_visible()
    assert button.evaluate("node => node === document.activeElement")
    assert posts == []


def test_an_administrators_review_is_recorded_in_place(page, component_origin):
    """An Automatic row's actions (#528, ADM-09) are same-size icons: Edit
    value, Approve and Ignore while it is To review. Approve asks in the
    decisions' dialog, which holds the note and posts it with the row's
    fields and the page's filters, and the answer redraws the page in place
    with its notice announced (#879)."""
    page.goto(component_origin + AUTOMATIC)
    page.evaluate(MARK)
    posts = []

    def choose(request):
        """Answer the review with the page after it."""
        posts.append(request.post_data)
        return "/census-changes-approved"

    fulfil_post_with(page, "**" + AUTOMATIC, component_origin, choose)
    row = page.locator("#table tbody tr").first
    icons = row.locator("td.table-actions button")
    assert [icons.nth(index).get_attribute("aria-label") for index in range(3)] == [
        f"Edit value of {ROW}",
        f"Approve {ROW}",
        f"Ignore {ROW}",
    ]
    # To review: never the tick a By-hand row has, nor Back to To review.
    assert icons.count() == 3
    sizes = [icons.nth(index).bounding_box() for index in range(3)]
    for box in sizes[1:]:
        assert abs(box["width"] - sizes[0]["width"]) <= 1
        assert abs(box["height"] - sizes[0]["height"]) <= 1
    row.get_by_role("button", name=f"Approve {ROW}").click()
    dialog = page.locator("#census-decide-dialog")
    has_text(dialog.locator("h2"), f"Approve {ROW}?")
    dialog.get_by_label("Note (optional)").fill("Checked")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), APPROVED)
    has_text(page.get_by_role("status").filter(has_text="approved."), APPROVED)
    assert not dialog.is_visible()
    sent = posts[-1]
    assert "resolve=approve" in sent and "note=Checked" in sent
    assert "status=open" in sent and "request_key=" in sent
    assert sent.count("note=") == 1
    assert page.evaluate(MARKED) == "kept"


def test_a_bulk_review_is_recorded_in_place(page, component_origin):
    """Ticking Automatic rows enables the decisions they all allow; the
    dialog names the count, posts the ticked rows with its note and the
    page's filters, and the answer redraws the page in place with its
    notice announced (#528, ADM-09). The script never fails meanwhile."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + BULK)
    page.evaluate(MARK)
    posts = []

    def choose(request):
        """Answer the bulk review with the page after it."""
        posts.append(request.post_data)
        return "/census-changes-bulk-approved"

    fulfil_post_with(page, "**" + BULK, component_origin, choose)
    table = page.locator("#table")
    approve = table.get_by_role("button", name="Approve", exact=True)
    back = table.get_by_role("button", name="Back to To review", exact=True)
    hint = table.locator("[data-selection-hint]")
    # Only the three Automatic rows have boxes; nothing is ticked yet.
    boxes = table.locator("input[data-select-row]")
    assert boxes.count() == 3
    assert approve.is_disabled() and back.is_disabled()
    has_text(hint, "Tick Automatic changes on this page to review them together.")
    boxes.nth(0).check()
    boxes.nth(1).check()
    assert approve.is_enabled()
    # They are To review already, so Back to To review does not apply, and
    # the reserved line says why.
    assert back.is_disabled()
    assert hint.inner_text().startswith("Not every ticked change can take that")
    approve.click()
    dialog = page.locator("#census-bulk-dialog")
    has_text(dialog.locator("h2"), "Approve 2 changes?")
    dialog.get_by_label("Note (optional)").fill("Checked")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#census-notice"), BULK_APPROVED)
    has_text(
        page.get_by_role("status").filter(has_text="changes approved"), BULK_APPROVED
    )
    assert not dialog.is_visible()
    sent = posts[-1]
    assert "bulk=approve" in sent and "bulk_note=Checked" in sent
    assert sent.count("change=") == 2 and "status=open" in sent
    assert "bulk_key=" in sent and "resolve=" not in sent
    assert page.evaluate(MARKED) == "kept"
    # The redrawn page carries its own fresh bulk key, so the next bulk
    # review is a new request, not a replay of this one.
    key = page.locator("#census-bulk input[name=bulk_key]").input_value()
    assert key and f"bulk_key={key}" not in sent
    # The note starts empty again the next time the dialog opens.
    table.locator("input[data-select-row]").nth(2).check()
    table.get_by_role("button", name="Ignore", exact=True).click()
    has_text(dialog.locator("h2"), "Ignore Mobile phone for Member 02?")
    assert dialog.get_by_label("Note (optional)").input_value() == ""
    dialog.get_by_role("button", name="Cancel").click()
    assert errors == []
