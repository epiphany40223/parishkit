"""Native private Ministry follow-up queue and edit form, with no assignment."""

import json
from datetime import UTC, datetime

import pytest
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.reports.ministry_followup_views import change_values
from parishkit.stewardship.reports.report_paging import pop_navigation

from .conftest import no_script_context
from .waits import has_text, visible

# The instant every follow-up fixture shows (followup_components.py).
FIXTURE = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)


def posted(body):
    """The follow-up form as the update view reads it: Save and next's
    fields (the queue token and next request, #534) split off first."""
    form = QueryDict(body, mutable=True)
    assert pop_navigation(form) == ("", False, None)
    return form


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
    date, time = (
        page.get_by_label("Date", exact=True),
        page.get_by_label("Time", exact=True),
    )
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
    page.route("**/record/", lambda route: route.fulfill(body="Saved"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Save follow-up").click()
    body = sent.value.post_data
    assert "state=in_progress" in body and "notes=" in body
    assert "contact_channel=" in body
    for name in (
        "outcome=",
        "contact_date=",
        "contact_time=",
        "contact_zone=",
        "contact_notes=",
    ):
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
    page.get_by_label("Date", exact=True).fill("2026-09-19")
    assert save.is_disabled()
    page.get_by_label("Time", exact=True).fill("15:04")
    assert save.is_enabled() and not hint.is_visible()
    # Choosing no contact attempt drops that requirement again.
    page.get_by_label("Date", exact=True).fill("")
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
    request = "00000000-0000-0000-0000-00000000005d"
    assert href == (
        reverse("admin:ministry_followup_item", args=[request])
        + "?page=2#followup-history"
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
    assert page.get_by_label("Date", exact=True).input_value() == "2026-09-19"
    assert page.get_by_label("Time", exact=True).input_value() == "15:04"
    # The local-time note and zone come back with the kept contact (#558).
    assert page.locator("[name=contact_zone]").input_value() == "America/Los_Angeles"
    visible(page.get_by_text("time zone: America/Los_Angeles.", exact=False))
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
    visible(page.get_by_label("Date", exact=True))
    assert page.get_by_role("button", name="Save follow-up").is_disabled()


def test_followup_filters_without_scripts(browser_engine, component_origin):
    """Identifying filters submit only through native POST, never the URL."""
    context = no_script_context(browser_engine)
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
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-item")
        page.get_by_label("Status").select_option("resolved")
        page.get_by_label("Outcome", exact=True).select_option("joined")
        page.get_by_label("How").select_option("email")
        page.get_by_label("Date", exact=True).fill("2026-09-19")
        page.get_by_label("Time", exact=True).fill("15:04")
        page.get_by_label("What happened").fill("Private reply")
        page.route("**/record/", lambda route: route.fulfill(body="Saved"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Save follow-up").click()
        body = sent.value.post_data
        assert "state=resolved" in body and "outcome=joined" in body
        assert "expected_version=3" in body and "request_key=" in body
        assert "contact_channel=email" in body and "contact_date=2026-09-19" in body
        # The Admin portal requires JavaScript (#565): without it the browser
        # zone is unknown, so the server refuses the contact time rather than
        # guessing its zone (#558).
        assert "contact_zone=&" in body
        with pytest.raises(ValueError):
            change_values(posted(body))
        assert "Private" not in sent.value.url and "?" not in sent.value.url
        assert "assignee" not in body
    finally:
        context.close()


@pytest.mark.parametrize(
    ("zone", "typed", "shown"),
    [
        ("America/Los_Angeles", "8:04 am", "September 19, 2026 at 8:04 AM"),
        ("UTC", "3:04pm", "September 19, 2026 at 3:04 PM"),
    ],
)
def test_followup_contact_time_round_trips_in_browser_time(
    browser_engine, component_origin, zone, typed, shown
):
    """A contact time typed in local time is stored as UTC and shown back alike.

    The browser sends its zone with the form; the server's own form grammar
    turns the typed time into the fixture instant, and the page shows that
    instant (history contact, history edit, last contact) as the same local
    time that was typed (#558).
    """
    context = browser_engine.new_context(timezone_id=zone)
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-item")
        times = page.locator("time[data-local-instant]").all_inner_texts()
        assert sum(shown in text for text in times) >= 2
        # Like the date and time, the zone is not sent until a channel is.
        assert page.locator("[name=contact_zone]").is_disabled()
        page.get_by_label("How").select_option("phone")
        note = page.locator("#contact-zone-help")
        visible(note.get_by_text(f"time zone: {zone}.", exact=False))
        assert page.locator("[name=contact_zone]").input_value() == zone
        date = page.get_by_label("Date", exact=True)
        assert date.get_attribute("aria-describedby") == "contact-zone-help"
        page.get_by_label("Date", exact=True).fill("2026-09-19")
        page.get_by_label("Time", exact=True).fill(typed)
        page.route("**/record/", lambda route: route.fulfill(body="Saved"))
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Save follow-up").click()
        change = change_values(posted(sent.value.post_data))["change"]
        assert change.contact_at == FIXTURE
    finally:
        context.close()


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_followup_contact_waits_for_a_known_browser_zone(
    page, component_origin, reported
):
    """A browser that reports no zone, or ICU's unknown zone, cannot save a
    contact time: the zone stays empty, its note stays hidden, and Save says
    why (#558); the server's catalog check refuses any other unknown zone.
    Other edits still save."""
    # Init scripts run before the page's own and are not subject to its CSP.
    page.add_init_script(
        """{
        const original = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            return {...original.call(this), timeZone: ZONE};
        };
    }""".replace("ZONE", json.dumps(reported))
    )
    page.goto(component_origin + "/followup-item")
    save = page.get_by_role("button", name="Save follow-up")
    assert save.is_enabled()
    page.get_by_label("How").select_option("phone")
    page.get_by_label("Date", exact=True).fill("2026-09-19")
    page.get_by_label("Time", exact=True).fill("08:04")
    assert save.is_disabled()
    has_text(
        page.locator("#followup-save-hint"),
        "Your browser didn't report a time zone, so this contact time can't be "
        "saved. Check your computer's time zone setting.",
    )
    assert page.locator("[name=contact_zone]").input_value() == ""
    # Time is also described by its time-entry reading line (#398).
    for label, described in (
        ("Date", "followup-save-hint"),
        ("Time", "followup-save-hint contact-time_reading"),
    ):
        field = page.get_by_label(label, exact=True)
        assert field.get_attribute("aria-describedby") == described
    assert not page.locator("#contact-zone-help").is_visible()
    # The note is never shown with an empty zone ("time zone: .").
    assert not page.get_by_text("time zone: .", exact=False).is_visible()
    page.get_by_label("How").select_option("")
    assert save.is_enabled()


def test_followup_sort_heading_without_scripts(browser_engine, component_origin):
    """A heading re-sorts the queue by native POST, keeping private filters."""
    context = no_script_context(browser_engine)
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
