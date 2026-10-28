"""Sent mail's sending limit note and bulk actions in the browser (#382)."""

from uuid import UUID

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_the_sending_limit_note_names_each_hold_in_local_time(page, component_origin):
    """Above the list, outside its in-place region, linking System health."""
    page.goto(component_origin + "/deliveries-holds")
    note = page.locator("#delivery-holds")
    assert note.is_visible()
    text = note.inner_text()
    assert "daily limit for Family email" in text
    assert "Gmail's own sending limit" in text
    assert "2 Family emails are waiting longer than usual to retry" in text
    # Browser-local times (the fixture browser is in Los Angeles).
    times = note.locator("time[data-local-instant]").all_inner_texts()
    assert len(times) == 2
    assert "5:40" in times[0] and "PDT" in times[0]
    assert "5:15" in times[1] and "PDT" in times[1]
    assert (
        note.get_by_role("link", name="See System health for details").get_attribute(
            "href"
        )
        == "/admin/system/health/"
    )
    # Drawn once per page load: never swapped in or out by an in-place control.
    assert note.evaluate(
        "node => !node.closest('[data-table-region], [data-in-place-region]')"
    )
    # The note precedes the list.
    assert note.evaluate(
        "node => Boolean(node.compareDocumentPosition("
        "document.querySelector('[data-table-region]'))"
        " & Node.DOCUMENT_POSITION_FOLLOWING)"
    )


def test_no_note_while_nothing_is_held(page, component_origin):
    """The ordinary page is unchanged."""
    page.goto(component_origin + "/deliveries")
    assert page.locator("#delivery-holds").count() == 0


# Sent mail's bulk actions (#2296): the shared selection table, whose
# bulk bar's Send again and Record as not sent open the shared review
# dialog. The fixture's rows: a failed email, an uncertain one (each with
# a box of its kind) and a delivered one (no box); the filters match
# 1,250 failed emails in all, so Select all 1,250 matching is offered.
FAILED = "Select Initial invitation for Castellanos, Maximiliana and Bartholomew"
SEND = "Send again"
RECORD = "Record as not sent"
HINT = "#delivery-selection-hint"
# Where the bulk bar's buttons sit on the page (Select all's own label
# changes to Clear selection, in its reserved width).
BAR = """() => [...document.querySelectorAll('#delivery-selection .bulk-bar button')]
    .filter(node => !node.hidden)
    .map(node => { const box = node.getBoundingClientRect();
        return [Math.round(box.x), Math.round(box.y + scrollY)]; })"""


def answer_posts(page, component_origin, answers):
    """Answer each POST to the bulk page with the next fixture answer.

    ``answers`` lists (fixture path, status); returns the POST bodies sent,
    parsed. GETs keep their ordinary fixture.
    """
    from urllib.parse import parse_qs

    from .delivery_components import BULK_ACTION

    bodies = [
        (page.request.get(component_origin + path).text(), status)
        for path, status in answers
    ]
    sent = []

    def handle(route):
        """Fulfil a POST with the next answer; let a GET through."""
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(parse_qs(route.request.post_data))
        body, status = bodies.pop(0)
        route.fulfill(status=status, body=body, content_type="text/html")

    page.route(component_origin + BULK_ACTION + "*", handle)
    return sent


def open_bulk(page, component_origin, suffix=""):
    """Open Sent mail with its bulk actions; marks the window to detect a
    reload and collects script errors."""
    from .delivery_components import BULK_ACTION

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + BULK_ACTION + suffix)
    page.evaluate("window.notReloaded = true")
    return failures


def rows(page):
    """The table's row boxes, in order."""
    return page.locator("#delivery-selection tbody input[data-select-row]")


def hint(page):
    """The bulk bar's reserved reason line's text."""
    return page.locator(HINT).evaluate(
        "node => node.hasAttribute('data-cleared') ? '' : node.textContent"
    )


def test_only_actionable_rows_have_boxes_and_a_mixed_selection_says_why(
    page, component_origin
):
    """Each action fits only its kind of email; the reason line says why
    one is unavailable, and ticking never moves a button."""
    from .waits import eventually

    page.set_viewport_size({"width": 1280, "height": 900})
    failures = open_bulk(page, component_origin)
    send = page.get_by_role("button", name=SEND)
    record = page.get_by_role("button", name=RECORD)
    # The delivered email has no box; the other two do.
    assert rows(page).count() == 2
    assert page.locator("#delivery-selection tbody tr").count() == 3
    eventually(page, f"document.querySelector('{HINT}').textContent.trim() !== ''")
    assert send.is_disabled() and record.is_disabled()
    assert (
        hint(page) == "Select at least one email to send again or record as not sent."
    )
    before = page.evaluate(BAR)
    rows(page).nth(0).check()
    eventually(
        page,
        "!document.querySelector('[data-bulk-action][value=retry_failed]').disabled",
    )
    assert record.is_disabled()
    assert hint(page).startswith("Record as not sent is only for emails marked")
    assert record.get_attribute("aria-describedby") == "delivery-selection-hint"
    rows(page).nth(1).check()
    assert send.is_disabled() and record.is_disabled()
    assert hint(page).startswith(
        "The selected emails mix Failed and Not sure it arrived"
    )
    rows(page).nth(0).uncheck()
    assert send.is_disabled() and record.is_enabled()
    assert hint(page).startswith("Send again is only for failed emails")
    # Nothing in the bar moved while the selection changed.
    assert page.evaluate(BAR) == before
    assert not failures


def test_select_all_matching_selects_every_page(page, component_origin):
    """The toggle ticks the page, counts all 1,250 and fits by their kind;
    unticking a row ends it, and its label and place never change."""
    from .waits import visible

    page.set_viewport_size({"width": 1280, "height": 900})
    sent = answer_posts(page, component_origin, [("/deliveries-bulk-review", 200)])
    failures = open_bulk(page, component_origin)
    toggle = page.get_by_role("button", name="Select all 1,250 matching")
    assert toggle.get_attribute("aria-pressed") == "false"
    before = page.evaluate(BAR)
    toggle.click()
    assert toggle.get_attribute("aria-pressed") == "true"
    assert all(box.is_checked() for box in rows(page).all())
    visible(
        page.locator("#delivery-selection [data-selected-count]").get_by_text(
            "1,250 selected"
        )
    )
    # All 1,250 are failed: Send again fits although the page's uncertain
    # row is ticked too; Record as not sent says why it does not.
    assert page.get_by_role("button", name=SEND).is_enabled()
    assert page.get_by_role("button", name=RECORD).is_disabled()
    assert page.evaluate(BAR) == before
    rows(page).nth(1).uncheck()
    assert toggle.get_attribute("aria-pressed") == "false"
    visible(
        page.locator("#delivery-selection [data-selected-count]").get_by_text(
            "1 selected"
        )
    )
    toggle.click()
    page.get_by_role("button", name=SEND).click()
    visible(page.get_by_role("dialog", name="Send 3 emails again?"))
    # The selection posts the filters for the server to read again.
    assert sent[0]["scope"] == ["matching"]
    assert sent[0]["kind"] == ["retry_failed"]
    assert sent[0]["size"] == ["25"] and sent[0]["page"] == ["1"]
    assert not failures


def test_a_reload_never_keeps_select_all_matching(page, component_origin):
    """The posted scope follows the toggle: after a reload, or a pageshow
    whose browser restored the hidden field, it is "page" with the toggle
    off (#2296)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    failures = open_bulk(page, component_origin)
    scope = page.locator("#delivery-actions input[data-select-scope]")
    toggle = page.get_by_role("button", name="Select all 1,250 matching")
    toggle.click()
    assert scope.input_value() == "matching"
    page.reload()
    assert toggle.get_attribute("aria-pressed") == "false"
    assert scope.input_value() == "page"
    # A field restored without the toggle (Firefox restores form values)
    # follows it again on pageshow.
    page.evaluate(
        """() => {
        document.querySelector("#delivery-actions input[data-select-scope]")
            .value = "matching";
        window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}));
    }"""
    )
    assert scope.input_value() == "page"
    assert toggle.get_attribute("aria-pressed") == "false"
    assert not failures


def test_send_again_reviews_then_applies_in_place(page, component_origin):
    """The review asks for the note; Apply posts the signed preview and
    redraws the page from its answer, with no reload and no jump."""
    from .waits import eventually, visible

    page.set_viewport_size({"width": 1280, "height": 700})
    sent = answer_posts(
        page,
        component_origin,
        [("/deliveries-bulk-review", 200), ("/deliveries-bulk-result", 200)],
    )
    failures = open_bulk(page, component_origin)
    history = page.evaluate("history.length")
    rows(page).nth(0).check()
    page.evaluate("window.scrollTo(0, 120)")
    scrolled = page.evaluate("scrollY")
    page.get_by_role("button", name=SEND).click()
    dialog = page.get_by_role("dialog", name="Send 3 emails again?")
    visible(dialog)
    assert page.evaluate("document.activeElement.id") == "delivery-bulk-review-title"
    assert sent[0]["action"] == ["preview"] and sent[0]["scope"] == ["page"]
    assert sent[0]["delivery"] == [str(UUID(int=22961))]
    visible(dialog.get_by_text("Initial invitation: 2"))
    apply = dialog.get_by_role("button", name="Apply")
    assert apply.is_disabled()
    visible(dialog.get_by_text("Enter the evidence or reason to apply."))
    dialog.get_by_label("Evidence or reason, recorded on every email").fill(
        "Gmail was down from 2 to 3 PM."
    )
    assert apply.is_enabled()
    apply.click()
    eventually(page, "!document.getElementById('delivery-bulk-review').open")
    assert sent[1]["action"] == ["confirm"]
    assert sent[1]["preview"] == ["signed-preview"]
    assert sent[1]["note"] == ["Gmail was down from 2 to 3 PM."]
    assert "checked" not in sent[1] and sent[1]["sort"] == ["-changed"]
    outcome = page.locator("#delivery-bulk-outcome")
    eventually(
        page,
        "document.getElementById('delivery-bulk-outcome').textContent"
        ".startsWith('Sent 100 emails again. 2 skipped. 148 left. Applied at')",
    )
    assert outcome.is_visible()
    notice = page.locator("#delivery-bulk-notice")
    assert "Sent 100 of 250 failed emails again." in notice.inner_text()
    assert "2 emails were skipped" in notice.inner_text()
    visible(notice.get_by_role("button", name="Continue"))
    # Redrawn in place: the list shows the email queued, nothing reloaded,
    # no history entry was added and the page did not jump to the top.
    assert "Still sending (queued)" in page.locator("[data-table-region]").inner_text()
    assert page.evaluate("window.notReloaded") is True
    assert page.evaluate("history.length") == history
    assert abs(page.evaluate("scrollY") - scrolled) < 2
    assert not failures


def test_record_as_not_sent_needs_the_records_check(page, component_origin):
    """Apply waits for the note and the "I checked" box, and posts both."""
    from .waits import visible

    sent = answer_posts(
        page,
        component_origin,
        [("/deliveries-bulk-review-unsent", 200), ("/deliveries-bulk-result", 200)],
    )
    open_bulk(page, component_origin)
    rows(page).nth(1).check()
    page.get_by_role("button", name=RECORD).click()
    dialog = page.get_by_role("dialog", name="Record 1 email as not sent?")
    visible(dialog)
    apply = dialog.get_by_role("button", name="Apply")
    dialog.get_by_label("Evidence or reason, recorded on every email").fill(
        "Sent folder shows none went out."
    )
    assert apply.is_disabled()
    visible(
        dialog.get_by_text(
            "Confirm that you checked the mail service records to apply."
        )
    )
    dialog.get_by_role("checkbox").check()
    assert apply.is_enabled()
    apply.click()
    page.get_by_role("button", name="Continue").wait_for()
    assert sent[1]["checked"] == ["yes"]
    assert sent[1]["preview"] == ["signed-unsent"]


def test_continue_reviews_the_rest_with_the_same_note(page, component_origin):
    """Continue opens the same dialog, its note shown, Apply ready."""
    from .waits import visible

    sent = answer_posts(page, component_origin, [("/deliveries-bulk-continue", 200)])
    open_bulk(page, component_origin, "-result")
    page.get_by_role("button", name="Continue").click()
    dialog = page.get_by_role("dialog", name="Send 148 more emails again?")
    visible(dialog)
    visible(dialog.get_by_text("Gmail was down from 2 to 3 PM."))
    assert dialog.get_by_role("button", name="Apply").is_enabled()
    assert sent[0]["action"] == ["continue"]
    assert sent[0]["preview"] == ["signed-continue"]
    assert sent[0]["note"] == ["Gmail was down from 2 to 3 PM."]


def test_a_refused_review_is_shown_in_the_dialog(page, component_origin):
    """A changed selection or an expired review says so where the reader
    is; Cancel leaves the page as it was."""
    from .waits import hidden, visible

    answer_posts(page, component_origin, [("/deliveries-bulk-refused", 409)])
    open_bulk(page, component_origin)
    rows(page).nth(0).check()
    page.get_by_role("button", name=SEND).click()
    dialog = page.locator("#delivery-bulk-review")
    visible(dialog.get_by_text("Some of the selected emails changed"))
    hidden(dialog.get_by_role("button", name="Apply"))
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert rows(page).nth(0).is_checked()
    assert page.evaluate("window.notReloaded") is True
