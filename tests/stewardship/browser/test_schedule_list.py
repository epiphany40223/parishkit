"""Dates and mail schedules' list and its New and Edit page (#878, #879).

The list sorts by When in place, deletes one or several schedules through the
shared confirmation dialog and redraws itself without a reload; New and
Edit scheduled email post one schedule, or one reminder per repeat date, in
the browser's time zone (#558): the campaign is in New York's zone and the
default page is Los Angeles's, three hours behind.
The pages and their POST answers are schedule_list_components'.
"""

import json
from urllib.parse import parse_qs

import pytest

from .schedule_list_components import (
    BULK,
    EDIT,
    EDIT_DIGEST,
    EDIT_UNCHANGED,
    FAILED,
    LIST,
    NEW,
    REFUSAL,
    REFUSED,
)
from .test_components import axe_violations
from .waits import eventually, has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def rows(page):
    """The scheduled emails table's body rows, top to bottom."""
    return page.locator("table.schedule-table tbody tr")


def names(page):
    """Each row's schedule name, top to bottom."""
    return [text.strip() for text in rows(page).locator("th").all_inner_texts()]


def dialog(page):
    """The open confirmation dialog."""
    return page.locator("dialog[data-confirm-dialog]")


def watch(page):
    """Fail on any script error, and mark the page so a reload shows."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.evaluate("window.notReloaded = true")
    return failures


def has_count(locator, count):
    """Wait until ``locator`` matches ``count`` elements (a redraw)."""
    from playwright.sync_api import expect

    expect(locator).to_have_count(count)


def posted(request):
    """A captured POST's fields, as lists of values."""
    return parse_qs(request.post_data or "", keep_blank_values=True)


@pytest.mark.parametrize("width", [320, 1280])
def test_list_has_one_line_of_dates_and_actions_only_on_unsent_rows(
    page, component_origin, axe_source, width
):
    """No campaign window panel; sent rows are read-only; emails have names."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + LIST)
    failures = watch(page)
    main = page.locator("main")
    assert "Campaign window" not in main.inner_text()
    visible(main.locator(".campaign-dates", has_text="Campaign dates:"))
    new = page.get_by_role("link", name="New scheduled email")
    assert new.get_attribute("href") == NEW
    assert names(page) == [
        "Initial invitation",
        "Reminder 1",
        "Reminder 2",
        "Weekly Admin digest",
    ]
    heading = page.locator('th[data-sort-column="when"]')
    assert "(your time)" not in heading.inner_text()
    sent = rows(page).nth(0)
    assert sent.get_attribute("data-schedule-state") == "sent"
    assert sent.locator("input[type=checkbox]").count() == 0
    assert sent.get_by_role("button").count() == 0
    assert sent.get_by_role("link", name="Edit Initial invitation").count() == 0
    reminder = rows(page).nth(1)
    edit = reminder.get_by_role("link", name="Edit Reminder 1")
    assert edit.get_attribute("title") == "Edit Reminder 1"
    assert edit.get_attribute("href").startswith("/admin/campaign/schedules/")
    visible(reminder.get_by_role("button", name="Delete Reminder 1"))
    # Two reminder emails share a subject, so each name adds its ID's start.
    assert "reminder mail (" in reminder.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_row_action_icons_are_the_same_size(page, component_origin, width):
    """The Edit link and Delete button draw as equal squares (#879 pattern)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + LIST)
    reminder = rows(page).nth(1)
    edit = reminder.get_by_role("link", name="Edit Reminder 1").bounding_box()
    delete = reminder.get_by_role("button", name="Delete Reminder 1").bounding_box()
    sizes = [(round(b["width"], 1), round(b["height"], 1)) for b in (edit, delete)]
    assert sizes[0] == sizes[1], sizes
    assert sizes[0][0] == sizes[0][1], sizes
    # Side by side on one line, top edges aligned.
    assert abs(edit["y"] - delete["y"]) < 0.5, (edit, delete)


def test_when_heading_sorts_the_table_in_place(page, component_origin):
    """Sending order reversed and back, without a reload."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    failures = watch(page)
    page.locator('th[data-sort-column="when"] .sort-link').click()
    has_attribute(
        page.locator('th[data-sort-column="when"]'), "aria-sort", "descending"
    )
    assert names(page) == [
        "Weekly Admin digest",
        "Reminder 2",
        "Reminder 1",
        "Initial invitation",
    ]
    assert "sort=-when" in page.url
    page.locator('th[data-sort-column="when"] .sort-link').click()
    has_attribute(page.locator('th[data-sort-column="when"]'), "aria-sort", "ascending")
    assert names(page)[0] == "Initial invitation"
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_delete_asks_in_a_dialog_then_redraws_the_table_in_place(
    page, component_origin, axe_source
):
    """Cancel changes nothing; Delete posts one ID and the list redraws."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    failures = watch(page)
    delete = page.get_by_role("button", name="Delete Reminder 1")
    identifier = delete.get_attribute("value")
    delete.click()
    visible(dialog(page))
    assert dialog(page).locator("h2").inner_text() == "Delete Reminder 1?"
    assert "never recalled" in dialog(page).inner_text()
    # The safe choice has focus, and Tab stays inside the dialog.
    assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
    page.keyboard.press("Tab")
    assert page.evaluate("Boolean(document.activeElement.closest('dialog'))")
    assert axe_violations(page, axe_source) == []
    page.keyboard.press("Escape")
    hidden(dialog(page))
    eventually(
        page,
        "document.activeElement.getAttribute('aria-label') === 'Delete Reminder 1'",
    )
    assert rows(page).count() == 4
    delete.click()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog(page).get_by_role("button", name="Delete", exact=True).click()
    fields = posted(sent.value)
    assert fields["schedule_id"] == [identifier]
    assert fields["base_digest"] == ["a" * 64]
    assert "csrfmiddlewaretoken" in fields
    # Applied: the table is redrawn without that reminder, nothing reloads.
    has_count(rows(page), 3)
    hidden(dialog(page))
    assert names(page) == ["Initial invitation", "Reminder 1", "Weekly Admin digest"]
    assert page.locator(f'button[value="{identifier}"]').count() == 0
    eventually(page, "document.activeElement.id === 'schedule-table-title'")
    visible(page.locator("main > [role=status]", has_text="Deleted Reminder 1."))
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_delete_selected_names_the_count(page, component_origin):
    """Only unsent rows can be ticked; the dialog names how many go."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + BULK)
    failures = watch(page)
    bulk = page.get_by_role("button", name="Delete selected")
    assert bulk.is_disabled()
    page.locator("input[data-select-all]").check()
    boxes = page.locator("input[data-select-row]")
    assert boxes.count() == 3
    # Keep the digest: tick only the two reminders.
    boxes.nth(2).uncheck()
    bulk.click()
    visible(dialog(page))
    assert dialog(page).locator("h2").inner_text() == "Delete 2 scheduled emails?"
    with page.expect_request(lambda request: request.method == "POST") as sent:
        dialog(page).get_by_role("button", name="Delete", exact=True).click()
    assert len(posted(sent.value)["schedule_id"]) == 2
    has_count(rows(page), 2)
    assert names(page) == ["Initial invitation", "Weekly Admin digest"]
    visible(
        page.locator("main > [role=status]", has_text="Deleted 2 scheduled emails.")
    )
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_refused_delete_explains_why_inside_the_dialog(page, component_origin):
    """The server's reason shows below the buttons; nothing is redrawn."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + REFUSED)
    failures = watch(page)
    page.get_by_role("button", name="Delete Reminder 1").click()
    accept = dialog(page).get_by_role("button", name="Delete", exact=True)
    before = accept.bounding_box()
    accept.click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    assert REFUSAL in error.inner_text()
    # The reason appears below the buttons, so they did not move.
    assert accept.bounding_box() == before
    assert accept.is_disabled()
    assert rows(page).count() == 4
    dialog(page).get_by_role("button", name="Cancel").click()
    hidden(dialog(page))
    eventually(
        page,
        "document.activeElement.getAttribute('aria-label') === 'Delete Reminder 1'",
    )
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_change_that_did_not_apply_is_reported(page, component_origin):
    """A recorded change that failed says so and links its status."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + FAILED)
    failures = watch(page)
    page.get_by_role("button", name="Delete Reminder 1").click()
    dialog(page).get_by_role("button", name="Delete", exact=True).click()
    error = dialog(page).locator("[data-confirm-error]")
    visible(error)
    assert "Not applied" in error.inner_text()
    visible(error.get_by_role("link", name="See the change's status"))
    assert rows(page).count() == 4
    assert not failures


def choose(page, name, value):
    """Choose ``value`` in the schedule field ``name``."""
    page.locator(f'[name="schedule-{name}"]').select_option(value)


def email_for(page, kind):
    """The value of the first email offered for ``kind``."""
    return page.locator(
        f'[name="schedule-template_version"] option[data-kind="{kind}"]'
    ).first.get_attribute("value")


def test_new_scheduled_email_posts_one_schedule(page, component_origin, axe_source):
    """Repeat is offered only for a Reminder; an invitation posts one date."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    repeat = page.get_by_label("Repeat", exact=True)
    hidden(repeat)
    choose(page, "kind", "reminder")
    visible(repeat)
    choose(page, "kind", "initial")
    hidden(repeat)
    visible(page.locator('[name="schedule-date"]'))
    visible(page.get_by_text("time zone: America/Los_Angeles."))
    assert "campaign's time zone" not in page.locator("main").inner_text()
    assert axe_violations(page, axe_source) == []
    page.locator('[name="schedule-date"]').fill("2054-10-02")
    page.locator('[name="schedule-time"]').fill("9am")
    email = email_for(page, "initial")
    choose(page, "template_version", email)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Review and save").click()
    fields = posted(sent.value)
    assert fields["schedule-kind"] == ["initial"]
    assert fields["schedule-date"] == ["2054-10-02"]
    # Typed in this browser's zone, which the page names and posts.
    assert fields["zone"] == ["America/Los_Angeles"]
    assert fields["schedule-template_version"] == [email]
    assert "repeat-dates" not in fields
    visible(page.get_by_role("heading", name="Review schedule changes"))
    assert not failures


def test_new_repeating_reminder_lists_and_posts_the_dates_that_fit(
    page, component_origin
):
    """Every Tuesday: the 20th clashes with Reminder 2, the rest are posted.

    Reminder 2 sends at 9:00 AM New York time, which is 6:00 AM here.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW)
    failures = watch(page)
    choose(page, "kind", "reminder")
    page.get_by_label("Repeat", exact=True).check()
    hidden(page.locator('[name="schedule-date"]'))
    # The rule starts with the campaign's dates.
    assert page.locator("#repeat-from").input_value() == "2054-10-01"
    assert page.locator("#repeat-until").input_value() == "2054-10-31"
    page.get_by_label("Tuesday").check()
    page.locator('[name="schedule-time"]').fill("06:00")
    choose(page, "template_version", email_for(page, "reminder"))
    summary = page.locator("[data-repeat-summary]")
    visible(summary)
    eventually(
        page,
        "document.querySelector('[data-repeat-summary]').textContent"
        " === '3 reminders to add, 1 not added.'",
    )
    dates = page.locator("[data-repeat-dates]")
    assert (
        "Tuesday, October 20, 2054: not added, another email is already "
        "scheduled then" in dates.inner_text()
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Review and save").click()
    fields = posted(sent.value)
    assert fields["zone"] == ["America/Los_Angeles"]
    assert fields["repeat-repeat"] == ["on"]
    assert fields["repeat-dates"] == ["2054-10-06", "2054-10-13", "2054-10-27"]
    assert fields["schedule-kind"] == ["reminder"]
    visible(page.get_by_role("heading", name="Review schedule changes"))
    assert not failures


def test_change_scheduled_email_is_filled_in_and_saves_that_schedule(
    page, component_origin, axe_source
):
    """Reminder 1's values in this browser's zone; its mail type fixed."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT)
    failures = watch(page)
    visible(page.get_by_role("heading", name="Edit scheduled email", level=1))
    visible(page.locator("legend", has_text="Reminder 1"))
    kind = page.locator('[name="schedule-kind"]')
    assert kind.is_disabled() and kind.input_value() == "reminder"
    # 9:00 AM in New York is 6:00 AM here.
    assert page.locator('[name="schedule-date"]').input_value() == "2054-10-10"
    assert page.locator('[name="schedule-time"]').input_value() == "06:00"
    assert page.locator('[name="schedule-template_version"]').input_value()
    assert page.locator("[data-repeat-entry]").count() == 0
    assert axe_violations(page, axe_source) == []
    page.locator('[name="schedule-time"]').fill("10:30")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Review and save").click()
    fields = posted(sent.value)
    assert fields["schedule-time"] == ["10:30"]
    assert fields["schedule-date"] == ["2054-10-10"]
    assert "schedule-kind" not in fields
    assert not any(name.startswith("repeat-") for name in fields)
    visible(page.get_by_role("heading", name="Review schedule changes"))
    assert not failures


def test_an_unchanged_edit_says_nothing_has_changed(page, component_origin):
    """Saved as shown, Edit answers with its own plain message in place.

    Not the review's catch-all rule text, which would suggest a schedule
    breaks a campaign rule; the fields keep what was posted.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_UNCHANGED)
    failures = watch(page)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Review and save").click()
    fields = posted(sent.value)
    assert fields["schedule-date"] == ["2054-10-10"]
    assert fields["schedule-time"] == ["06:00"]
    summary = page.locator("[data-error-summary]")
    visible(summary)
    assert (
        "Nothing has changed. Change the date, time or email, then save."
        in summary.inner_text()
    )
    main = page.locator("main").inner_text()
    assert "must fit the campaign" not in main
    visible(page.get_by_role("heading", name="Edit scheduled email", level=1))
    assert page.locator('[name="schedule-date"]').input_value() == "2054-10-10"
    assert page.locator('[name="schedule-time"]').input_value() == "06:00"
    assert not page.get_by_role("button", name="Review and save").is_disabled()
    assert not failures


def test_edit_fills_and_posts_times_in_a_zone_a_day_ahead(
    browser_engine, component_origin
):
    """In Auckland, a 9:00 AM New York send is 2:00 AM the next day.

    Reminder 1 (Saturday, October 10, 9:00 AM in New York) shows as October
    11 at 02:00, and the weekly digest (Monday 9:00 AM) as Tuesday 02:00;
    what is posted is what is shown, with the zone, for the server to
    convert back.
    """
    context = browser_engine.new_context(timezone_id="Pacific/Auckland")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(component_origin + EDIT)
        failures = watch(page)
        visible(page.get_by_text("time zone: Pacific/Auckland."))
        assert page.locator('[name="schedule-date"]').input_value() == "2054-10-11"
        assert page.locator('[name="schedule-time"]').input_value() == "02:00"
        reading = page.locator("#id_schedule-time_reading")
        assert "02:00 (2:00 AM)" in reading.inner_text()
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Review and save").click()
        fields = posted(sent.value)
        assert fields["schedule-date"] == ["2054-10-11"]
        assert fields["schedule-time"] == ["02:00"]
        assert fields["zone"] == ["Pacific/Auckland"]
        visible(page.get_by_role("heading", name="Review schedule changes"))
        page.goto(component_origin + EDIT_DIGEST)
        assert page.locator('[name="schedule-weekday"]').input_value() == "1"
        assert page.locator('[name="schedule-time"]').input_value() == "02:00"
        assert not failures
    finally:
        context.close()


def test_a_repeat_compares_moments_in_a_zone_a_day_ahead(
    browser_engine, component_origin
):
    """In Auckland, Reminder 2 (October 20, 9:00 AM New York) is the 21st.

    Every Wednesday at 02:00: the 21st clashes with Reminder 2 at the same
    moment; October 7, 14 and 28 are posted.
    """
    context = browser_engine.new_context(timezone_id="Pacific/Auckland")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(component_origin + NEW)
        failures = watch(page)
        choose(page, "kind", "reminder")
        page.get_by_label("Repeat", exact=True).check()
        # A pure date is never shifted: the campaign's own dates.
        assert page.locator("#repeat-from").input_value() == "2054-10-01"
        assert page.locator("#repeat-until").input_value() == "2054-10-31"
        page.get_by_label("Wednesday").check()
        page.locator('[name="schedule-time"]').fill("02:00")
        choose(page, "template_version", email_for(page, "reminder"))
        eventually(
            page,
            "document.querySelector('[data-repeat-summary]').textContent"
            " === '3 reminders to add, 1 not added.'",
        )
        assert (
            "Wednesday, October 21, 2054: not added, another email is already "
            "scheduled then" in page.locator("[data-repeat-dates]").inner_text()
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Review and save").click()
        fields = posted(sent.value)
        assert fields["repeat-dates"] == ["2054-10-07", "2054-10-14", "2054-10-28"]
        assert fields["zone"] == ["Pacific/Auckland"]
        visible(page.get_by_role("heading", name="Review schedule changes"))
        assert not failures
    finally:
        context.close()


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_new_waits_for_a_known_browser_zone(page, component_origin, reported):
    """Without a browser zone the send time can't be read: Review and save
    stays unavailable and the page says why (#558)."""
    # Init scripts run before the page's own and are not subject to its CSP.
    page.add_init_script(
        """{
        const original = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            return {...original.call(this), timeZone: ZONE};
        };
    }""".replace("ZONE", json.dumps(reported))
    )
    page.goto(component_origin + NEW)
    failures = watch(page)
    missing = page.locator("[data-zone-missing]")
    visible(missing)
    hidden(page.locator("[data-browser-zone-note]"))
    save = page.get_by_role("button", name="Review and save")
    assert save.is_disabled()
    assert "schedule-zone-missing" in save.get_attribute("aria-describedby")
    # Typing a readable time does not make it available.
    choose(page, "kind", "daily_digest")
    page.locator('[name="schedule-time"]').fill("9am")
    page.locator('[name="schedule-time"]').blur()
    assert save.is_disabled()
    assert not failures
