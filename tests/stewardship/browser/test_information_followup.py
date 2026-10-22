"""Additional information on the review worklist decides in place (#1685).

In Chromium and WebKit. The real page is served by
``information_components``; each POST is answered with the fixture page
after it. A mark outside every region shows the page never reloaded. The
shared worklist's general behaviour is tested on its own fixture page
(``test_review_worklist``); these tests check what Additional information
adds: its columns and filters, Accept to To handle and then Handled, the
Ignored request's optional reopening note (#1889), Handled's undo with its
note (#1840), the
export pane below the table, and a request's own page.
"""

from uuid import UUID

import pytest
from django.urls import reverse

from .information_components import (
    ACCEPTED,
    ACCEPTED_NOTICE,
    BULK_NOTICE,
    BULKED,
    HANDLED,
    HANDLED_NOTICE,
    IGNORED,
    PATH,
    REOPENED,
    REOPENED_NOTICE,
    STALE,
    STALE_NOTICE,
    UNDONE,
    UNDONE_NOTICE,
)
from .test_bulk_bar import assert_accessible
from .test_table_sorting import MARK, MARKED, fulfil_post_with
from .waits import available, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


def name(index):
    """How the page names row ``index``'s request."""
    return f"the request from Family {index:02d}"


def row(page, index):
    """Row ``index`` of the table, by its request id."""
    return page.locator(f"#information-row-{UUID(int=200 + index)}")


def icon(page, index, label, after=""):
    """Row ``index``'s icon named ``label``, the request and then ``after``."""
    title = f"{label} {name(index)}{after}"
    return row(page, index).get_by_role("button", name=title, exact=True)


def posts(page, origin, answer):
    """Answer every POST to the page with the fixture ``answer``; the bodies."""
    sent = []

    def choose(request):
        """Keep the body and answer with the fixture page."""
        sent.append(request.post_data)
        return answer

    fulfil_post_with(page, "**" + PATH, origin, choose)
    return sent


@pytest.mark.parametrize("width", [320, 1280])
def test_the_worklist_and_a_request_fit_with_no_wcag_violation(
    page, component_origin, axe_source, width
):
    """The worklist (filters open, Stage first, export pane below), a
    request's page and its error page keep the phone width without a page
    scroll and have no WCAG violation."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/information-all")
    filters = page.locator("#table-filters")
    assert filters.locator("xpath=ancestor::details").count() == 0
    assert filters.locator("label").first.inner_text() == "Stage"
    export = page.locator("#table-export").bounding_box()
    assert export["y"] > page.locator(".table-scroll").bounding_box()["y"]
    headings = page.locator(".table-scroll thead th").all_inner_texts()
    assert "Reviewer's value" not in headings and "Baseline" not in headings
    assert page.locator(".stat-tiles, .chart").count() == 0
    for path in ("/information-all", "/information-item", "/information-refused"):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert_accessible(page, axe_source)


def test_a_request_page_shows_its_stage_contacts_and_history(page, component_origin):
    """A request's own page has its text, Stage, contact details and history
    worded by decision, and no follow-up form: decisions are on the list."""
    page.goto(component_origin + "/information-item")
    contact = page.locator("#information-contact")
    visible(contact.get_by_role("heading", name="Contact details"))
    visible(contact.get_by_role("heading", name="Anna Example"))
    visible(contact.get_by_text("Home: +1 (202) 555-0123"))
    visible(contact.get_by_text("None recorded"))
    visible(page.get_by_role("heading", name="Version 2: Handled"))
    # This request was replaced by a later one, so it is History.
    has_text(page.locator(".stage-chip .status-word"), "History")
    assert page.locator("textarea, input[type=checkbox]").count() == 0
    visible(page.get_by_role("link", name="Open the replacement request"))
    # The submitted text is text, never markup.
    assert page.locator("our").count() == 0
    page.goto(component_origin + "/information-unresolved")
    visible(
        page.get_by_text(
            "This change is visible to the weekly digest correction workflow; "
            "the old request is no longer actionable.",
            exact=True,
        )
    )


def test_accept_then_handled_in_place(page, component_origin):
    """Accept asks in the shared dialog and posts the row's decision with the
    page's view; the redrawn row is To handle with Accept selected and
    Handled offered, and Handled then marks it, all with no reload."""
    page.goto(component_origin + "/information-all")
    page.evaluate(MARK)
    sent = posts(page, component_origin, ACCEPTED)
    actions = [
        node.get_attribute("data-row-action")
        for node in row(page, 0).locator(".table-actions button").all()
    ]
    assert actions == ["accept", "ignore"]
    icon(page, 0, "Accept").click()
    dialog = page.locator("#information-decide-dialog")
    has_text(dialog.locator("h2"), f"Accept {name(0)}?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#information-notice"), ACCEPTED_NOTICE)
    assert icon(page, 0, "Accept").get_attribute("aria-pressed") == "true"
    has_text(row(page, 0).locator(".stage-chip .status-word"), "To handle")
    body = sent[-1]
    assert "decide=accept" in body and "version=1" in body and "stage=all" in body
    assert page.evaluate(MARKED) == "kept"
    sent = posts(page, component_origin, HANDLED)
    icon(page, 0, "Mark", " handled").click()
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Record that this was handled. Back to review undoes it.",
    )
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#information-notice"), HANDLED_NOTICE)
    assert "decide=handled" in sent[-1] and "version=2" in sent[-1]
    has_text(row(page, 0).locator(".stage-chip .status-word"), "Handled")
    assert page.evaluate(MARKED) == "kept"


def test_back_to_review_undoes_handled_with_a_note(page, component_origin):
    """A Handled request offers Back to review (#1840); its dialog says the
    request goes back to To handle with its decision kept and waits for the
    required note, then the redrawn row is To handle, with no reload."""
    page.goto(component_origin + HANDLED)
    page.evaluate(MARK)
    sent = posts(page, component_origin, UNDONE)
    actions = [
        node.get_attribute("data-row-action")
        for node in row(page, 0).locator(".table-actions button").all()
    ]
    assert actions == ["accept", "back_to_review", "handled"]
    icon(page, 0, "Send", " back to review").click()
    dialog = page.locator("#information-decide-dialog")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Handled is undone: the item goes back to To handle, with its decision "
        "kept. Say why.",
    )
    record = dialog.get_by_role("button", name="Record")
    assert record.get_attribute("aria-disabled") == "true" or record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Not called yet")
    available(record)
    record.click()
    has_text(page.locator("#information-notice"), UNDONE_NOTICE)
    body = sent[-1]
    assert "decide=back_to_review" in body and "version=2" in body
    assert "note=Not+called+yet" in body
    has_text(row(page, 0).locator(".stage-chip .status-word"), "To handle")
    assert page.evaluate(MARKED) == "kept"


def test_reopening_an_ignored_request_takes_an_optional_note(page, component_origin):
    """Back to review on an Ignored request may record at once (#1889); a
    note written is posted."""
    page.goto(component_origin + IGNORED)
    sent = posts(page, component_origin, REOPENED)
    icon(page, 1, "Send", " back to review").click()
    dialog = page.locator("#information-decide-dialog")
    record = dialog.get_by_role("button", name="Record")
    available(record)
    dialog.get_by_label("Note (optional)").fill("Misread")
    available(record)
    record.click()
    has_text(page.locator("#information-notice"), REOPENED_NOTICE)
    assert "decide=back_to_review" in sent[-1] and "note=Misread" in sent[-1]


def test_bulk_accept_and_a_stale_decision(page, component_origin):
    """The bulk bar accepts every ticked request, counted in its dialog; a
    request someone else changed first answers with the notice that says so."""
    page.goto(component_origin + "/information-all")
    sent = posts(page, component_origin, BULKED)
    page.get_by_label(f"Select {name(0)}").check()
    page.get_by_label(f"Select {name(1)}").check()
    accept = page.locator(".bulk-bar").get_by_role("button", name="Accept", exact=True)
    available(accept)
    accept.click()
    dialog = page.locator("#information-bulk-dialog")
    has_text(dialog.locator("h2"), "Accept 2 requests?")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#information-notice"), BULK_NOTICE)
    assert "bulk=accept" in sent[-1] and sent[-1].count("item=") == 2
    page.goto(component_origin + "/information-all")
    posts(page, component_origin, STALE)
    icon(page, 0, "Ignore").click()
    page.locator("#information-decide-dialog").get_by_role(
        "button", name="Record"
    ).click()
    has_text(page.locator("#information-notice"), STALE_NOTICE)


def test_the_export_pane_posts_the_view_privately(page, component_origin):
    """The complete export, below the table, posts the worklist's view in its
    body: the Stage as its follow-up choices, never in an address."""
    page.goto(component_origin + "/information")
    # The export row (#1679) asks no zone: the browser's goes unasked.
    assert page.get_by_label("Export timezone", exact=True).count() == 0
    page.get_by_label("Include complete Staff workflow history").check()
    page.route(
        "**" + reverse("admin:information_export"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Export XLSX").click()
    body = sent.value.post_data
    assert "disposition=current_actionable" in body
    assert "needed=no" in body and "completed=no" in body
    assert "format=xlsx" in body and "history=yes" in body
    assert "browser_timezone=America%2FLos_Angeles" in body
    assert "?" not in sent.value.url


def test_a_held_campaign_offers_no_decision(page, component_origin):
    """While other campaign work holds the campaign, rows offer no icon and
    the export waits, with the reason shown."""
    page.goto(component_origin + "/information-queue-gated")
    visible(page.get_by_text("Other campaign work", exact=False))
    assert page.locator("[data-row-action]").count() == 0
    assert page.get_by_role("button", name="Export XLSX").is_disabled()
