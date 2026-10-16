"""Census changes pages and sorts in place (#528), in Chromium.

The real page is served by ``census_components``; each POST from a heading
or the navigator is answered with the fixture page its body asks for. A mark
outside every region shows the page never reloaded.
"""

import pytest

from .census_components import APPROVED, AUTOMATIC, PATH, RESOLVED, STALE
from .test_table_sorting import MARK, MARKED, fulfil_post_with, sort_heading
from .waits import has_attribute, has_text

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

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


def test_a_resolution_is_recorded_in_place(page, component_origin):
    """A row's Entered in ParishSoft posts its note with the page's filters
    and the answer refreshes the page in place, with its notice."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    posts = []

    def choose(request):
        """Answer the resolution with the page after it."""
        posts.append(request.post_data)
        return "/census-changes-resolved"

    fulfil_post_with(page, "**" + PATH, component_origin, choose)
    record = page.locator("details.census-record").first
    # The form stays folded away, keeping the row short, until it is opened.
    assert not record.locator("form.census-resolve").is_visible()
    record.locator("summary").click()
    form = record.locator("form.census-resolve")
    form.get_by_label("Note (optional)").fill("Typed it in")
    form.get_by_role("button", name="Entered in ParishSoft").click()
    # It cannot be undone, so it asks first, naming the row.
    dialog = page.locator("#census-enter-confirm")
    has_text(
        dialog.locator("h2"), "Mark Mobile phone for Member 00 entered in ParishSoft?"
    )
    dialog.get_by_role("button", name="Entered in ParishSoft").click()
    has_text(page.locator("#census-notice"), RESOLVED)
    # The page's own notice is what is announced, never a fixed "Saved."
    has_text(
        page.get_by_role("status").filter(has_text="marked entered in ParishSoft"),
        RESOLVED,
    )
    sent = posts[-1]
    assert "resolve=entered" in sent and "note=Typed+it+in" in sent
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
    record = page.locator("details.census-record").first
    record.locator("summary").click()
    record.get_by_role("button", name="Ignore").click()
    has_text(page.locator("#census-notice"), STALE)
    announced = page.get_by_role("status").filter(has_text="updated by someone else")
    has_text(announced, STALE)
    assert page.get_by_role("status").filter(has_text="Saved.").count() == 0
    assert page.evaluate(MARKED) == "kept"


def test_cancelling_the_confirmation_sends_nothing(page, component_origin):
    """Cancel in the confirmation dialog posts nothing and returns focus to
    the row's Entered in ParishSoft button."""
    page.goto(component_origin + PATH)
    posts = []
    page.on(
        "request",
        lambda request: posts.append(request) if request.method == "POST" else None,
    )
    record = page.locator("details.census-record").first
    record.locator("summary").click()
    button = record.get_by_role("button", name="Entered in ParishSoft")
    button.click()
    dialog = page.locator("#census-enter-confirm")
    dialog.get_by_role("button", name="Cancel").click()
    assert not dialog.is_visible()
    assert button.evaluate("node => node === document.activeElement")
    assert posts == []


def test_an_administrators_review_is_recorded_in_place(page, component_origin):
    """An Automatic row's Review (#528, ADM-09) folds away like Record
    outcome, posts Approve with its note and the page's filters without
    asking first (it is reversible), and the answer refreshes the page in
    place with its notice announced."""
    page.goto(component_origin + AUTOMATIC)
    page.evaluate(MARK)
    posts = []

    def choose(request):
        """Answer the review with the page after it."""
        posts.append(request.post_data)
        return "/census-changes-approved"

    fulfil_post_with(page, "**" + AUTOMATIC, component_origin, choose)
    review = page.locator("details.census-record").first
    assert review.locator("summary").inner_text() == "Review"
    assert not review.locator("form.census-resolve").is_visible()
    review.locator("summary").click()
    form = review.locator("form.census-resolve")
    # To review: Approve or Ignore, never the tick a By-hand row has.
    assert form.get_by_role("button", name="Entered in ParishSoft").count() == 0
    assert form.get_by_role("button", name="Back to To review").count() == 0
    form.get_by_label("Note (optional)").fill("Checked")
    form.get_by_role("button", name="Approve").click()
    has_text(page.locator("#census-notice"), APPROVED)
    has_text(page.get_by_role("status").filter(has_text="approved."), APPROVED)
    assert not page.locator("#census-enter-confirm").is_visible()
    sent = posts[-1]
    assert "resolve=approve" in sent and "note=Checked" in sent
    assert "status=open" in sent and "request_key=" in sent
    assert page.evaluate(MARKED) == "kept"
