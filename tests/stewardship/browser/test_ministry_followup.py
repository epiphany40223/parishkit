"""Private Ministry follow-up queue and edit form, with no assignment."""

import json
from datetime import UTC, datetime

import pytest
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.reports.ministry_followup_views import (
    bulk_values,
    change_values,
)
from parishkit.stewardship.reports.report_paging import pop_navigation

from .followup_components import (
    BULK_CLOSED,
    BULK_JOIN,
    BULK_KEY,
    BULK_LEAVE,
    BULK_POST,
    BULK_QUEUE,
)
from .test_components import axe_violations
from .waits import available, eventually, greyed, has_text, hidden, visible

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
    # The bulk outcome selection column (#1131) is named for what it
    # selects, not the old assignment's Select.
    assert page.get_by_role("columnheader", name="Select", exact=True).count() == 0
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


def test_followup_form_enables_only_the_fields_that_apply(page, component_origin):
    """Outcome is available only for Resolved and the contact details only
    once a channel is chosen; otherwise they stay in place, greyed (#1014),
    and disabled fields are not sent."""
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
        greyed(field)
    visible(page.get_by_label("Notes (optional; needed only for the outcome Other)"))
    status.select_option("resolved")
    available(outcome)
    status.select_option("closed_no_response")
    greyed(outcome)
    how.select_option("phone")
    for field in (date, time, happened):
        available(field)
    how.select_option("")
    for field in (date, time, happened):
        greyed(field)
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
    """A join is never offered Left the Ministry, nor a leave Joined the
    Ministry, so Save can never pass an outcome the server refuses; each kind
    names the shared declined outcome in its own words (#1132)."""
    join = ("Joined the Ministry", "Declined / no longer interested")
    leave = ("Left the Ministry", "Stayed in the Ministry")
    for path, offered, absent in (
        ("/followup-item", join, leave),
        ("/followup-item-leave", leave, join),
    ):
        page.goto(component_origin + path)
        page.get_by_label("Status").select_option("resolved")
        choices = (
            page.get_by_label("Outcome", exact=True).locator("option").all_inner_texts()
        )
        assert set(offered) <= set(choices) and not set(absent) & set(choices)
        assert "No response" not in choices


def test_followup_refusal_is_shown_in_place(page, component_origin):
    """A refused save shows the same form with a summary of the one problem,
    focused, and the submitted values kept and their fields shown."""
    page.goto(component_origin + "/followup-item-refused")
    summary = page.locator("[data-error-summary]")
    visible(summary)
    has_text(
        summary.get_by_role("link"),
        "Left the Ministry doesn't apply to a request to join.",
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
    greyed(outcome)
    page.evaluate(
        """() => {
        document.getElementById("followup-state").value = "resolved";
        document.getElementById("contact-channel").value = "email";
        window.dispatchEvent(new Event("pageshow"));
    }"""
    )
    available(outcome)
    available(page.get_by_label("Date", exact=True))
    assert page.get_by_role("button", name="Save follow-up").is_disabled()


def test_followup_filters_post_privately(page, component_origin):
    """Identifying filters submit only in a POST body, never the URL."""
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


def test_followup_edit_posts_privately(page, component_origin):
    """The edit posts in the body, with no URL state and no assignee."""
    page.goto(component_origin + "/followup-item")
    page.get_by_label("Status").select_option("resolved")
    page.get_by_label("Outcome", exact=True).select_option("joined")
    page.get_by_label("How").select_option("email")
    page.get_by_label("Date", exact=True).fill("2026-09-19")
    page.get_by_label("Time", exact=True).fill("08:04")
    page.get_by_label("What happened").fill("Private reply")
    page.route("**/record/", lambda route: route.fulfill(body="Saved"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Save follow-up").click()
    body = sent.value.post_data
    assert "state=resolved" in body and "outcome=joined" in body
    assert "expected_version=3" in body and "request_key=" in body
    assert "contact_channel=email" in body and "contact_date=2026-09-19" in body
    # The browser's zone goes with the typed time for the server to convert.
    assert "contact_zone=America%2FLos_Angeles" in body
    assert change_values(posted(body))["change"].contact_at == FIXTURE
    assert "Private" not in sent.value.url and "?" not in sent.value.url
    assert "assignee" not in body


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
        assert (
            date.get_attribute("aria-describedby")
            == "contact-zone-help contact-details-help"
        )
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
        ("Date", "followup-save-hint contact-details-help"),
        ("Time", "followup-save-hint contact-details-help contact-time_reading"),
    ):
        field = page.get_by_label(label, exact=True)
        assert field.get_attribute("aria-describedby") == described
    assert not page.locator("#contact-zone-help").is_visible()
    # The note is never shown with an empty zone ("time zone: .").
    assert not page.get_by_text("time zone: .", exact=False).is_visible()
    page.get_by_label("How").select_option("")
    assert save.is_enabled()


def test_followup_sort_heading_posts_privately(page, component_origin):
    """A heading re-sorts the queue by POST, keeping private filters. The
    default order (newest first) has no column to show it (#1064), so no
    heading is marked sorted."""
    page.goto(component_origin + "/followup-queue")
    assert page.locator("thead th[aria-sort]").count() == 0
    page.route("**/follow-up/", lambda route: route.fulfill(body="Sorted"))
    # "Member" alone would also match the Member DUID heading.
    member = page.locator("th[data-sort-column='member']")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        member.get_by_role("button").click()
    body = sent.value.post_data
    assert "sort=name" in body and "search=Example" in body
    assert "ministry=9" in body and "?" not in sent.value.url


def test_clipped_name_tip_never_swallows_the_next_tap(browser_engine, component_origin):
    """On a touch screen a tap on a cut Ministry name shows its tip, and the
    next tap, even on a link drawn under the tip, hides the tip and opens
    the link (#1136)."""
    from playwright.sync_api import expect

    context = browser_engine.new_context(
        has_touch=True, viewport={"width": 1280, "height": 900}
    )
    try:
        page = context.new_page()
        page.goto(component_origin + "/followup-all")
        name = page.locator("tbody tr").nth(1).locator(".cell-clamp")
        name.tap()
        tip = page.locator(".admin-tip.is-shown")
        expect(tip).to_have_text(name.text_content())
        # A link where the tip is drawn, under it.
        page.evaluate(
            """() => {
                const box = document.querySelector('.admin-tip.is-shown')
                    .getBoundingClientRect();
                const link = document.createElement('a');
                link.href = '#under-the-tip';
                link.id = 'under-the-tip';
                link.textContent = 'Under the tip';
                Object.assign(link.style, {
                    position: 'fixed', zIndex: '1', display: 'block',
                    left: box.left + 'px', top: box.top + 'px',
                    width: box.width + 'px', height: box.height + 'px',
                });
                document.body.append(link);
            }"""
        )
        box = tip.bounding_box()
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        expect(tip).to_have_count(0)
        assert page.evaluate("location.hash") == "#under-the-tip"
    finally:
        context.close()


def test_followup_columns_follow_the_shared_order(page, component_origin):
    """The queue's columns follow the shared order (#932): the Member leads
    and the Member DUID has its own column (a Member added on the form reads
    New Member). By Administrator decision (#1064) there is no Ministry DUID
    or Submitted column.
    A Ministry name too long even for two lines (#1136) ends in an
    ellipsis on its second line, joins the tab order and shows whole in a
    tip on keyboard focus, and never widens the page."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/followup-all")
    headings = page.locator("thead th").all_inner_texts()
    # A sort heading's text also holds its button's arrow and hint lines. The
    # selection box for bulk outcomes leads (#1131), as the shared order puts
    # it.
    assert [text.split("\n")[0].strip() for text in headings] == [
        "",
        "Member",
        "Member DUID",
        "Ministry",
        "Request",
        "Status",
        "Last contact",
    ]
    rows = page.locator("tbody tr")
    first, second = (rows.nth(n).locator("th, td") for n in (0, 1))
    assert first.nth(1).inner_text() == "Example <Member>"
    assert first.nth(2).inner_text() == "12345"
    assert second.nth(2).inner_text() == "New Member"
    name = second.nth(3).locator(".cell-clamp")
    assert name.text_content().endswith("<end>")
    assert name.evaluate("e => e.scrollHeight > e.clientHeight")
    # Two lines: twice the first row's one-line name, whatever the font.
    short = first.nth(3).locator(".cell-clamp")
    height = name.bounding_box()["height"]
    assert abs(height - 2 * short.bounding_box()["height"]) <= 2, height
    assert name.get_attribute("tabindex") == "0"
    # The short name in the first row fits, so it stays out of the tab order.
    assert short.get_attribute("tabindex") is None
    name.focus()
    expect(page.locator(".admin-tip.is-shown")).to_have_text(name.text_content())
    # A resize keeps focus on the name and its tip shown beside it.
    page.set_viewport_size({"width": 1100, "height": 900})
    assert name.evaluate("e => document.activeElement === e")
    expect(page.locator(".admin-tip.is-shown")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def bulk_dialog(page):
    """The follow-up queue's bulk outcome dialog (#1131)."""
    return page.locator("dialog#followup-bulk-dialog")


def bulk_page(page, component_origin):
    """Open the bulk outcome queue; fail on script errors; mark against reloads."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + BULK_QUEUE)
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def tick(page, request):
    """Tick one request's selection box on the queue."""
    page.locator(f"input[data-select-row][value^='{request}:']").check()


def test_bulk_outcome_records_only_the_matching_kind_in_place(
    page, component_origin, axe_source
):
    """Bulk outcomes (#1131): only open requests can be ticked; ticking moves
    no control; Mark as Joined counts only the requests to join and names
    the skipped leave; the optional contact attempt gates Record outcome
    until complete; recording posts only the matching request with its own
    version, then redraws the table in place without a reload."""
    failures = bulk_page(page, component_origin)
    assert page.locator(f"input[value^='{BULK_CLOSED}:']").count() == 0
    joined = page.get_by_role("button", name="Mark as Joined")
    buttons = page.locator(".bulk-bar [data-bulk-action]")
    assert buttons.all_inner_texts() == [
        "Mark as Joined",
        "Mark as Left",
        "Mark as Declined / stayed",
    ]
    for index in range(buttons.count()):
        assert buttons.nth(index).is_disabled()
    hint = page.locator("[data-selection-hint]")
    assert "Tick open requests on this page" in hint.inner_text()
    place = "node => JSON.stringify(node.getBoundingClientRect())"
    before = [buttons.nth(i).evaluate(place) for i in range(buttons.count())]
    table_top = page.locator("#followup-table").evaluate(place)
    tick(page, BULK_JOIN)
    tick(page, BULK_LEAVE)
    available(joined)
    # Ticking never moves a control or the table (no layout shift).
    assert [buttons.nth(i).evaluate(place) for i in range(buttons.count())] == before
    assert page.locator("#followup-table").evaluate(place) == table_top
    joined.click()
    dialog = bulk_dialog(page)
    visible(dialog)
    assert dialog.locator("h2").inner_text() == (
        "Mark Joining <Member> — Example <Ministry> as Joined the Ministry?"
    )
    skipped = dialog.locator("[data-confirm-skipped]")
    visible(skipped)
    assert skipped.inner_text() == (
        "Leaving <Member> — Example <Ministry> is not a request to join, so it "
        "is left as it is."
    )
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    assert axe_violations(page, axe_source) == []
    record = dialog.get_by_role("button", name="Record outcome")
    available(record)
    for label in ("Date", "Time", "What happened (optional)"):
        greyed(dialog.get_by_label(label, exact=True))
    dialog.get_by_label("How").select_option("phone")
    hint_line = dialog.locator("#bulk-complete-hint")
    eventually(page, "document.querySelector('#bulk-complete-hint').textContent !== ''")
    assert record.is_disabled()
    assert "Enter the date and time" in hint_line.inner_text()
    dialog.get_by_label("Date", exact=True).fill("2026-09-19")
    dialog.get_by_label("Time", exact=True).fill("3:04 PM")
    dialog.get_by_label("What happened (optional)").fill("Left a <message>")
    available(record)
    posted = []

    def answer(route):
        """Record the dialog's POST and answer as the bulk view does."""
        posted.append(route.request.post_data or "")
        route.fulfill(status=204, body="")

    page.route(component_origin + BULK_POST, answer)
    record.click()
    hidden(dialog)
    visible(
        page.locator(
            "main > [role=status]",
            has_text="Marked Joining <Member> — Example <Ministry> as Joined the "
            "Ministry.",
        )
    )
    # The table is redrawn from the remembered view: the join is resolved.
    visible(
        page.locator("tbody tr", has_text="Joining <Member>").get_by_text(
            "Resolved: Joined the Ministry"
        )
    )
    assert page.locator(f"input[value^='{BULK_JOIN}:']").count() == 0
    assert page.evaluate("window.notReloaded === true")
    form = QueryDict(posted[0])
    assert form.getlist("request") == [f"{BULK_JOIN}:3"]
    values = bulk_values(form)
    assert values["outcome"] == "joined" and values["bulk_key"] == BULK_KEY
    assert values["versions"] == {BULK_JOIN: 3}
    assert values["change"].contact_channel == "phone"
    assert values["change"].contact_notes == "Left a <message>"
    assert values["change"].contact_at is not None
    # The redrawn region carries a fresh bulk key for the next action.
    assert page.locator("input[name=bulk_key]").input_value() != str(BULK_KEY)
    assert not failures


def test_bulk_outcome_with_no_matching_request_cannot_be_recorded(
    page, component_origin
):
    """With only a leave ticked, Mark as Joined says why and offers nothing to
    record; reopening the dialog clears an earlier contact attempt."""
    failures = bulk_page(page, component_origin)
    tick(page, BULK_LEAVE)
    page.get_by_role("button", name="Mark as Joined").click()
    dialog = bulk_dialog(page)
    visible(dialog)
    assert "None of the selected requests is a request to join" in (
        dialog.locator("[data-confirm-skipped]").inner_text()
    )
    record = dialog.get_by_role("button", name="Record outcome")
    assert record.is_disabled()
    # The skipped line describes the dialog, and the unavailable Record
    # outcome names it, so a screen reader hears why.
    skipped_id = "followup-bulk-dialog-skipped"
    assert dialog.locator("[data-confirm-skipped]").get_attribute("id") == skipped_id
    assert skipped_id in dialog.get_attribute("aria-describedby").split()
    assert skipped_id in (record.get_attribute("aria-describedby") or "").split()
    dialog.get_by_label("How").select_option("email")
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    page.get_by_role("button", name="Mark as Declined / stayed").click()
    visible(dialog)
    assert dialog.locator("h2").inner_text() == (
        "Mark Leaving <Member> — Example <Ministry> as Declined / stayed?"
    )
    hidden(dialog.locator("[data-confirm-skipped]"))
    # Available again, Record outcome no longer names the skipped line.
    assert skipped_id not in (record.get_attribute("aria-describedby") or "").split()
    assert "Stayed in the Ministry" in dialog.inner_text()
    assert dialog.get_by_label("How").input_value() == ""
    available(dialog.get_by_role("button", name="Record outcome"))
    assert not failures


def test_a_refused_bulk_outcome_names_the_row_inside_the_dialog(page, component_origin):
    """A stale request refuses the whole batch: the server's words show below
    the buttons, which do not move, and nothing is redrawn."""
    failures = bulk_page(page, component_origin)
    message = (
        "Joining <Member> — Example <Ministry> changed after this list was "
        "shown (someone else may have recorded follow-up on it), so nothing "
        "was saved."
    )

    def refuse(route):
        """Answer as the bulk view does for a request that changed."""
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps(
                {"refusal": {"message": message, "fix": "Cancel, then reload."}}
            ),
        )

    page.route(component_origin + BULK_POST, refuse)
    page.locator("input[data-select-all]").check()
    assert page.locator("input[data-select-row]:checked").count() == 2
    page.get_by_role("button", name="Mark as Declined / stayed").click()
    dialog = bulk_dialog(page)
    assert dialog.locator("h2").inner_text() == (
        "Mark 2 requests as Declined / stayed?"
    )
    record = dialog.get_by_role("button", name="Record outcome")
    box = record.bounding_box()
    record.click()
    error = dialog.locator("[data-confirm-error]")
    visible(error)
    assert message in error.inner_text()
    assert record.bounding_box() == box and record.is_disabled()
    assert page.locator("input[data-select-row]:checked").count() == 2
    dialog.get_by_role("button", name="Cancel").click()
    hidden(dialog)
    assert page.evaluate("window.notReloaded === true")
    assert not failures
