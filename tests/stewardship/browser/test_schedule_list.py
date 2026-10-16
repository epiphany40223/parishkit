"""Dates and mail schedules' list and its New and Edit page (#878, #879).

The list sorts by When in place, deletes one or several schedules through the
shared confirmation dialog and redraws itself without a reload; New and
Edit scheduled email post one schedule, or one reminder per repeat date, in
the browser's time zone (#558): the campaign is in New York's zone and the
default page is Los Angeles's, three hours behind. Their Review changes is
available only for a real change and opens the shared review dialog (#750),
whose Apply returns to the list with its status line. The date-change
review (proposed campaign dates with every schedule's editor) reviews and
applies in the same dialog, then returns to the list the same way.
The pages and their POST answers are schedule_list_components'.
"""

import json
import re
from urllib.parse import parse_qs

import pytest

from .schedule_list_components import (
    APPLY,
    APPLY_PASSED,
    BULK,
    DATE_REFUSAL,
    DATES,
    DATES_END,
    DATES_INVALID,
    DATES_STALE,
    DAY_MOVED_TEXT,
    EDIT,
    EDIT_CLOSED,
    EDIT_DIGEST,
    EDIT_EMAIL_REFUSED,
    EDIT_INITIAL,
    EDIT_PASSED,
    EDIT_SERIES,
    EDIT_SERIES_GATED,
    EMAIL_REFUSAL,
    FAILED,
    GATED_REASON,
    LIST,
    NEW,
    NEW_BLOCKED,
    NEW_INVALID,
    NEW_PENDING,
    NEW_REPEAT,
    NEW_STALE,
    NOT_OPEN,
    PASSED_REFUSAL,
    REFUSAL,
    REFUSED,
    STALE_REFUSAL,
    TEST_SEND,
)
from .test_components import axe_violations
from .waits import eventually, has_attribute, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REVIEW = "Review changes"
# New scheduled email's button carries the action's own label (#1356).
ADD = "Add the scheduled email"
APPLIED_LINE = re.compile(r"Applied at \d{1,2}:\d{2}\s?[AP]M\.$")
FOCUSED = "document.activeElement && document.activeElement.id"


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
    # A blank form is nothing to review.
    assert page.get_by_role("button", name=ADD).is_disabled()
    visible(page.get_by_text("Fill in the scheduled email to add it."))
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
        page.get_by_role("button", name=ADD).click()
    fields = posted(sent.value)
    assert fields["schedule-kind"] == ["initial"]
    assert fields["schedule-date"] == ["2054-10-02"]
    # Typed in this browser's zone, which the page names and posts.
    assert fields["zone"] == ["America/Los_Angeles"]
    assert fields["schedule-template_version"] == [email]
    assert "repeat-dates" not in fields
    # One schedule that sends once: a confirmation (#1356).
    dialog = page.get_by_role("dialog", name="Add this scheduled email?")
    visible(dialog)
    assert page.evaluate(FOCUSED) == "schedule-review-title"
    visible(dialog.get_by_role("button", name="Apply"))
    assert (
        page.evaluate(
            "document.getElementById('schedule-review').getAttribute('aria-describedby')"
        )
        == "schedule-review-summary"
    )
    assert page.evaluate(
        "document.getElementById('schedule-review-summary').textContent"
    ).startswith("This schedules the email to send once")
    assert "Reminder — Add" not in dialog.inner_text()
    assert "Current schedule" not in dialog.inner_text()
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_repeat_after_a_confirmation_gets_the_full_review(page, component_origin):
    """One dialog serves both kinds (#1356): a full review after a
    confirmation has the dialog's own title back and no description."""
    fill_new(page, component_origin)
    failures = watch(page)
    shown = review(page)
    visible(page.get_by_role("dialog", name="Add this scheduled email?"))
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)
    eventually(page, f"document.activeElement.textContent.trim() === '{ADD}'")
    # The same form, answered as a Repeat is (NEW_REPEAT's thirty reminders).
    page.evaluate(
        "document.getElementById('schedule-entry-form')"
        f".setAttribute('action', '{NEW_REPEAT}/review')"
    )
    shown = review(page)
    visible(page.get_by_role("dialog", name="Review new scheduled email"))
    assert shown.inner_text().count("Reminder — Add") == 30
    assert shown.get_attribute("aria-describedby") is None
    assert page.evaluate("window.notReloaded === true")
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
        page.get_by_role("button", name=ADD).click()
    fields = posted(sent.value)
    assert fields["zone"] == ["America/Los_Angeles"]
    assert fields["repeat-repeat"] == ["on"]
    assert fields["repeat-dates"] == ["2054-10-06", "2054-10-13", "2054-10-27"]
    assert fields["schedule-kind"] == ["reminder"]
    # The fixture answers every New Review alike; the server answers a
    # Repeat with its full review (test_a_repeat_after_a_confirmation...).
    visible(page.locator("dialog[data-review-dialog]"))
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
    # Nothing to review until a value changes.
    assert page.get_by_role("button", name=REVIEW).is_disabled()
    assert axe_violations(page, axe_source) == []
    page.locator('[name="schedule-time"]').fill("10:30")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=REVIEW).click()
    fields = posted(sent.value)
    assert fields["schedule-time"] == ["10:30"]
    assert fields["schedule-date"] == ["2054-10-10"]
    assert "schedule-kind" not in fields
    assert not any(name.startswith("repeat-") for name in fields)
    dialog = page.get_by_role("dialog", name="Review scheduled email change")
    visible(dialog)
    assert "Reminder — Update" in dialog.inner_text()
    assert not failures


def test_edit_review_waits_for_a_change_and_greys_again_on_revert(
    page, component_origin
):
    """No review of nothing (#750): Review changes is unavailable until a
    value differs from the saved schedule as this browser shows it, and again
    once it is put back, even when typed another way."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT)
    failures = watch(page)
    button = page.get_by_role("button", name=REVIEW)
    assert button.is_disabled()
    visible(
        page.get_by_text("Change the date, time, email or comparison to review it.")
    )
    time = page.locator('[name="schedule-time"]')
    time.fill("10:30")
    assert button.is_enabled()
    time.fill("06:00")
    assert button.is_disabled()
    # "6am" is the saved 06:00 once it is written out on leaving the field.
    time.fill("6am")
    time.blur()
    assert time.input_value() == "06:00"
    assert button.is_disabled()
    date = page.locator('[name="schedule-date"]')
    date.fill("2054-10-11")
    assert button.is_enabled()
    date.fill("2054-10-10")
    assert button.is_disabled()
    # The weekly digest's weekday, filled in by the script, is saved too.
    page.goto(component_origin + EDIT_DIGEST)
    button = page.get_by_role("button", name=REVIEW)
    assert button.is_disabled()
    weekday = page.locator('[name="schedule-weekday"]')
    saved = weekday.input_value()
    weekday.select_option("3")
    assert button.is_enabled()
    weekday.select_option(saved)
    assert button.is_disabled()
    assert not failures


def test_a_series_reminder_changes_this_and_following_in_place(
    page, component_origin, axe_source
):
    """Part of its series (#469): choosing a scope greys the date in place,
    showing this reminder's own, is no change by itself, and Review posts
    the scope without the date; the review says what happens to the series."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_SERIES)
    failures = watch(page)
    choice = page.get_by_role("group", name="Apply this edit to", exact=False)
    visible(choice)
    # "Part of" describes the choice, so a screen reader hears it there.
    described = choice.get_attribute("aria-describedby").split()
    assert page.locator(f"#{described[0]}").inner_text() == (
        "Part of: Every day, October 1, 2054 – October 31, 2054"
    )
    date = page.locator('[name="schedule-date"]')
    time = page.locator('[name="schedule-time"]')
    this = page.get_by_role("radio", name="This reminder only")
    assert this.is_checked() and date.is_enabled()
    button = page.get_by_role("button", name=REVIEW)
    assert button.is_disabled()
    # A date typed first is not used by a series scope: the box shows the
    # reminder's own again (Los Angeles, a day the same as New York's).
    date.fill("2054-10-11")
    places = [node.bounding_box() for node in (date, time, button)]
    page.get_by_role("radio", name="This and following").check()
    # Greyed in place, not hidden: nothing moves, and a scope is no change.
    assert date.is_disabled() and date.is_visible()
    assert date.input_value() == "2054-10-10"
    visible(page.get_by_text("Used only with \u201cThis reminder only\u201d"))
    assert same_place([node.bounding_box() for node in (date, time, button)], places)
    assert button.is_disabled()
    assert axe_violations(page, axe_source) == []
    this.check()
    assert date.is_enabled()
    page.get_by_role("radio", name="The whole series").check()
    time.fill("10:30")
    assert button.is_enabled()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        button.click()
    fields = posted(sent.value)
    assert fields["series-scope"] == ["series"]
    assert fields["schedule-time"] == ["10:30"]
    assert "schedule-date" not in fields
    dialog = page.get_by_role("dialog", name="Review scheduled email change")
    visible(dialog)
    assert (
        "From October 10, 2054, every reminder of this series sends at 10:30 AM"
        in dialog.inner_text()
    )
    # A reminder outside a series has no scope.
    page.goto(component_origin + EDIT)
    assert page.locator('[name="series-scope"]').count() == 0
    assert not failures


def test_an_unavailable_scope_says_why_and_a_refusal_shows_at_the_choice(
    page, component_origin, axe_source
):
    """A scope the server would refuse is unavailable with its reason
    (prerequisite gates); a refusal of the typed time stays on the form."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_SERIES_GATED)
    failures = watch(page)
    whole = page.get_by_role("radio", name="The whole series")
    assert whole.is_disabled()
    reason = page.locator(f"#{whole.get_attribute('aria-describedby')}")
    assert reason.inner_text() == f"Unavailable: {GATED_REASON}"
    page.get_by_role("radio", name="This and following").check()
    page.locator('[name="schedule-time"]').fill("23:00")
    page.get_by_role("button", name=REVIEW).click()
    error = page.locator("#id_series-scope_error")
    visible(error)
    assert error.inner_text() == DAY_MOVED_TEXT
    assert (
        page.get_by_role("radio", name="This and following").get_attribute(
            "aria-invalid"
        )
        == "true"
    )
    assert not page.get_by_role(
        "dialog", name="Review scheduled email change"
    ).is_visible()
    assert axe_violations(page, axe_source) == []
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
        # As shown it is unchanged; a later time is a change to review.
        assert page.get_by_role("button", name=REVIEW).is_disabled()
        page.locator('[name="schedule-time"]').fill("03:00")
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name=REVIEW).click()
        fields = posted(sent.value)
        assert fields["schedule-date"] == ["2054-10-11"]
        assert fields["schedule-time"] == ["03:00"]
        assert fields["zone"] == ["Pacific/Auckland"]
        visible(page.get_by_role("dialog", name="Review scheduled email change"))
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
            page.get_by_role("button", name=ADD).click()
        fields = posted(sent.value)
        assert fields["repeat-dates"] == ["2054-10-07", "2054-10-14", "2054-10-28"]
        assert fields["zone"] == ["Pacific/Auckland"]
        visible(page.locator("dialog[data-review-dialog]"))
        assert not failures
    finally:
        context.close()


@pytest.mark.parametrize("reported", ["", "Etc/Unknown"])
def test_new_waits_for_a_known_browser_zone(page, component_origin, reported):
    """Without a browser zone the send time can't be read: Review changes
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
    save = page.get_by_role("button", name=ADD)
    assert save.is_disabled()
    assert "schedule-zone-missing" in save.get_attribute("aria-describedby")
    # Typing a readable time does not make it available.
    choose(page, "kind", "daily_digest")
    page.locator('[name="schedule-time"]').fill("9am")
    page.locator('[name="schedule-time"]').blur()
    assert save.is_disabled()
    assert not failures


def fill_new(page, origin, path=NEW):
    """New scheduled email filled in as an Initial invitation."""
    page.goto(origin + path)
    choose(page, "kind", "initial")
    page.locator('[name="schedule-date"]').fill("2054-10-02")
    page.locator('[name="schedule-time"]').fill("9am")
    choose(page, "template_version", email_for(page, "initial"))


def review(page, name=ADD):
    """Choose New's Add the scheduled email (or ``name``); wait for the dialog."""
    page.get_by_role("button", name=name).click()
    shown = page.locator("dialog[data-review-dialog]")
    visible(shown)
    return shown


def near(first, second):
    """Whether two positions are the same within a pixel."""
    return abs(first - second) <= 1


@pytest.mark.parametrize("width", [320, 1280])
def test_a_long_repeat_review_scrolls_inside_the_dialog(
    page, component_origin, axe_source, width
):
    """Thirty repeated reminders: the review scrolls in the dialog's body
    while its title and its Apply and Cancel stay in view."""
    page.set_viewport_size({"width": width, "height": 640})
    fill_new(page, component_origin, NEW_REPEAT)
    failures = watch(page)
    shown = review(page)
    assert shown.inner_text().count("Reminder — Add") == 30
    body = shown.locator("[data-review-body]")
    assert body.evaluate("node => node.scrollHeight > node.clientHeight")
    box = shown.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] >= 0 and box["y"] + box["height"] <= 640
    apply = shown.get_by_role("button", name="Apply")
    before = apply.bounding_box()["y"]
    body.evaluate("node => { node.scrollTop = node.scrollHeight; }")
    for name in ("Apply", "Cancel"):
        edge = shown.get_by_role("button", name=name).bounding_box()
        assert edge["y"] + edge["height"] <= 640, name
    assert near(apply.bounding_box()["y"], before)
    visible(shown.get_by_role("heading", name="Review new scheduled email"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_apply_returns_to_the_list_with_the_status_line(page, component_origin):
    """Applied: the dialog closes and the list says so in its reserved line,
    which takes focus without moving the table."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    table = page.locator("#schedule-table-title").bounding_box()["y"]
    fill_new(page, component_origin)
    shown = review(page)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(APPLY)
    fields = posted(sent.value)
    assert fields["action"] == ["confirm"]
    assert fields["preview"] == ["synthetic-preview"]
    assert set(fields) == {"csrfmiddlewaretoken", "action", "preview"}
    page.wait_for_url(re.compile(re.escape(LIST) + "$"))
    eventually(page, "document.getElementById('schedules-outcome').textContent !== ''")
    line = page.locator("#schedules-outcome")
    assert line.inner_text().startswith("Scheduled email added. ")
    assert APPLIED_LINE.search(line.inner_text())
    assert line.get_attribute("role") == "status"
    assert page.evaluate(FOCUSED) == "schedules-outcome"
    assert near(page.locator("#schedule-table-title").bounding_box()["y"], table)


def test_an_apply_whose_reviewed_time_passed_is_refused_in_place(
    page, component_origin
):
    """A reviewed send time that passed before Apply (#1464): the server's
    refusal shows in the dialog, below its buttons, which do not move; the
    page is neither reloaded nor redrawn, and Cancel returns to the form
    with the typed time, ready to review again."""
    # Tall enough that the review and its refusal stay below the dialog's
    # 90% height limit: at that limit the shared dialog shrinks its
    # scrolling body for the refusal and the buttons rise (a shared-dialog
    # follow-up, #1482, not #1464's; drop this override when it lands).
    # With CI's Linux fonts this review's body is about 1,040 px tall, so
    # 1,400 px reached the limit in every engine; 1,800 px leaves room.
    page.set_viewport_size({"width": 1280, "height": 1800})
    page.goto(component_origin + EDIT_PASSED)
    failures = watch(page)
    time = page.locator('[name="schedule-time"]')
    time.fill("10:30")
    form = page.locator("#schedule-entry-form").element_handle()
    shown = review(page, REVIEW)
    cancel = shown.get_by_role("button", name="Cancel")
    places = [cancel.bounding_box()]
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(APPLY_PASSED)
    error = shown.locator("[data-confirm-error]")
    visible(error)
    assert PASSED_REFUSAL["message"] in error.inner_text()
    assert PASSED_REFUSAL["fix"] in error.inner_text()
    assert shown.is_visible()
    # The premise above: the body did not shrink to make room (#1482), so a
    # move below is this page's, not the shared dialog's height limit.
    body = shown.locator("[data-review-body]")
    assert body.evaluate("node => node.scrollHeight <= node.clientHeight")
    assert same_place([cancel.bounding_box()], places)
    cancel.click()
    hidden(shown)
    # The very form the reader used is still there, with the typed time.
    assert form.evaluate("node => node.isConnected")
    assert time.input_value() == "10:30"
    assert page.get_by_role("button", name=REVIEW).is_enabled()
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_once_applying_nothing_dismisses_the_dialog(page, component_origin):
    """No Escape and no Cancel until the answer; Apply goes once recorded."""
    fill_new(page, component_origin, NEW_PENDING)
    shown = review(page)
    shown.get_by_role("button", name="Apply").click()
    eventually(
        page,
        "document.querySelector('#schedule-review [data-confirm-accept]')"
        ".hasAttribute('data-recorded')",
    )
    assert shown.locator("[data-confirm-status]").inner_text() == "Applying…"
    assert shown.locator("[data-confirm-cancel]").is_disabled()
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    eventually(page, "document.getElementById('schedule-review').open")
    assert shown.is_visible()


def test_a_field_error_stays_on_the_form(page, component_origin):
    """A date after the campaign: the dialog stays shut, the date is marked
    with its message where the server draws it, and the summary has focus."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW_INVALID)
    failures = watch(page)
    choose(page, "kind", "reminder")
    date = page.locator('[name="schedule-date"]')
    date.fill("2054-11-15")
    page.locator('[name="schedule-time"]').fill("6am")
    choose(page, "template_version", email_for(page, "reminder"))
    page.get_by_role("button", name=ADD).click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    eventually(page, "document.activeElement?.matches('[data-error-summary]')")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    message = page.locator("#id_schedule-date_error")
    visible(message)
    assert message.inner_text() == DATE_REFUSAL
    # Just before the field, as on a page the server drew.
    assert message.evaluate("node => node.nextElementSibling.id") == "id_schedule-date"
    assert date.get_attribute("aria-invalid") == "true"
    assert "id_schedule-date_error" in date.get_attribute("aria-describedby")
    # The reader's values stay, and the form can be edited again.
    assert date.input_value() == "2054-11-15"
    assert not page.evaluate("document.getElementById('schedule-entry-form').inert")
    date.fill("2054-10-15")
    assert date.get_attribute("aria-invalid") == "false"
    hidden(message)
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_another_refusal_shows_in_the_dialog(page, component_origin):
    """While Review is prepared the form is inert and busy; a stale page's
    refusal then shows in the dialog, in the server's words, with no Apply."""
    fill_new(page, component_origin, NEW_STALE)
    failures = watch(page)
    held = []
    page.route(f"**{NEW_STALE}/review", lambda route: held.append(route))
    page.get_by_role("button", name=ADD).click()
    eventually(page, "document.getElementById('schedule-entry-form').inert")
    assert page.locator("#schedule-entry-form").get_attribute("aria-busy") == "true"
    eventually(page, "true")
    assert len(held) == 1
    held[0].continue_()
    shown = page.locator("dialog[data-review-dialog]")
    visible(shown)
    error = shown.locator("[data-confirm-error]")
    visible(error)
    assert STALE_REFUSAL in error.inner_text()
    assert shown.get_by_role("button", name="Apply").count() == 0
    assert page.evaluate(FOCUSED) == "schedule-review-title"
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)
    eventually(page, f"document.activeElement.textContent.trim() === '{ADD}'")
    assert page.locator('[name="schedule-date"]').input_value() == "2054-10-02"
    assert not failures


def test_a_blocked_change_has_no_apply(page, component_origin):
    """Sends in progress block the change: the dialog says why and offers
    only Cancel."""
    fill_new(page, component_origin, NEW_BLOCKED)
    shown = review(page)
    assert "Sends blocking the change: 2" in shown.inner_text()
    assert shown.get_by_role("button", name="Apply").count() == 0
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)


def test_tab_reaches_the_review_body_and_arrow_keys_scroll_it(page, component_origin):
    """The long repeat review holds nothing else to focus: Tab from the
    title reaches the body, and the keyboard scrolls it."""
    page.set_viewport_size({"width": 1280, "height": 640})
    fill_new(page, component_origin, NEW_REPEAT)
    shown = review(page)
    assert page.evaluate(FOCUSED) == "schedule-review-title"
    page.keyboard.press("Tab")
    eventually(
        page,
        "document.activeElement?.matches('[data-review-body]') === true",
    )
    body = shown.locator("[data-review-body]")
    assert body.evaluate("node => node.scrollTop") == 0
    # Playwright's WebKit ignores a synthetic arrow key for scrolling (Safari
    # itself scrolls on it), so there Page Down stands in for it.
    engine = page.context.browser.browser_type.name
    key = "PageDown" if engine == "webkit" else "ArrowDown"
    for _ in range(5):
        page.keyboard.press(key)
    eventually(page, "document.querySelector('[data-review-body]').scrollTop > 0")
    # Still the body, inside the open dialog.
    assert page.evaluate("document.getElementById('schedule-review').open")


def test_an_accepted_review_clears_an_earlier_field_error(page, component_origin):
    """A date refused, then a Review the server accepts without the date
    edited: the message placed at the date and its mark go."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + NEW_INVALID)
    failures = watch(page)
    choose(page, "kind", "reminder")
    date = page.locator('[name="schedule-date"]')
    date.fill("2054-11-15")
    page.locator('[name="schedule-time"]').fill("6am")
    choose(page, "template_version", email_for(page, "reminder"))
    page.get_by_role("button", name=ADD).click()
    message = page.locator("#id_schedule-date_error")
    visible(message)
    assert date.get_attribute("aria-invalid") == "true"
    # The next Review is answered with an accepted review.
    page.evaluate(
        "document.getElementById('schedule-entry-form')"
        f".setAttribute('action', '{NEW}/review')"
    )
    page.locator('[name="schedule-time"]').fill("7am")
    review(page)
    hidden(message)
    assert date.get_attribute("aria-invalid") == "false"
    assert "id_schedule-date_error" not in (
        date.get_attribute("aria-describedby") or ""
    )
    assert page.locator("[data-error-summary]").count() == 0
    assert not failures


DATES_REVIEW = "Review dates and mail schedules"
SAVED_END = "2054-10-31"


def test_date_change_review_waits_for_a_real_change(page, component_origin):
    """Proposed dates are a change; returning them to the saved dates, or
    adding a blank schedule, is none, and Review greys again (#750)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + DATES)
    failures = watch(page)
    button = page.get_by_role("button", name=REVIEW)
    end = page.locator('[name="window-end_date"]')
    assert end.input_value() == DATES_END
    eventually(
        page, "!document.querySelector('#schedule-dates-form [type=submit]').disabled"
    )
    hint = page.get_by_text("Change the dates or a schedule to review it.")
    hidden(hint)
    before = button.bounding_box()
    end.fill(SAVED_END)
    eventually(
        page, "document.querySelector('#schedule-dates-form [type=submit]').disabled"
    )
    visible(hint)
    assert near(button.bounding_box()["y"], before["y"])
    # A blank added schedule is nothing to review; choosing its type is.
    page.get_by_role("button", name="Add another schedule").click()
    assert button.is_disabled()
    added = page.locator("[data-change-blank]")
    added.locator('[data-schedule-field="kind"] select').select_option("reminder")
    eventually(
        page, "!document.querySelector('#schedule-dates-form [type=submit]').disabled"
    )
    page.get_by_role("button", name="Remove this new schedule").click()
    eventually(
        page, "document.querySelector('#schedule-dates-form [type=submit]').disabled"
    )
    end.fill(DATES_END)
    eventually(
        page, "!document.querySelector('#schedule-dates-form [type=submit]').disabled"
    )
    assert not failures


def test_date_change_review_applies_in_the_dialog_and_returns_to_the_list(
    page, component_origin, axe_source
):
    """Review posts the proposal; the dialog shows the new dates and the
    re-planned mail; Apply posts only the signed preview to the list, then
    returns there with the status line."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + DATES)
    failures = watch(page)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=REVIEW).click()
    fields = posted(sent.value)
    assert fields["action"] == ["preview"]
    assert fields["window-end_date"] == [DATES_END]
    shown = page.get_by_role("dialog", name=DATES_REVIEW)
    visible(shown)
    assert page.evaluate(FOCUSED) == "schedule-dates-review-title"
    text = shown.inner_text()
    assert "Campaign window" in text and "Reminder — Update" in text
    assert "Email already sent cannot be recalled." in text
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("window.notReloaded === true")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(LIST)
    fields = posted(sent.value)
    assert set(fields) == {"csrfmiddlewaretoken", "action", "preview"}
    assert fields["action"] == ["confirm"]
    assert fields["preview"] == ["synthetic-preview"]
    page.wait_for_url(re.compile(re.escape(LIST) + "$"))
    eventually(page, "document.getElementById('schedules-outcome').textContent !== ''")
    line = page.locator("#schedules-outcome")
    assert line.inner_text().startswith("Dates and mail schedules changed. ")
    assert APPLIED_LINE.search(line.inner_text())
    assert page.evaluate(FOCUSED) == "schedules-outcome"
    assert not failures


def test_a_refused_date_change_marks_the_schedule_on_the_form(page, component_origin):
    """A reminder left after the new end: no dialog; the summary has focus,
    and that reminder's editor opens with its date marked."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + DATES_INVALID)
    failures = watch(page)
    date = page.locator('[name="schedules-2-date"]')
    hidden(date)
    page.get_by_role("button", name=REVIEW).click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    eventually(page, "document.activeElement?.matches('[data-error-summary]')")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    visible(date)
    assert date.get_attribute("aria-invalid") == "true"
    visible(page.locator("#id_schedules-2-date_error"))
    assert summary.locator('a[href="#id_schedules-2-date"]').count() == 1
    assert not page.evaluate("document.getElementById('schedule-dates-form').inert")
    assert page.locator('[name="window-end_date"]').input_value() == DATES_END
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_stale_date_change_is_refused_in_the_dialog(page, component_origin):
    """Another tab's change: the refusal shows in the dialog with no Apply,
    and the form keeps the proposal."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + DATES_STALE)
    shown = review(page, REVIEW)
    error = shown.locator("[data-confirm-error]")
    visible(error)
    assert STALE_REFUSAL in error.inner_text()
    assert shown.get_by_role("button", name="Apply").count() == 0
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)
    assert page.locator('[name="window-end_date"]').input_value() == DATES_END


def generated_text(page):
    """Answer the plain-text preview as the server does, from the posted HTML.

    The text is the HTML with its tags removed, which is what the server's
    generation gives for these single-paragraph emails.
    """

    def answer(route):
        # An emptied email posts blank HTML, which parse_qs keeps only so.
        posted = parse_qs(route.request.post_data or "", keep_blank_values=True)
        html = posted["html"][0]
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"html": html, "text": re.sub("<[^>]+>", "", html), "removed": []}
            ),
        )

    page.route("**/content/plain-text", answer)


def page_top(locator):
    """Where ``locator`` sits on the page, whatever the scroll position."""
    return locator.evaluate(
        "(node) => node.getBoundingClientRect().top + window.scrollY"
    )


def test_edit_offers_the_schedules_own_email_below_its_choice(
    page, component_origin, axe_source
):
    """Ticking fills the fields from the chosen email; nothing above moves.

    Choosing the other reminder email fills them again until they are
    edited, after which a choice keeps the typed text; Reset to the default
    text fills the default. The fields post only while the box is ticked.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT)
    failures = watch(page)
    box = page.get_by_label("Edit this schedule's email")
    subject = page.locator('[name="subject"]')
    html = page.locator('[name="html"]')
    text = page.locator('[name="text"]')
    hidden(subject)
    choice = page.locator('[name="schedule-template_version"]')
    above, box_at = page_top(choice), page_top(box)
    box.check()
    visible(subject)
    # Opening the fields moves nothing above them or under the pointer.
    assert (page_top(choice), page_top(box)) == (above, box_at)
    # Reminder 1 sends the second reminder email, with hand-written plain
    # text, so generation starts unticked.
    generate = page.get_by_label("Generate plain text from HTML")
    assert subject.input_value() == "reminder mail"
    assert "A second reminder." in html.input_value()
    assert not generate.is_checked()
    assert text.input_value().startswith("Hand-written: a second reminder.")
    assert axe_violations(page, axe_source) == []
    # The other reminder email fills the fields while they are unedited.
    saved = choice.input_value()
    other = [
        value
        for value in choice.locator('option[data-kind="reminder"]').evaluate_all(
            "(nodes) => nodes.map((node) => node.value)"
        )
        if value != saved
    ][0]
    choice.select_option(other)
    assert "Welcome to {{ parish_name }}." in html.input_value()
    assert generate.is_checked()
    # Typed text is kept when another email is chosen.
    subject.fill("Last call")
    choice.select_option(saved)
    assert subject.input_value() == "Last call"
    assert "Welcome to {{ parish_name }}." in html.input_value()
    reset = page.get_by_role("button", name="Reset to the default text")
    reset_at = page_top(reset)
    reset.click()
    assert page_top(reset) == reset_at
    assert subject.input_value() not in {"Last call", "reminder mail", ""}
    assert "{{ family_code }}" in html.input_value()
    # Generated plain text follows the HTML as it is typed.
    html.fill("<p>Still time. {{ family_code }} {{ family_url }}</p>")
    eventually(
        page,
        "document.querySelector('[name=\"text\"]').value"
        " === 'Still time. {{ family_code }} {{ family_url }}'",
    )
    subject.fill("Last call")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=REVIEW).click()
    fields = posted(sent.value)
    assert fields["edit_email"] == ["on"]
    assert fields["subject"] == ["Last call"]
    assert fields["html"] == ["<p>Still time. {{ family_code }} {{ family_url }}</p>"]
    assert fields["generate_text"] == ["on"]
    # Generated text is the server's to make; it is not posted.
    assert "text" not in fields
    visible(page.locator("dialog[data-review-dialog]"))
    assert not failures


def test_an_unticked_email_editor_posts_no_email_fields(page, component_origin):
    """Ticked and unticked again, the page posts only the schedule."""
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT)
    failures = watch(page)
    box = page.get_by_label("Edit this schedule's email")
    box.check()
    page.locator('[name="subject"]').fill("Changed")
    box.uncheck()
    hidden(page.locator('[name="subject"]'))
    page.locator('[name="schedule-time"]').fill("10:30")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=REVIEW).click()
    fields = posted(sent.value)
    for name in ("edit_email", "subject", "html", "generate_text", "text"):
        assert name not in fields
    assert fields["schedule-time"] == ["10:30"]
    assert not failures


def test_new_offers_the_email_of_the_chosen_mail_type(page, component_origin):
    """New's own email follows the mail type and email chosen above it.

    Ticked, the fields fill from the chosen reminder email; another mail type
    narrows Email to send to nothing chosen, which empties the unedited
    fields rather than leave a reminder's text for a digest; Reset fills the
    chosen type's default, and edited text then stays while the type
    changes. The fields post with New's schedule.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + NEW)
    failures = watch(page)
    box = page.get_by_label("Edit this schedule's email")
    subject = page.locator('[name="subject"]')
    html = page.locator('[name="html"]')
    kind = page.locator('[name="schedule-kind"]')
    choice = page.locator('[name="schedule-template_version"]')
    reset = page.get_by_role("button", name="Reset to the default text")
    reason = page.locator("[data-schedule-email-reason]")
    hidden(subject)
    # No mail type yet: the checkbox is unavailable and its reserved line
    # says why; once a type is chosen the line idles but keeps its space, so
    # the fields below it do not move up.
    assert box.is_disabled() and reason.is_visible()
    assert "schedule-email-reason" in (box.get_attribute("aria-describedby") or "")
    space = reason.bounding_box()["height"]
    kind.select_option("reminder")
    assert box.is_enabled() and not reason.is_visible()
    assert reason.bounding_box()["height"] == space > 0
    # The reason leaves the description; the checkbox's own help stays.
    assert box.get_attribute("aria-describedby") == "id_edit_email_helptext"
    first = choice.locator('option[data-kind="reminder"]').first.get_attribute("value")
    choice.select_option(first)
    box.check()
    visible(subject)
    assert subject.input_value() == "reminder mail"
    # Another mail type leaves no email chosen: the unedited fields empty.
    kind.select_option("daily_digest")
    assert choice.input_value() == ""
    assert (subject.input_value(), html.input_value()) == ("", "")
    reset.click()
    assert "{{ family_code }}" not in html.input_value()
    digest = subject.input_value()
    assert digest
    # Reset text is the reader's: changing the type keeps it.
    kind.select_option("reminder")
    assert subject.input_value() == digest
    reset.click()
    assert "{{ family_code }}" in html.input_value()
    # Clearing the mail type unticks the box and closes the fields.
    kind.select_option("")
    assert not box.is_checked() and box.is_disabled()
    assert page.locator("[data-schedule-email-reset]").is_disabled()
    hidden(subject)
    kind.select_option("reminder")
    box.check()
    # Unticking keeps what was typed, so the reset text is still there.
    assert "{{ family_code }}" in html.input_value()
    choice.select_option(first)
    page.locator('[name="schedule-date"]').fill("2054-10-15")
    page.locator('[name="schedule-time"]').fill("09:00")
    subject.fill("Last call")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=ADD).click()
    fields = posted(sent.value)
    assert fields["edit_email"] == ["on"]
    assert fields["subject"] == ["Last call"]
    assert fields["schedule-kind"] == ["reminder"]
    assert "{{ family_code }}" in fields["html"][0]
    assert not failures


def test_a_never_ticked_email_editor_posts_no_email_fields(page, component_origin):
    """Hand-written plain text stays unsent while the box was never ticked.

    Reminder 1's email has hand-written plain text, so "Generate plain text
    from HTML" starts unticked and the plain-text panel leaves its field
    editable; inside the hidden group it must stay disabled even after the
    first load's pageshow.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT)
    failures = watch(page)
    assert not page.get_by_label("Edit this schedule's email").is_checked()
    assert page.locator('[name="text"]').is_disabled()
    page.locator('[name="schedule-time"]').fill("10:30")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name=REVIEW).click()
    fields = posted(sent.value)
    for name in ("edit_email", "subject", "html", "generate_text", "text"):
        assert name not in fields
    assert fields["schedule-time"] == ["10:30"]
    assert not failures


def test_the_schedules_own_email_counts_toward_review_changes(page, component_origin):
    """The email's fields count as changes; the checkbox alone does not.

    Ticking "Edit this schedule's email" shows the saved email's text, which
    is no change; editing it makes Review changes available, and putting it
    back or unticking the box makes it unavailable again (#446, #750).
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT)
    failures = watch(page)
    button = page.get_by_role("button", name=REVIEW)
    assert button.is_disabled()
    box = page.get_by_label("Edit this schedule's email")
    box.check()
    subject = page.locator('[name="subject"]')
    visible(subject)
    assert button.is_disabled()
    subject.fill("Last call")
    assert button.is_enabled()
    subject.fill("reminder mail")
    assert button.is_disabled()
    html = page.locator('[name="html"]')
    saved = html.input_value()
    html.fill(saved.replace("A second reminder.", "Still time."))
    assert button.is_enabled()
    box.uncheck()
    assert button.is_disabled()
    assert not failures


def test_a_refused_email_is_marked_at_its_html_source(page, component_origin):
    """A refusal of the whole edited email stays on the form, at the HTML.

    The dialog stays shut; the message is placed before the HTML source, which
    is marked invalid and described by it, and the summary links to it.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT_EMAIL_REFUSED)
    failures = watch(page)
    page.get_by_label("Edit this schedule's email").check()
    page.locator('[name="subject"]').fill("Refused")
    page.get_by_role("button", name=REVIEW).click()
    summary = page.locator("[data-error-summary]")
    visible(summary)
    eventually(page, "document.activeElement?.matches('[data-error-summary]')")
    assert not page.locator("dialog[data-review-dialog]").is_visible()
    html = page.locator('[name="html"]')
    message = page.locator("#id_html_error")
    visible(message)
    assert message.inner_text() == EMAIL_REFUSAL
    assert html.get_attribute("aria-invalid") == "true"
    assert "id_html_error" in html.get_attribute("aria-describedby")
    link = summary.locator('a[href="#id_html"]')
    assert link.inner_text() == EMAIL_REFUSAL
    link.click()
    eventually(page, "document.activeElement?.name === 'html'")
    # The reader's text stays.
    assert page.locator('[name="subject"]').input_value() == "Refused"
    assert not failures


def test_regenerated_plain_text_is_no_change(page, component_origin):
    """Generated plain text is not sent, so the gate does not count it.

    The Initial invitation's email opens with "Generate plain text from HTML"
    ticked; today's generation answers differently from its saved text (an
    older email), and ticking the box alone still leaves Review unavailable.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.route(
        "**/content/plain-text",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"html": "", "text": "Regenerated.", "removed": []}),
        ),
    )
    page.goto(component_origin + EDIT_INITIAL)
    failures = watch(page)
    button = page.get_by_role("button", name=REVIEW)
    text = page.locator('[name="text"]')
    # Its saved text, which the generated preview replaces on load.
    saved = text.evaluate("(node) => node.defaultValue")
    assert saved != "Regenerated."
    page.get_by_label("Edit this schedule's email").check()
    assert page.get_by_label("Generate plain text from HTML").is_checked()
    eventually(
        page, "document.querySelector('[name=\"text\"]').value === 'Regenerated.'"
    )
    assert button.is_disabled()
    # The gate runs again on the next edit, with the regenerated text shown;
    # a subject typed and put back is still no change.
    subject = page.locator('[name="subject"]')
    original = subject.input_value()
    subject.fill("Changed")
    assert button.is_enabled()
    subject.fill(original)
    assert button.is_disabled()
    # Choosing to edit the plain text makes it count again.
    page.get_by_label("Generate plain text from HTML").uncheck()
    assert button.is_enabled()
    assert not failures


TEST = "Send a test of this text"
REQUIRED = "This field is required."
MISSING = "HTML is missing {{ family_code }}."
QUEUED_LINE = re.compile(
    r"^Test queued at \d{1,2}:\d{2}\s?[AP]M for the Testing recipient\. "
    r"Check the Testing mailbox\.$"
)


def answer_tests(page, answers):
    """Answer "Send a test of this text" with ``answers`` in turn.

    Each answer is ``(status, body)``, or None for no answer at all; the
    posted fields of each request are collected in the returned list.
    """
    sent = []
    queue = list(answers)

    def reply(route):
        sent.append(posted(route.request))
        status, body = queue.pop(0) or (None, None)
        if status is None:
            route.abort()
        else:
            route.fulfill(
                status=status, content_type="application/json", body=json.dumps(body)
            )

    page.route("**" + TEST_SEND, reply)
    return sent


def test_a_test_of_the_unsaved_text_is_sent_in_place(
    page, component_origin, axe_source
):
    """The fields are posted as they are, nothing reloads, the line says so.

    Only the email's fields and the chosen email are sent, never the
    schedule's own fields or the edit checkbox; the status line keeps its
    space, so the answer moves nothing below it either.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT)
    failures = watch(page)
    sent = answer_tests(page, [(200, {"queued": True})])
    page.get_by_label("Edit this schedule's email").check()
    subject = page.locator('[name="subject"]')
    visible(subject)
    subject.fill("Last call")
    button = page.get_by_role("button", name=TEST)
    status = page.locator("[data-schedule-email-test-status]")
    assert button.is_enabled() and status.inner_text() == ""
    assert button.get_attribute("aria-describedby") == "schedule-email-test-status"
    assert status.bounding_box()["height"] > 0
    review_at = page_top(page.get_by_role("button", name=REVIEW))
    assert axe_violations(page, axe_source) == []
    button.click()
    eventually(
        page,
        "/^Test queued/.test(document.querySelector("
        "'[data-schedule-email-test-status]').textContent)",
    )
    assert QUEUED_LINE.match(status.inner_text())
    assert "is-error" not in (status.get_attribute("class") or "")
    assert same_place(page_top(page.get_by_role("button", name=REVIEW)), review_at)
    fields = sent[0]
    chosen = page.locator('[name="schedule-template_version"]').input_value()
    assert fields["template_version"] == [chosen]
    assert fields["subject"] == ["Last call"]
    assert "A second reminder." in fields["html"][0]
    # Reminder 1's email has hand-written plain text, which is sent.
    assert fields["text"][0].startswith("Hand-written: a second reminder.")
    assert "generate_text" not in fields
    for name in ("edit_email", "acknowledge_unknown", "schedule-time", "base_digest"):
        assert name not in fields
    # The typing, the page and Review changes are as they were.
    assert subject.input_value() == "Last call"
    assert page.evaluate("window.notReloaded")
    assert page.get_by_role("button", name=REVIEW).is_enabled()
    assert not failures


def test_a_refused_test_says_why_and_an_uncertain_one_asks_first(
    page, component_origin
):
    """Field problems and refusals show in the line; nothing is resent.

    An uncertain earlier test shows the confirmation below the line, and the
    button waits for it; the next send carries it.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + EDIT)
    failures = watch(page)
    sent = answer_tests(
        page,
        [
            (
                400,
                {
                    "errors": [
                        {"field": "Email subject", "message": REQUIRED},
                        {"field": None, "message": MISSING},
                    ]
                },
            ),
            (
                409,
                {
                    "unknown": True,
                    "refusal": {
                        "message": "An earlier test may have arrived.",
                        "fix": "Tick the confirmation below.",
                    },
                },
            ),
            (200, {"queued": True}),
            None,
        ],
    )
    page.get_by_label("Edit this schedule's email").check()
    button = page.get_by_role("button", name=TEST)
    status = page.locator("[data-schedule-email-test-status]")
    box = page.get_by_label(
        "A previous test may have arrived; I want to send another test."
    )
    hidden(box)
    button.click()
    eventually(
        page,
        "/can't be sent/.test(document.querySelector("
        "'[data-schedule-email-test-status]').textContent)",
    )
    assert status.inner_text() == (
        "This text can't be sent as a test. Email subject: This field is "
        "required. HTML is missing {{ family_code }}."
    )
    assert "is-error" in status.get_attribute("class")
    assert button.is_enabled()
    button_at = page_top(button)
    button.click()
    visible(box)
    assert same_place(page_top(button), button_at)
    assert status.inner_text() == (
        "An earlier test may have arrived. Tick the confirmation below."
    )
    assert button.is_disabled() and not box.is_checked()
    eventually(page, "document.activeElement?.id === 'schedule-email-test-acknowledge'")
    box.check()
    assert button.is_enabled() and status.inner_text() == ""
    box.uncheck()
    assert button.is_disabled()
    assert status.inner_text() == "Tick the confirmation below to send another test."
    box.check()
    button.click()
    eventually(
        page,
        "/^Test queued/.test(document.querySelector("
        "'[data-schedule-email-test-status]').textContent)",
    )
    assert sent[2]["acknowledge_unknown"] == ["on"]
    assert "acknowledge_unknown" not in sent[0]
    # No answer at all: the test may be on its way, so it is not repeated.
    button.click()
    eventually(
        page,
        "/could not be reached/.test(document.querySelector("
        "'[data-schedule-email-test-status]').textContent)",
    )
    assert len(sent) == 4
    # The confirmation is never posted with a Review.
    page.locator('[name="subject"]').fill("Last call")
    with page.expect_request(lambda request: request.url.endswith("/review")) as review:
        page.get_by_role("button", name=REVIEW).click()
    assert "acknowledge_unknown" not in posted(review.value)
    assert not failures


def test_the_test_waits_for_an_email_and_for_an_open_campaign(page, component_origin):
    """Unavailable with its reason until an email is chosen, or while closed."""
    page.set_viewport_size({"width": 1280, "height": 900})
    generated_text(page)
    page.goto(component_origin + NEW)
    failures = watch(page)
    page.locator('[name="schedule-kind"]').select_option("reminder")
    page.get_by_label("Edit this schedule's email").check()
    button = page.get_by_role("button", name=TEST)
    status = page.locator("[data-schedule-email-test-status]")
    visible(button)
    assert button.is_disabled()
    assert status.inner_text() == "Choose an email to send first."
    choice = page.locator('[name="schedule-template_version"]')
    choice.select_option(
        choice.locator('option[data-kind="reminder"]').first.get_attribute("value")
    )
    assert button.is_enabled() and status.inner_text() == ""
    choice.select_option("")
    assert button.is_disabled()
    assert status.inner_text() == "Choose an email to send first."
    # Another mail type empties Email to send from script, with no change
    # event of its own: the button waits again, saying why.
    choice.select_option(
        choice.locator('option[data-kind="reminder"]').first.get_attribute("value")
    )
    assert button.is_enabled() and status.inner_text() == ""
    page.locator('[name="schedule-kind"]').select_option("daily_digest")
    assert choice.input_value() == ""
    assert button.is_disabled()
    assert status.inner_text() == "Choose an email to send first."
    page.goto(component_origin + EDIT_CLOSED)
    page.get_by_label("Edit this schedule's email").check()
    visible(button)
    assert button.is_disabled() and status.inner_text() == NOT_OPEN
    assert not failures
