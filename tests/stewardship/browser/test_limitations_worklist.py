"""Limitations on the review worklist decides in place (#1685).

In Chromium and WebKit. The real page is served by ``limitations_components``;
each POST is answered with the fixture page after it. A mark outside every
region shows the page never reloaded. The shared worklist's general
behaviour is tested on its own fixture page (``test_review_worklist``);
these tests check what Limitations adds: one table of Members' and
Families' answers, its Show filter, its download below the table, and
Handled's undo with its required note (#1840).
"""

import pytest

from .limitations_components import (
    ACCEPTED,
    ACCEPTED_NOTICE,
    CONTRIBUTION,
    HANDLED,
    PATH,
    PLEDGES_LINK,
    UNDONE,
    UNDONE_NOTICE,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

FAMILY = "the answer of Baker"


def test_one_table_lists_members_and_families(page, component_origin):
    """The filters are open above the table, Stage first then Search and
    Show; one table holds each Member's and each Family's answer, in words,
    and the download sits below it."""
    page.goto(component_origin + "/limitations-report")
    filters = page.locator("#table-filters")
    assert filters.locator("xpath=ancestor::details").count() == 0
    assert filters.locator("label").all_inner_texts() == [
        "Stage",
        "Search name or Family DUID",
        "Show",
    ]
    assert page.locator(".table-scroll table").count() == 1
    answers = page.locator("#table tbody tr td").all_inner_texts()
    assert "Cannot participate in any Ministries" in answers
    assert "Cannot attend Mass or prayer services" in answers
    # A Member added on the form shows New Member under its name, where a
    # Member DUID would be (#2124).
    assert any(answer.endswith("New Member") for answer in answers)
    export = page.locator("#table-export").bounding_box()
    assert export["y"] > page.locator(".table-scroll").bounding_box()["y"]
    assert page.locator(".stat-tiles").count() == 0


def test_accept_a_familys_answer_in_place(page, component_origin):
    """Accept asks in the shared dialog and posts the answer's item and
    version with the page's view; the redrawn row is To handle with Accept
    selected and Handled offered, with no reload."""
    page.goto(component_origin + "/limitations-report")
    page.evaluate(MARK)
    sent = []

    def choose(request):
        """Keep the body and answer with the page after the decision."""
        sent.append(request.post_data)
        return ACCEPTED

    fulfil_post_with(page, "**" + PATH, component_origin, choose)
    page.get_by_role("button", name=f"Accept {FAMILY}", exact=True).click()
    dialog = page.locator("#limitations-decide-dialog")
    has_text(dialog.locator("h2"), f"Accept {FAMILY}?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#limitations-notice"), ACCEPTED_NOTICE)
    row = page.locator("#limitations-row-f2")
    # Pressed, its name says choosing it again sends it back (#2131).
    accept = row.locator('[data-row-action="accept"]')
    assert accept.get_attribute("aria-pressed") == "true"
    assert accept.get_attribute("aria-label") == (
        f"Accepted; select to send {FAMILY} back to review"
    )
    has_text(row.locator(".stage-chip .status-word"), "To handle")
    assert row.locator('[data-row-action="handled"]').count() == 1
    body = sent[-1]
    assert "decide=accept" in body and "item=f2" in body and "version=0" in body
    assert "show=any" in body and "stage=all" in body
    assert page.evaluate(MARKED) == "kept"


def test_back_to_review_undoes_a_handled_answer(page, component_origin):
    """A Handled answer offers Back to review (#1840), its pressed Handled
    chosen again (#2131); its dialog says the
    answer goes back to To handle with its decision kept and waits for the
    required note, then the redrawn row is To handle, with no reload."""
    page.goto(component_origin + HANDLED)
    page.evaluate(MARK)
    sent = []

    def choose(request):
        """Keep the body and answer with the page after the undo."""
        sent.append(request.post_data)
        return UNDONE

    fulfil_post_with(page, "**" + PATH, component_origin, choose)
    row = page.locator("#limitations-row-f2")
    has_text(row.locator(".stage-chip .status-word"), "Handled")
    handled = row.locator('[data-row-action="handled"]')
    assert handled.get_attribute("aria-pressed") == "true"
    assert row.locator('[data-row-action="back_to_review"]').count() == 0
    handled.click()
    dialog = page.locator("#limitations-decide-dialog")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Handled is undone: the item goes back to To handle, with its decision "
        "kept. Say why.",
    )
    record = dialog.get_by_role("button", name="Record")
    assert record.get_attribute("aria-disabled") == "true" or record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Not yet")
    available(record)
    record.click()
    has_text(page.locator("#limitations-notice"), UNDONE_NOTICE)
    body = sent[-1]
    assert "decide=back_to_review" in body and "item=f2" in body
    assert "version=1" in body and "note=Not+yet" in body
    has_text(row.locator(".stage-chip .status-word"), "To handle")
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [320, 1280])
def test_the_page_fits_and_has_no_wcag_violation(
    page, component_origin, axe_source, width
):
    """The worklist keeps the phone width without a page scroll and has no
    WCAG violation."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/limitations-report")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert_accessible(page, axe_source)


def test_cannot_contribute_is_listed_but_decided_on_pledges(page, component_origin):
    """A Family's Cannot contribute financially answer is a row with its
    Pledges stage and a link to Pledges, and no box or icon (R39 finding
    11): it is decided once, on Pledges."""
    page.goto(component_origin + CONTRIBUTION)
    row = page.locator("#table tbody tr").filter(has_text="Dalton")
    assert "Cannot contribute financially" in row.inner_text()
    assert "To review" in row.inner_text()
    link = row.get_by_role("link", name="Open in Pledges: the answer of Dalton")
    assert link.get_attribute("href") == PLEDGES_LINK
    assert row.locator("input[type=checkbox]").count() == 0
    assert row.locator("[data-row-action]").count() == 0
