"""Outgoing mail's sending limit note and bulk actions in the browser (#382)."""

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


def answer_posts(page, component_origin, answers):
    """Answer each POST to the bulk page with the next fixture page.

    ``answers`` lists (fixture path, status); returns the POST bodies sent,
    parsed. GETs keep their ordinary fixture.
    """
    from urllib.parse import parse_qs

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

    page.route(component_origin + "/deliveries-bulk*", handle)
    return sent


def open_panel(page, component_origin):
    """Open Outgoing mail's bulk panel; marks the window to detect a reload."""
    page.goto(component_origin + "/deliveries-bulk")
    page.evaluate("window.notReloaded = true")
    page.locator("#bulk-panel summary").click()


def test_preview_waits_for_the_note_and_the_records_check(page, component_origin):
    """Preview is unavailable until every required field is filled in."""
    open_panel(page, component_origin)
    retry = page.locator("#bulk-retry_failed")
    preview = retry.get_by_role("button", name="Preview retry")
    assert preview.is_disabled()
    # Only the email types the action covers, with All types first.
    options = retry.locator("select[name=purpose] option").all_inner_texts()
    assert options == [
        "All types (3)",
        "Initial invitation (2)",
        "Submission receipt (1)",
    ]
    retry.locator("textarea[name=note]").fill("   ")
    assert preview.is_disabled()
    retry.locator("textarea[name=note]").fill("Gmail was down from 2 to 3 PM.")
    assert preview.is_enabled()
    unsent = page.locator("#bulk-confirm_unsent")
    record = unsent.get_by_role("button", name="Preview recording as not sent")
    unsent.locator("textarea[name=note]").fill("Sent folder shows none went out.")
    assert record.is_disabled()
    unsent.get_by_role("checkbox").check()
    assert record.is_enabled()
    # One type only: no All types choice.
    assert unsent.locator("select[name=purpose] option").all_inner_texts() == [
        "Reminder (1)"
    ]


def test_preview_and_confirm_happen_in_place(page, component_origin):
    """No reload, no jump; the typed note stays; Confirm sends the preview."""
    from playwright.sync_api import expect

    sent = answer_posts(
        page,
        component_origin,
        [("/deliveries-bulk-review", 200), ("/deliveries-bulk-result", 200)],
    )
    open_panel(page, component_origin)
    retry = page.locator("#bulk-retry_failed")
    retry.locator("textarea[name=note]").fill("Gmail was down from 2 to 3 PM.")
    button = retry.get_by_role("button", name="Preview retry")
    # Its place on the page (the review may scroll into view below it).
    top = "node => node.getBoundingClientRect().top + window.scrollY"
    before = button.evaluate(top)
    button.click()
    heading = page.get_by_role("heading", name="Retry 3 failed emails?")
    expect(heading).to_be_focused()
    assert page.evaluate("window.notReloaded") is True
    # Nothing above the review moved, and the typing is kept.
    assert button.evaluate(top) == before
    assert retry.locator("textarea[name=note]").input_value() == (
        "Gmail was down from 2 to 3 PM."
    )
    assert sent[0] == {
        "csrfmiddlewaretoken": ["a" * 64],
        "action": ["preview"],
        "kind": ["retry_failed"],
        "purpose": ["all"],
        "note": ["Gmail was down from 2 to 3 PM."],
    }
    page.get_by_role("button", name="Retry 3 emails").click()
    expect(page.get_by_role("heading", name="Partly done")).to_be_focused()
    assert page.evaluate("window.notReloaded") is True
    assert sent[1] == {
        "csrfmiddlewaretoken": ["a" * 64],
        "action": ["confirm"],
        "preview": ["signed-preview"],
        "note": ["Gmail was down from 2 to 3 PM."],
    }
    region = page.locator("#bulk-review")
    assert "Retried 100 of 250 failed emails." in region.inner_text()
    assert "2 emails were skipped" in region.inner_text()
    assert region.get_by_role("button", name="Continue").is_visible()
    # Continue posts the re-signed preview, which carries what was skipped.
    assert region.locator("input[name=preview]").input_value() == "signed-continue"
    assert region.locator("input[name=note]").input_value() == (
        "Gmail was down from 2 to 3 PM."
    )
    # The list was refreshed in place too.
    assert "Still sending (queued)" in page.locator("[data-table-region]").inner_text()


def test_editing_the_form_withdraws_its_preview(page, component_origin):
    """Confirm can only ever apply what the preview shows."""
    from playwright.sync_api import expect

    answer_posts(page, component_origin, [("/deliveries-bulk-review", 200)])
    open_panel(page, component_origin)
    retry = page.locator("#bulk-retry_failed")
    retry.locator("textarea[name=note]").fill("Gmail was down from 2 to 3 PM.")
    retry.get_by_role("button", name="Preview retry").click()
    expect(page.get_by_role("button", name="Retry 3 emails")).to_be_visible()
    retry.locator("select[name=purpose]").select_option("receipt")
    expect(page.get_by_role("button", name="Retry 3 emails")).to_have_count(0)
    expect(page.locator("#bulk-review [data-review-note]")).to_have_text(
        "You changed the form after previewing it. "
        "Choose Preview again to see what it covers now."
    )


def test_a_refused_confirmation_is_shown_in_place(page, component_origin):
    """An expired or changed preview says so where the reader is."""
    from playwright.sync_api import expect

    answer_posts(
        page,
        component_origin,
        [("/deliveries-bulk-review", 200), ("/deliveries-bulk-refused", 409)],
    )
    open_panel(page, component_origin)
    retry = page.locator("#bulk-retry_failed")
    retry.locator("textarea[name=note]").fill("Gmail was down from 2 to 3 PM.")
    retry.get_by_role("button", name="Preview retry").click()
    page.get_by_role("button", name="Retry 3 emails").click()
    summary = page.locator("#bulk-review [data-error-summary]")
    expect(summary).to_be_focused()
    assert "Choose Preview again" in summary.inner_text()
    assert page.evaluate("window.notReloaded") is True
