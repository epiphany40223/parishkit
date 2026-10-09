"""Mail schedule rows show, clear and offer only what the chosen mail type uses."""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from .test_components import axe_violations
from .waits import has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The blank new row follows the one saved initial invitation.
NEW = "schedules-1-"
FIELDS = ("date", "time", "weekday", "template_version")
SHOWN = {
    "": set(),
    "initial": {"date", "time", "template_version"},
    "reminder": {"date", "time", "template_version"},
    "daily_digest": {"time", "template_version"},
    "weekly_digest": {"weekday", "time", "template_version"},
}


def group(page, field, prefix=NEW):
    """The wrapper of one schedule field: label, help, errors and control."""
    control = f'[name="{prefix}{field}"]'
    return page.locator(f'[data-schedule-field="{field}"]:has({control})')


def shown(page, prefix=NEW):
    """The names of the row's optional fields that are currently visible."""
    return {field for field in FIELDS if group(page, field, prefix).is_visible()}


def offered(page):
    """The mail types of the emails the new row's email list offers."""
    return page.locator(f'[name="{NEW}template_version"] option').evaluate_all(
        "options => options.map(option => option.dataset.kind || '')"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_rows_show_only_the_fields_of_their_mail_type(
    page, component_origin, axe_source, width
):
    """Before a type is chosen only the type shows; switching clears stale values."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-schedules-mail")
    # The page's explanation waits in its closed About panel (#227).
    page.locator("details[data-about-page] > summary").click()
    visible(page.get_by_role("heading", name="How mail schedules work"))
    assert "October 1, 2054 – October 31, 2054" in page.inner_text("main")
    # The saved initial invitation keeps its fixed type and its own fields.
    assert page.locator('[name="schedules-0-kind"]').is_disabled()
    assert shown(page, "schedules-0-") == SHOWN["initial"]
    assert shown(page) == set()
    kind = page.locator(f'[name="{NEW}kind"]')
    for choice in ("weekly_digest", "initial", "reminder", "daily_digest"):
        kind.select_option(choice)
        assert shown(page) == SHOWN[choice], choice
        assert offered(page) == ["", choice]
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        assert axe_violations(page, axe_source) == []
    # Values a mail type does not use are cleared when their field is hidden.
    kind.select_option("weekly_digest")
    page.locator(f'[name="{NEW}weekday"]').select_option("0")
    page.locator(f'[name="{NEW}time"]').fill("08:30:00")
    template = page.locator(f'[name="{NEW}template_version"]')
    template.select_option(index=1)
    kind.select_option("reminder")
    assert page.locator(f'[name="{NEW}weekday"]').input_value() == ""
    # The email chosen for another type is no longer offered or selected.
    assert template.input_value() == ""
    assert page.locator(f'[name="{NEW}time"]').input_value() == "08:30:00"
    page.locator(f'[name="{NEW}date"]').fill("2054-10-05")
    kind.select_option("daily_digest")
    assert page.locator(f'[name="{NEW}date"]').input_value() == ""
    kind.select_option("")
    assert shown(page) == set()
    assert page.locator(f'[name="{NEW}time"]').input_value() == ""
    assert not failures


def test_a_reported_field_stays_visible_until_the_type_changes(
    page, component_origin, axe_source
):
    """The server's weekday error is readable; a new type then hides and clears it."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/setup-schedules-error")
    prefix = "schedules-0-"
    weekday = group(page, "weekday", prefix)
    visible(weekday)
    assert "leave it at Not weekly" in weekday.inner_text()
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == "0"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.locator(f'[name="{prefix}kind"]').select_option("daily_digest")
    page.locator(f'[name="{prefix}kind"]').select_option("initial")
    hidden(weekday)
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == ""
    assert shown(page, prefix) == SHOWN["initial"]


def posted(page, component_origin, path):
    """Submit the page's form and return the posted fields without a server."""
    fields = []

    def capture(route):
        """Record the POST body, then answer with an empty page."""
        if route.request.method == "POST":
            fields.append(parse_qs(route.request.post_data, keep_blank_values=True))
            route.fulfill(status=200, content_type="text/html", body="<p>Saved</p>")
        else:
            route.continue_()

    page.route(component_origin + path, capture)
    with page.expect_response(lambda response: response.request.method == "POST"):
        page.locator("form button[type=submit]").last.click()
    return {name: values[0] for name, values in fields[0].items()}


@pytest.mark.parametrize("path", ["/setup-schedules-mail", "/schedule-settings"])
def test_add_and_remove_new_schedule_rows_before_saving(
    page, component_origin, axe_source, path
):
    """New rows clone the empty form, show only their type's fields and renumber."""
    page.set_viewport_size({"width": 320, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    total = page.locator('[name="schedules-TOTAL_FORMS"]')
    first = int(total.input_value())
    add = page.get_by_role("button", name="Add another schedule")
    add.click()
    one = f"schedules-{first}-"
    assert page.evaluate("document.activeElement.name") == one + "kind"
    assert "added" in page.locator("[data-schedule-status]").inner_text()
    add.click()
    two = f"schedules-{first + 1}-"
    assert page.evaluate("document.activeElement.name") == two + "kind"
    assert total.input_value() == str(first + 2)
    # Mail-type show/hide applies to the added rows too.
    assert shown(page, two) == set()
    page.locator(f'[name="{two}kind"]').select_option("weekly_digest")
    assert shown(page, two) == SHOWN["weekly_digest"]
    page.locator(f'[name="{two}weekday"]').select_option("0")
    page.locator(f'[name="{two}time"]').fill("08:30:00")
    page.locator(f'[name="{one}kind"]').select_option("daily_digest")
    assert shown(page, one) == SHOWN["daily_digest"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Removing the first added row renumbers the second into its place.
    page.get_by_role("button", name="Remove this new schedule").first.click()
    assert page.evaluate("document.activeElement.dataset.scheduleAdd") == ""
    assert total.input_value() == str(first + 1)
    assert page.locator(f'[name="{one}kind"]').input_value() == "weekly_digest"
    assert page.locator(f'[name="{two}kind"]').count() == 0
    assert shown(page, one) == SHOWN["weekly_digest"]
    fields = posted(page, component_origin, path)
    assert fields["schedules-TOTAL_FORMS"] == str(first + 1)
    assert fields[one + "kind"] == "weekly_digest"
    # The typed time was rewritten in its canonical form as focus left (#631).
    assert fields[one + "weekday"] == "0" and fields[one + "time"] == "08:30"
    assert not any(name.startswith(two) for name in fields)
    assert not any("__prefix__" in name for name in fields)
    assert not failures


RECURRENCE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "recurrence_cases.json").read_text()
)


def test_the_page_expander_runs_the_shared_fixture(page, component_origin):
    """Every repeat rule gives exactly the fixture's dates, gaps or refusal (#469)."""
    page.goto(component_origin + "/setup-schedules-mail")
    results = page.evaluate(
        "(cases) => cases.map((item) => window.ParishRecurrence.expand(item.rule))",
        RECURRENCE["cases"],
    )
    assert results == [case["expected"] for case in RECURRENCE["cases"]]


def page_top(locator):
    """The element's top edge from the top of the page, whatever the scroll.

    Compare two readings within a pixel: Firefox reports subpixel edges that
    differ by rounding noise (4151.8499… against 4151.8500…) with no move.
    """
    return locator.evaluate("node => node.getBoundingClientRect().top + window.scrollY")


def repeat(page, name):
    """One control of the Repeat a reminder panel."""
    return page.locator(f'[data-repeat-panel] [data-repeat="{name}"]')


@pytest.mark.parametrize("width", [320, 1280])
def test_repeat_adds_ordinary_reminder_rows_in_place(
    page, component_origin, axe_source, width
):
    """The rule lists its dates and why any is left out, then adds one row each.

    The saved initial invitation is Thursday, October 1, 2054 at 9:00 AM, in a
    campaign of October 2054, so a Monday and Thursday rule at 9am leaves out
    that first Thursday and adds the other eight. Nothing reloads; the rows
    post like hand-added ones, and the panel's own controls never post.
    """
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    path = "/setup-schedules-mail"
    page.goto(component_origin + path)
    panel = page.locator("[data-repeat-panel]")
    visible(panel)
    total = page.locator('[name="schedules-TOTAL_FORMS"]')
    first = int(total.input_value())
    # The campaign's own dates are the starting range.
    assert repeat(page, "from").input_value() == "2054-10-01"
    assert repeat(page, "until").input_value() == "2054-10-31"
    # Only the fields of the chosen repeat show.
    hidden(panel.locator('[data-repeat-when="monthly"]'))
    summary = panel.locator("[data-repeat-summary]")
    add = panel.locator("[data-repeat-add]")
    dates = panel.locator("[data-repeat-dates]")
    # Only the one-line summary is live; the long date list is not.
    assert summary.get_attribute("role") == "status"
    assert dates.get_attribute("aria-live") is None
    assert not dates.locator("[aria-live], [role=status]").count()
    has_text(summary, "Choose at least one day of the week.")
    top = page_top(add)
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').check()
    # Without a send time the timed checks cannot run: say what is missing
    # instead of offering a count that could be too high.
    has_text(summary, "Enter a send time to check these dates.")
    assert add.is_disabled()
    assert "Dates this rule gives:" in dates.inner_text()
    assert "to add" not in dates.inner_text()
    reading = panel.locator("[data-repeat-reading]")
    repeat(page, "time").fill("9xx")
    has_attribute(reading, "class", "time-reading is-error")
    repeat(page, "time").fill("9am")
    assert "Reads as 09:00 (9:00 AM)" in panel.inner_text()
    has_attribute(reading, "class", "time-reading")
    has_text(add, "Add these 8 reminders")
    has_text(summary, "8 reminders to add, 1 not added.")
    # The summary's line is reserved and the list sits below the button, so
    # neither the summary's text nor a growing list moves the button.
    assert abs(page_top(add) - top) < 1
    # No email chosen: say so rather than leave the button silently disabled.
    repeat(page, "email").evaluate(
        "select => { select.selectedIndex = -1;"
        " select.dispatchEvent(new Event('change', {bubbles: true})); }"
    )
    has_text(summary, "Choose the email to send.")
    assert add.is_disabled()
    repeat(page, "email").evaluate(
        "select => { select.selectedIndex = 0;"
        " select.dispatchEvent(new Event('change', {bubbles: true})); }"
    )
    has_text(summary, "8 reminders to add, 1 not added.")
    assert abs(page_top(add) - top) < 1
    assert (
        "Thursday, October 1, 2054: not added, not after the initial invitation"
        in dates.inner_text()
    )
    assert "8 reminders to add:" in dates.inner_text()
    assert repeat(page, "email").locator("option").evaluate_all(
        "options => options.map(option => option.dataset.kind)"
    ) == ["reminder"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    url = page.url
    add.click()
    assert page.url == url
    assert total.input_value() == str(first + 8)
    one = f"schedules-{first}-"
    # The status and focus do not depend on how a new row is labelled (a
    # row may carry no schedule number), and the summary is refreshed.
    has_text(
        page.locator("[data-schedule-status]"),
        "8 reminders added. Preview to check and save them.",
    )
    assert page.evaluate("document.activeElement.name") == one + "date"
    has_text(summary, "Nothing to add: none of these dates can be used.")
    # The added dates are now taken, so nothing is left to add.
    assert add.is_disabled()
    assert "another email is already scheduled then" in dates.inner_text()
    assert shown(page, one) == SHOWN["reminder"]
    fields = posted(page, component_origin, path)
    reminders = [
        (fields[f"schedules-{index}-date"], fields[f"schedules-{index}-time"])
        for index in range(first, first + 8)
        if fields[f"schedules-{index}-kind"] == "reminder"
    ]
    assert reminders == [
        (date, "09:00")
        for date in (
            "2054-10-05",
            "2054-10-08",
            "2054-10-12",
            "2054-10-15",
            "2054-10-19",
            "2054-10-22",
            "2054-10-26",
            "2054-10-29",
        )
    ]
    email = fields[one + "template_version"]
    assert email and all(
        fields[f"schedules-{index}-template_version"] == email
        for index in range(first, first + 8)
    )
    assert not any(name.startswith("repeat") for name in fields)
    assert not failures


def test_repeat_monthly_names_skipped_months_and_refusals(page, component_origin):
    """A month without the chosen day is listed as skipped; dates outside the
    campaign are listed as not added."""
    page.goto(component_origin + "/setup-schedules-mail")
    repeat(page, "frequency").select_option("monthly")
    panel = page.locator("[data-repeat-panel]")
    visible(panel.locator('[data-repeat-when="monthly"]'))
    hidden(panel.locator('[data-repeat-when="weekly"]'))
    repeat(page, "day").fill("31")
    repeat(page, "until").fill("2054-11-30")
    repeat(page, "time").fill("10:00")
    dates = panel.locator("[data-repeat-dates]")
    has_text(panel.locator("[data-repeat-add]"), "Add this reminder")
    text = dates.inner_text()
    assert "Saturday, October 31, 2054" in text
    assert "November 2054: not added, skipped: it has no day 31" in text
    # The nth-weekday form replaces the day of the month.
    repeat(page, "by").select_option("weekday")
    hidden(panel.locator('[data-repeat-by="day"]'))
    repeat(page, "ordinal").select_option("-1")
    repeat(page, "month-weekday").select_option("4")
    assert (
        "Friday, November 27, 2054: not added, outside the campaign dates"
        in dates.inner_text()
    )
    repeat(page, "until").fill("2054-09-01")
    has_text(
        panel.locator("[data-repeat-summary]"), "The last date is before the first."
    )
    assert panel.locator("[data-repeat-add]").is_disabled()


def test_repeat_limit_leaves_out_saved_rows_marked_delete(page, component_origin):
    """The form's schedule limit counts what the server counts (#469).

    The page is served with a limit two rows above its own, so a Monday and
    Thursday rule fits only two new reminders. Marking the saved initial
    invitation Delete frees its place (and its date and time), as the
    server's formset does not count a deleted row.
    """
    path = "/setup-schedules-mail"

    def lower_limit(route):
        """Serve the page with MAX_NUM_FORMS two above TOTAL_FORMS."""
        body = route.fetch().text()
        total = int(re.search(r'name="schedules-TOTAL_FORMS" value="(\d+)"', body)[1])
        body = re.sub(
            r'(name="schedules-MAX_NUM_FORMS" value=")\d+"',
            rf'\g<1>{total + 2}"',
            body,
        )
        route.fulfill(body=body, content_type="text/html; charset=utf-8")

    page.route(component_origin + path, lower_limit)
    page.goto(component_origin + path)
    panel = page.locator("[data-repeat-panel]")
    visible(panel)
    total = int(page.locator('[name="schedules-TOTAL_FORMS"]').input_value())
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').check()
    repeat(page, "time").fill("9:00")
    summary = panel.locator("[data-repeat-summary]")
    has_text(summary, "2 reminders to add, 7 not added.")
    assert f"over the limit of {total + 2} schedules" in panel.inner_text()
    page.locator('[name="schedules-0-DELETE"]').check()
    has_text(summary, "3 reminders to add, 6 not added.")
    has_text(panel.locator("[data-repeat-add]"), "Add these 3 reminders")
