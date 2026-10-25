"""Private Ministry follow-up queue and edit form, with no assignment."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.reports.ministry_followup_views import change_values
from parishkit.stewardship.reports.report_paging import pop_navigation

from .followup_components import (
    DECIDED,
    DECIDED_NOTICE,
    GONE_ITEM,
    GONE_QUEUE,
    LEADER_STAGES,
    REQUEST,
    REQUESTS,
    STAGES,
)
from .test_table_sorting import fulfil_post_with
from .waits import available, greyed, has_text, hidden, visible

# The instant every follow-up fixture shows (followup_components.py).
FIXTURE = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
# The review worklist's rows (#1684): its table, in the shared region.
ROWS = "#table table tbody tr"


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
    GONE_QUEUE,
    GONE_ITEM,
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
    # A decided request's history remains, the form does not; its decision
    # changes on Ministry requests (Back to review, #1684).
    page.goto(component_origin + "/followup-closed")
    assert page.get_by_role("button", name="Save follow-up").count() == 0
    visible(page.get_by_text("This request is decided", exact=False))
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
    page.get_by_label("Stage").select_option("ignored")
    page.route("**/reports/ministries/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    assert "stage=ignored" in sent.value.post_data
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
    default order is the Member column's A-Z order (#2124), so the Member
    heading alone is marked sorted."""
    page.goto(component_origin + "/followup-queue")
    sorted_headings = page.locator("#table thead th[aria-sort]")
    assert sorted_headings.count() == 1
    assert sorted_headings.get_attribute("data-sort-column") == "member"
    assert sorted_headings.get_attribute("aria-sort") == "ascending"
    page.route("**/reports/ministries/", lambda route: route.fulfill(body="Sorted"))
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
        name = page.locator(ROWS).nth(1).locator(".cell-clamp:not(.name-clamp)")
        # The Ministry name now opens the request's page (R38 finding 13),
        # and a tap on a link opens it as it shows the tip; the tip itself is
        # what this checks, so its link is set aside.
        name.evaluate("node => node.closest('a').removeAttribute('href')")
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
    """The worklist's columns follow the shared order (#932, #1684): the
    Member leads, its Member DUID on the line under it (a Member added on
    the form reads New Member, #2124), then the Ministry, then the review
    worklist's own, Last contact a line of the Submitted and Last decision
    cell (#2124). No Ministry DUID.
    A Ministry name too long even for two lines (#1136) ends in an
    ellipsis on its second line, joins the tab order and shows whole in a
    tip on keyboard focus, and never widens the page."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1366, "height": 900})
    page.goto(component_origin + "/followup-all")
    headings = page.locator("#table thead th").all_inner_texts()
    # A sort heading's text also holds its button's arrow and hint lines. The
    # selection box for bulk decisions leads (#1131), as the shared order puts
    # it.
    assert [text.split("\n")[0].strip() for text in headings] == [
        "",
        "Member",
        "Ministry",
        "Family's answer",
        "Stage",
        "Submitted",
        "Actions",
    ]
    rows = page.locator(ROWS)
    first, second = (rows.nth(n).locator("th, td") for n in (0, 1))
    assert first.nth(1).locator("a").inner_text() == "Example <Member>"
    assert first.nth(1).locator(".cell-detail").inner_text().endswith("12345")
    assert second.nth(1).locator(".cell-detail").inner_text().endswith("New Member")
    assert "Last contact" in first.nth(5).inner_text()
    name = second.nth(2).locator(".cell-clamp")
    assert name.text_content().endswith("<end>")
    assert name.evaluate("e => e.scrollHeight > e.clientHeight")
    # Two lines: twice the first row's one-line name, whatever the font.
    short = first.nth(2).locator(".cell-clamp")
    height = name.bounding_box()["height"]
    assert abs(height - 2 * short.bounding_box()["height"]) <= 2, height
    # The Ministry cell is the request's link (R38 finding 13), so the
    # keyboard reaches a clipped name through it: neither name joins the tab
    # order itself, and focus on the link shows the clipped one's tip.
    assert name.get_attribute("tabindex") is None
    assert short.get_attribute("tabindex") is None
    link = name.locator("xpath=ancestor::a[1]")
    link.focus()
    expect(page.locator(".admin-tip.is-shown")).to_have_text(name.text_content())
    # A resize keeps focus on the link and the name's tip shown beside it.
    page.set_viewport_size({"width": 1100, "height": 900})
    assert link.evaluate("e => document.activeElement === e")
    expect(page.locator(".admin-tip.is-shown")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_members_no_longer_in_parishsoft_are_marked_with_nothing_to_record(
    page, component_origin
):
    """Staff's Members choice (#1217) lists requests for Members no longer in
    ParishSoft, each marked beside the name with #1216's phrase and offering
    no action, so the rows stay one line; its request page says exactly why
    and has no form. A Ministry leader is not offered the choice."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/followup-queue")
    members = page.get_by_label("Members")
    assert members.locator("option").all_inner_texts() == [
        "In ParishSoft",
        "No longer in ParishSoft",
    ]
    assert page.locator("[data-source-status]").count() == 0
    page.goto(component_origin + "/followup-leader")
    assert page.get_by_label("Members").count() == 0
    page.goto(component_origin + GONE_QUEUE)
    assert page.get_by_label("Members").input_value() == "gone"
    assert page.locator("[data-select-row]").count() == 0
    assert page.locator(f"{ROWS} .table-actions button").count() == 0
    marks = page.locator("[data-source-status]")
    assert marks.count() == 2
    # #1216's phrases, shown whole, so a reader cannot take them for a
    # Ministry's own activity marks.
    for index, phrase in enumerate(
        ("Inactive in ParishSoft", "Deceased in ParishSoft")
    ):
        mark = marks.nth(index)
        visible(mark)
        assert mark.inner_text() == phrase
    # The mark sits beside the name on the same line, so a long name cut
    # short and a short one take the same height.
    heights = page.locator(ROWS).evaluate_all(
        "rows => rows.map((row) => row.getBoundingClientRect().height)"
    )
    assert max(heights) - min(heights) < 1
    page.goto(component_origin + GONE_ITEM)
    notice = page.locator("#followup-not-in-source")
    visible(notice)
    assert notice.inner_text().startswith(
        "Inactive in ParishSoft: ParishSoft marks this Member inactive."
    )
    assert page.get_by_role("button", name="Save follow-up").count() == 0
    assert page.get_by_text("This request is decided", exact=False).count() == 0
    visible(page.get_by_text("Left a <private> voicemail", exact=True).first)


def worklist_row(page, request_id):
    """One request's row on the worklist."""
    return page.locator(f"#requests-row-{request_id}")


# A row's icons by what they do; "Send" is its Back to review, the pressed
# icon (#2131) or, on a row with nothing pressed, an icon of its own.
ICONS = {
    "Accept": '[data-row-action="accept"]',
    "Edit": '[data-row-action="edit"]',
    "Ignore": '[data-row-action="ignore"]',
    "Mark": '[data-row-action="entered_by_hand"]',
    "Send": '[data-row-undo], [data-row-action="back_to_review"]',
}


def icon_named(page, request_id, label):
    """A row's ``label`` icon (Accept, Edit, Ignore, Mark or Send)."""
    return worklist_row(page, request_id).locator(
        ", ".join(f"button{part.strip()}" for part in ICONS[label].split(","))
    )


def test_each_stage_shows_its_decision_on_the_icons(page, component_origin):
    """Staff see every stage with the pipeline's words (#1684): To review
    offers Accept, Edit and Ignore; an accepted request is To enter by hand
    with Accept pressed as its Back to review (#2131) and Entered by hand
    offered; once entered, the pressed mark undoes it (#1840);
    an ignored one names its outcome; an edited one and an added one say
    so; a confirmed one offers only Back to review."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + STAGES)
    stage = page.get_by_label("Stage")
    assert stage.locator("option").all_inner_texts() == [
        "To review (1)",
        "To enter by hand (3)",
        "Entered by hand (1)",
        "Ignored (1)",
        "Confirmed (1)",
        "Every stage (7)",
    ]
    for label in ("Accept", "Edit", "Ignore"):
        assert icon_named(page, REQUEST, label).get_attribute("aria-pressed") in (
            "false",
            None,
        )
    accepted = UUID(int=120)
    chosen = icon_named(page, accepted, "Accept")
    assert chosen.get_attribute("aria-pressed") == "true"
    assert chosen.get_attribute("title").startswith(
        "Accepted; select to send "
    ) and chosen.get_attribute("title").endswith(" back to review")
    assert icon_named(page, accepted, "Send").get_attribute("data-row-action") == (
        "accept"
    )
    visible(icon_named(page, accepted, "Mark"))
    has_text(worklist_row(page, accepted).locator(".stage-chip"), "To enter by hand")
    entered = UUID(int=121)
    assert icon_named(page, entered, "Mark").get_attribute("aria-pressed") == "true"
    assert icon_named(page, entered, "Send").get_attribute("data-row-action") == (
        "entered_by_hand"
    )
    ignored = worklist_row(page, UUID(int=122))
    has_text(ignored.locator(".stage-chip"), "Ignored")
    assert "Stayed in the Ministry" in ignored.inner_text()
    edited = worklist_row(page, UUID(int=123))
    assert "Accepted (edited)" in edited.inner_text()
    # The edited Ministry is on the values cell's Reviewer's value line (R38
    # finding 11, #2124), not only somewhere in the row.
    reviewer = edited.locator(".values-cell .stack-line", has_text="Reviewer's value")
    assert reviewer.locator(".value-clamp").inner_text().strip() == (
        "Join Another Ministry"
    )
    added = worklist_row(page, UUID(int=124))
    assert "Added by the parish" in added.inner_text()
    confirmed = worklist_row(page, UUID(int=125))
    has_text(confirmed.locator(".stage-chip"), "Confirmed")
    offered = [
        "back_to_review"
        if node.get_attribute("data-row-undo") is not None
        else node.get_attribute("data-row-action")
        for node in confirmed.locator(".table-actions button").all()
        if node.get_attribute("aria-disabled") is None
    ]
    assert offered == ["back_to_review"]
    visible(page.locator(".bulk-bar").get_by_role("button", name="Add"))


def test_back_to_review_undoes_entered_by_hand_with_a_note(page, component_origin):
    """On an Entered by hand row, Back to review's dialog says the mark is
    undone and the request goes back to To enter by hand, and Record waits
    for the required note; it posts the decision and the note, in place
    (#1840)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + STAGES)
    page.evaluate("window.notReloaded = true")
    sent = []

    def choose(request):
        """Keep the body; answer with a page after a decision."""
        sent.append(request.post_data)
        return DECIDED

    fulfil_post_with(page, "**/reports/ministries/", component_origin, choose)
    entered = UUID(int=121)
    icon_named(page, entered, "Send").click()
    dialog = page.locator("#requests-decide-dialog")
    has_text(
        dialog.locator("[data-confirm-body]"),
        "Entered by hand is undone: the item goes back to To enter by hand, with "
        "its decision kept. Say why.",
    )
    record = dialog.get_by_role("button", name="Record")
    assert record.is_disabled()
    dialog.get_by_label("Note (required)").fill("Not typed yet")
    available(record)
    record.click()
    has_text(page.locator("#requests-notice"), DECIDED_NOTICE)
    hidden(dialog)
    body = QueryDict(sent[0])
    assert body["decide"] == "back_to_review" and body["note"] == "Not typed yet"
    assert body.getlist("item") == [str(entered)]
    assert page.evaluate("window.notReloaded") is True


def test_a_leader_never_sees_the_update_phase(page, component_origin):
    """A Ministry leader's Stage choices hold Accepted, not To enter by hand
    or Entered by hand, and an entered request reads Accepted with no mark
    (#1160); a leader has no Add (#1684)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LEADER_STAGES)
    options = page.get_by_label("Stage").locator("option").all_inner_texts()
    assert options == [
        "To review (1)",
        "Accepted (4)",
        "Ignored (1)",
        "Confirmed (1)",
        "Every stage (7)",
    ]
    entered = worklist_row(page, UUID(int=121))
    has_text(entered.locator(".stage-chip"), "Accepted")
    assert "Entered by hand" not in page.locator(ROWS).all_inner_texts()[2]
    assert page.get_by_role("button", name="Add").count() == 0
    assert page.locator("button[aria-label^='Mark ']").count() == 0


def test_accept_records_in_place(page, component_origin):
    """Accept asks in the shared dialog naming the request, posts its fields
    with the worklist's view to Ministry requests itself, and the answer is
    the page redrawn in place with its notice."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + REQUESTS)
    page.evaluate("window.notReloaded = true")
    sent = []

    def choose(request):
        """Keep the body; answer with the page after the Accept."""
        sent.append(request.post_data)
        return DECIDED

    fulfil_post_with(page, "**/reports/ministries/", component_origin, choose)
    icon_named(page, REQUEST, "Accept").click()
    dialog = page.locator("#requests-decide-dialog")
    has_text(dialog.locator("h2"), "Accept Example <Member> — Example <Ministry>?")
    has_text(page.get_by_label("Stage").locator("option").first, "To review (2)")
    dialog.get_by_role("button", name="Record").click()
    has_text(page.locator("#requests-notice"), DECIDED_NOTICE)
    hidden(dialog)
    body = QueryDict(sent[0])
    assert body["decide"] == "accept" and body["item"] == str(REQUEST)
    assert body["version"] == "3" and body["stage"] == "to_review"
    # Accepted, the request leaves the To review view, and the Stage
    # choices' counts follow the answer in place (#1684).
    has_text(
        page.get_by_label("Stage").locator("option").nth(1), "To enter by hand (1)"
    )
    assert page.get_by_label("Stage").input_value() == "to_review"
    assert worklist_row(page, REQUEST).count() == 0
    assert page.evaluate("window.notReloaded === true")


def test_edit_offers_another_ministry(page, component_origin):
    """Edit opens the request's own Ministry list (never its own Ministry);
    Save waits until one is chosen."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + REQUESTS)
    icon_named(page, REQUEST, "Edit").click()
    dialog = page.locator("dialog[open]")
    choice = dialog.get_by_label("Reviewer's value: the Ministry meant")
    assert choice.locator("option").all_inner_texts() == [
        "Choose a Ministry",
        "Another Ministry",
    ]
    save = dialog.get_by_role("button", name="Save")
    assert save.is_disabled()
    choice.select_option("12")
    available(save)


def test_add_asks_for_the_member_ministry_and_request(page, component_origin):
    """Staff's Add opens its dialog: the Member DUID, a campaign Ministry,
    Join or Leave and the optional ignored request it replaces."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + STAGES)
    page.locator(".bulk-bar").get_by_role("button", name="Add").click()
    dialog = page.locator("#requests-add-dialog")
    visible(dialog)
    add = dialog.get_by_role("button", name="Add")
    assert add.is_disabled()
    dialog.get_by_label("Member DUID of the Member to add").fill("12345")
    dialog.get_by_label("Ministry to add").select_option("12")
    dialog.get_by_label("Request to add").select_option("join")
    replaced = dialog.get_by_label("Added in place of (optional)")
    # The choices follow the Ministry and Request chosen; wait for them, as
    # Firefox fills them a moment later.
    expect(replaced).to_contain_text("Ignored <Member>")
    available(add)


# The table's box and its first row's layout, and how many lines each
# "Leave (cannot participate)" answer takes and whether its two-line clamp
# cuts it short.
LONG_NAMES_FIT = """() => {
  const box = document.querySelector('.worklist-table .table-scroll');
  const leaves = [...box.querySelectorAll('.value-clamp')]
    .filter((value) => value.textContent.includes('cannot participate'))
    .map((value) => {
      const range = document.createRange();
      range.selectNodeContents(value);
      return {lines: new Set([...range.getClientRects()].map(
                (rect) => Math.round(rect.top))).size,
              cut: value.scrollHeight > value.clientHeight + 1};
    });
  return {scroll: box.scrollWidth, width: box.clientWidth, leaves,
          display: getComputedStyle(box.querySelector('tbody tr')).display};
}"""


def test_long_names_fit_the_box_at_a_laptop_window(page, component_origin):
    """With the long-names data (followup-page: long Member and Ministry
    names, every stage) at a 1280 px window beside the Admin menu, the
    table keeps its table layout and fits its box, so neither scrolls
    sideways; each "Leave (cannot participate)" answer takes at most two
    lines, none cut short. In Linux's DejaVu Sans it was 80-100 px wider
    than its box (#2181)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/followup-page")
    page.evaluate("document.fonts.ready")
    fit = page.evaluate(LONG_NAMES_FIT)
    assert fit["display"] == "table-row", fit
    assert fit["scroll"] <= fit["width"] + 1, fit
    assert fit["leaves"], fit
    assert all(leave["lines"] <= 2 and not leave["cut"] for leave in fit["leaves"]), fit
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
