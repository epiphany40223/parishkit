"""Native private Ministry follow-up queue and edit form, with no assignment."""

import pytest

from .waits import has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def no_assignment(page):
    """Follow-up has no assignment (#552): no Select column, Assign panel,
    assignee filter, column or field."""
    assert page.get_by_role("columnheader", name="Select").count() == 0
    assert page.get_by_role("columnheader", name="Assigned to").count() == 0
    assert page.get_by_role("button", name="Assign selected").count() == 0
    assert page.get_by_label("Assigned to").count() == 0
    assert page.get_by_label("Assign to").count() == 0
    assert page.locator("#followup-assign-panel, [name=assignee]").count() == 0


PAGES = (
    "/followup-queue",
    "/followup-all",
    "/followup-empty",
    "/followup-gated",
    "/followup-item",
    "/followup-closed",
    "/followup-item-gated",
    "/followup-item-leave",
    "/followup-item-refused",
    "/followup-error-400",
    "/followup-error-409",
    "/followup-error-503",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_followup_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Every state stays readable, escaped and operable without a JavaScript UI."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/followup-queue")
    assert page.get_by_text("Example <Member>", exact=True).count() == 1
    no_assignment(page)
    search = page.get_by_label("Search Member or Ministry name")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "ministry"
    page.goto(component_origin + "/followup-all")
    no_assignment(page)
    page.goto(component_origin + "/followup-item")
    assert page.get_by_text("Left a <private> voicemail", exact=True).count() == 2
    assert page.get_by_text("No <answer>", exact=False).count() == 1
    no_assignment(page)
    assert page.get_by_label("Status").input_value() == "new"
    choices = page.get_by_label("Status").locator("option").all_inner_texts()
    assert choices == ["New", "In progress", "Resolved", "Closed: no response"]
    # A past edit's recorded assignment stays readable in history.
    visible(page.get_by_text("assigned to leader@example.org", exact=False))
    # Closed outcomes are permanent: history remains, the form does not.
    page.goto(component_origin + "/followup-closed")
    assert page.get_by_role("button", name="Save follow-up").count() == 0
    visible(page.get_by_text("Closed outcomes are permanent", exact=False))
    page.goto(component_origin + "/followup-item-gated")
    assert page.get_by_role("button", name="Save follow-up").is_disabled()


def test_followup_form_shows_only_the_fields_that_apply(page, component_origin):
    """Outcome appears only for Resolved and the contact details only once a
    channel is chosen; hidden fields are disabled, so they are not sent."""
    page.goto(component_origin + "/followup-item")
    outcome = page.get_by_label("Outcome", exact=True)
    date, time = page.get_by_label("Date (UTC)"), page.get_by_label("Time (UTC)")
    happened = page.get_by_label("What happened")
    status, how = page.get_by_label("Status"), page.get_by_label("How")
    # The request loads as New with no contact attempt chosen.
    for field in (outcome, date, time, happened):
        assert not field.is_visible() and field.is_disabled()
    visible(page.get_by_label("Notes (optional; needed only for the outcome Other)"))
    status.select_option("resolved")
    visible(outcome)
    assert outcome.is_enabled()
    status.select_option("closed_no_response")
    assert not outcome.is_visible() and outcome.is_disabled()
    how.select_option("phone")
    for field in (date, time, happened):
        visible(field)
        assert field.is_enabled()
    how.select_option("")
    for field in (date, time, happened):
        assert not field.is_visible() and field.is_disabled()
    # Only the fields that apply are sent.
    status.select_option("in_progress")
    page.route("**/update", lambda route: route.fulfill(body="Saved"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Save follow-up").click()
    body = sent.value.post_data
    assert "state=in_progress" in body and "notes=" in body
    assert "contact_channel=" in body
    for name in ("outcome=", "contact_date=", "contact_time=", "contact_notes="):
        assert name not in body


def test_followup_save_waits_for_the_visible_required_fields(page, component_origin):
    """Save stays disabled, saying why, until every shown required field is
    filled: the outcome for Resolved, notes for Other, and a contact's date
    and time once a channel is chosen."""
    page.goto(component_origin + "/followup-item")
    save = page.get_by_role("button", name="Save follow-up")
    hint = page.locator("#followup-save-hint")
    assert save.is_enabled() and not hint.is_visible()
    assert save.get_attribute("aria-describedby") == "followup-save-hint"
    page.get_by_label("Status").select_option("resolved")
    assert save.is_disabled()
    has_text(hint, "Choose an outcome to save.")
    page.get_by_label("Outcome", exact=True).select_option("joined")
    assert save.is_enabled() and not hint.is_visible()
    page.get_by_label("Outcome", exact=True).select_option("other")
    notes = page.get_by_label("Notes (optional; needed only for the outcome Other)")
    notes.fill("")
    assert save.is_disabled()
    has_text(hint, "Add notes to save: the outcome Other needs them.")
    notes.fill("Moved to another parish")
    assert save.is_enabled()
    page.get_by_label("How").select_option("phone")
    assert save.is_disabled()
    has_text(hint, "Enter the date and time of the contact attempt to save.")
    page.get_by_label("Date (UTC)").fill("2026-09-19")
    assert save.is_disabled()
    page.get_by_label("Time (UTC)").fill("15:04")
    assert save.is_enabled() and not hint.is_visible()
    # Choosing no contact attempt drops that requirement again.
    page.get_by_label("Date (UTC)").fill("")
    assert save.is_disabled()
    page.get_by_label("How").select_option("")
    assert save.is_enabled()
    # A read-only request's Save, disabled by the server, stays disabled.
    page.goto(component_origin + "/followup-item-gated")
    page.evaluate("window.dispatchEvent(new Event('pageshow'))")
    assert page.get_by_role("button", name="Save follow-up").is_disabled()


def test_followup_history_links_name_the_request(page, component_origin):
    """History paging links carry the request's own address, so they still
    work on a page re-rendered at the update URL after a refused save."""
    page.goto(component_origin + "/followup-item")
    href = page.get_by_role("link", name="Older history").get_attribute("href")
    assert href == (
        "/admin/reports/00000000-0000-0000-0000-00000000005c/ministries/"
        "follow-up/00000000-0000-0000-0000-00000000005d/?page=2"
    )


def test_followup_outcomes_match_the_request_kind(page, component_origin):
    """A join is never offered Left ministry, nor a leave Joined ministry, so
    Save can never pass an outcome the server refuses."""
    for path, offered, absent in (
        ("/followup-item", "Joined ministry", "Left ministry"),
        ("/followup-item-leave", "Left ministry", "Joined ministry"),
    ):
        page.goto(component_origin + path)
        page.get_by_label("Status").select_option("resolved")
        choices = (
            page.get_by_label("Outcome", exact=True).locator("option").all_inner_texts()
        )
        assert offered in choices and absent not in choices
        assert "No response" not in choices


def test_followup_refusal_is_shown_in_place(page, component_origin):
    """A refused save shows the same form with a summary of the one problem,
    focused, and the submitted values kept and their fields shown."""
    page.goto(component_origin + "/followup-item-refused")
    summary = page.locator("[data-error-summary]")
    visible(summary)
    has_text(
        summary.get_by_role("link"), "Left ministry doesn't apply to a request to join."
    )
    assert page.evaluate("document.activeElement.hasAttribute('data-error-summary')")
    assert page.get_by_label("Status").input_value() == "resolved"
    assert page.get_by_label("Outcome", exact=True).input_value() == "other"
    assert page.get_by_label("Date (UTC)").input_value() == "2026-09-19"
    assert page.get_by_label("What happened").input_value() == "Kept <reply>"
    # The form carries the version it was loaded with, not a newer one.
    assert page.locator('input[name="expected_version"]').input_value() == "3"
    summary.get_by_role("link").click()
    assert page.evaluate("document.activeElement.id") == "followup-outcome"
    assert page.get_by_role("button", name="Save follow-up").is_enabled()


def test_followup_fields_follow_a_restored_status(page, component_origin):
    """A value the browser restores without a change event (going back after
    the error page, or autofill) is applied again on pageshow."""
    page.goto(component_origin + "/followup-item")
    outcome = page.get_by_label("Outcome", exact=True)
    assert not outcome.is_visible()
    page.evaluate(
        """() => {
        document.getElementById("followup-state").value = "resolved";
        document.getElementById("contact-channel").value = "email";
        window.dispatchEvent(new Event("pageshow"));
    }"""
    )
    visible(outcome)
    assert outcome.is_enabled()
    visible(page.get_by_label("Date (UTC)"))
    assert page.get_by_role("button", name="Save follow-up").is_disabled()


def test_followup_filters_without_scripts(browser_engine, component_origin):
    """Identifying filters submit only through native POST, never the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-queue")
        page.get_by_label("Search Member or Ministry name").fill("Private name")
        page.get_by_label("Status").select_option("in_progress")
        page.route("**/follow-up/", lambda route: route.fulfill(body="Filtered"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert "search=Private+name" in sent.value.post_data
        assert "state=in_progress" in sent.value.post_data
        assert "assignee" not in sent.value.post_data
        assert "ministry=9" in sent.value.post_data
        assert "Private" not in sent.value.url and "?" not in sent.value.url
    finally:
        context.close()


def test_followup_edit_without_scripts(browser_engine, component_origin):
    """The edit posts natively, with no URL state and no assignee."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-item")
        page.get_by_label("Status").select_option("resolved")
        page.get_by_label("Outcome", exact=True).select_option("joined")
        page.get_by_label("How").select_option("email")
        page.get_by_label("Date (UTC)").fill("2026-09-19")
        page.get_by_label("Time (UTC)").fill("15:04")
        page.get_by_label("What happened").fill("Private reply")
        page.route("**/update", lambda route: route.fulfill(body="Saved"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Save follow-up").click()
        body = sent.value.post_data
        assert "state=resolved" in body and "outcome=joined" in body
        assert "expected_version=3" in body and "request_key=" in body
        assert "contact_channel=email" in body and "contact_date=2026-09-19" in body
        assert "Private" not in sent.value.url and "?" not in sent.value.url
        assert "assignee" not in body
    finally:
        context.close()


def test_followup_sort_heading_without_scripts(browser_engine, component_origin):
    """A heading re-sorts the queue by native POST, keeping private filters."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-queue")
        heading = page.get_by_role("columnheader", name="Request")
        assert heading.get_attribute("aria-sort") == "descending"
        page.route("**/follow-up/", lambda route: route.fulfill(body="Sorted"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("columnheader", name="Member").get_by_role(
                "button"
            ).click()
        body = sent.value.post_data
        assert "sort=name" in body and "search=Example" in body
        assert "ministry=9" in body and "?" not in sent.value.url
    finally:
        context.close()
