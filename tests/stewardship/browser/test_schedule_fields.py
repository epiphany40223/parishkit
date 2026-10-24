"""Mail schedule rows show, clear and offer only what the chosen mail type uses."""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from .repeat_dates_components import assert_span_row
from .test_components import axe_violations
from .waits import (
    available,
    has_attribute,
    has_text,
    hidden,
    repeat_line,
    repeat_lines,
    visible,
)

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The blank new row follows the one saved initial invitation.
NEW = "schedules-1-"
FIELDS = ("date", "time", "weekday", "template_version")
# A new row is Family mail only: report emails replaced the digests (#1786).
SHOWN = {
    "": set(),
    "initial": {"date", "time", "template_version"},
    "reminder": {"date", "time", "template_version"},
}


def open_saved(page):
    """Open the first saved schedule's editor from the schedules table."""
    page.locator("[data-schedule-edit]").first.click()


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


def counted(pane, count):
    """The pane's dates list says how many reminders it adds, in its heading;
    the line above it does not repeat the count (R45)."""
    word = "reminder" if count == 1 else "reminders"
    has_text(pane.locator("[data-repeat-dates] > p"), f"{count} {word} to add:")
    has_text(pane.locator("[data-repeat-summary]"), "")


@pytest.mark.parametrize("width", [320, 1280])
def test_rows_show_only_the_fields_of_their_mail_type(
    page, component_origin, axe_source, width
):
    """Before a type is chosen only the type shows; switching clears stale values.

    New offers only the Family mail types (#1786).
    """
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/schedule-editor-mail")
    # The page's explanation waits in its closed About panel (#227).
    page.locator("details[data-about-page] > summary").click()
    visible(page.get_by_role("heading", name="How mail schedules work"))
    assert "October 1, 2054 – October 31, 2054" in page.inner_text("main")
    # The saved initial invitation keeps its fixed type and its own fields.
    open_saved(page)
    assert page.locator('[name="schedules-0-kind"]').is_disabled()
    assert shown(page, "schedules-0-") == SHOWN["initial"]
    assert shown(page) == set()
    kind = page.locator(f'[name="{NEW}kind"]')
    assert kind.locator("option").evaluate_all(
        "options => options.map(option => option.value)"
    ) == ["", "initial", "reminder"]
    for choice in ("initial", "reminder"):
        kind.select_option(choice)
        assert shown(page) == SHOWN[choice], choice
        assert offered(page) == ["", choice]
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        assert axe_violations(page, axe_source) == []
    # The email chosen for another type is no longer offered or selected;
    # the date and time both types use are kept.
    kind.select_option("reminder")
    page.locator(f'[name="{NEW}time"]').fill("08:30")
    page.locator(f'[name="{NEW}date"]').fill("2054-10-05")
    template = page.locator(f'[name="{NEW}template_version"]')
    template.select_option(index=1)
    kind.select_option("initial")
    assert template.input_value() == ""
    assert page.locator(f'[name="{NEW}time"]').input_value() == "08:30"
    assert page.locator(f'[name="{NEW}date"]').input_value() == "2054-10-05"
    # Values no type uses yet are cleared when every field is hidden.
    kind.select_option("")
    assert shown(page) == set()
    assert page.locator(f'[name="{NEW}time"]').input_value() == ""
    assert not failures


def test_a_reported_field_stays_visible_until_the_type_changes(
    page, component_origin, axe_source
):
    """The server's weekday error is readable; a new type then hides and clears it."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/schedule-editor-error")
    prefix = "schedules-0-"
    weekday = group(page, "weekday", prefix)
    visible(weekday)
    assert "leave it at Not weekly" in weekday.inner_text()
    assert page.locator(f'[name="{prefix}weekday"]').input_value() == "0"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    page.locator(f'[name="{prefix}kind"]').select_option("reminder")
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


def test_review_posts_the_panes_once_schedule_as_an_ordinary_row(
    page, component_origin, axe_source
):
    """Review covers the New schedule pane's one schedule (#1944).

    Only the Mail type shows until a type is chosen; a Once schedule is the
    pane's own row, which Review posts as an ordinary new row, with no row
    added and nothing of Sends.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    path = "/schedule-editor-mail"
    page.goto(component_origin + path)
    total = page.locator('[name="schedules-TOTAL_FORMS"]')
    pane = page.locator("[data-schedule-pane]")
    first = int(total.input_value())
    assert first == int(NEW.split("-")[1]) + 1
    hidden(pane.locator("[data-schedule-pane-line]"))
    assert shown(page) == set()
    page.locator(f'[name="{NEW}kind"]').select_option("reminder")
    page.locator(f'[name="{NEW}date"]').fill("2054-10-05")
    page.locator(f'[name="{NEW}time"]').fill("08:30:00")
    page.locator(f'[name="{NEW}template_version"]').select_option(index=1)
    has_text(
        pane.locator("[data-repeat-summary]"),
        "Ready to add: Reminder, Monday, October 5, 2054 at 8:30 AM: reminder mail.",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    fields = posted(page, component_origin, path)
    assert fields["schedules-TOTAL_FORMS"] == str(first)
    assert fields[NEW + "kind"] == "reminder"
    assert fields[NEW + "date"] == "2054-10-05" and fields[NEW + "time"] == "08:30"
    assert "schedule_sends" not in fields
    assert not any(name.startswith(f"schedules-{first}-") for name in fields)
    assert not any("__prefix__" in name for name in fields)
    assert not failures


RECURRENCE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "recurrence_cases.json").read_text()
)


def test_the_page_expander_runs_the_shared_fixture(page, component_origin):
    """Every repeat rule gives exactly the fixture's dates, gaps or refusal (#469)."""
    page.goto(component_origin + "/schedule-editor-mail")
    results = page.evaluate(
        "(cases) => cases.map((item) => window.ParishRecurrence.expand(item.rule))",
        RECURRENCE["cases"],
    )
    assert results == [case["expected"] for case in RECURRENCE["cases"]]


DST = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "dst_cases.json").read_text()
)["cases"]


@pytest.mark.parametrize("zone", sorted({case["zone"] for case in DST}))
def test_the_page_reads_typed_times_by_the_one_dst_rule(
    browser_engine, component_origin, zone
):
    """The page script turns typed times into the server's instants (#1129).

    In a browser in ``zone``, ``ParishLocalTime.moment`` gives exactly the
    shared fixture's instant, the one ``browser_instant`` gives: a skipped
    time is the first instant after the gap and a repeated one the first.
    """
    cases = [case for case in DST if case["zone"] == zone]
    context = browser_engine.new_context(timezone_id=zone)
    try:
        page = context.new_page()
        page.goto(component_origin + "/schedule-editor-mail")
        moments = page.evaluate(
            "(cases) => cases.map((item) =>"
            " new Date(window.ParishLocalTime.moment(item.date, item.time))"
            ".toISOString())",
            cases,
        )
    finally:
        context.close()
    assert moments == [case["utc"].replace("Z", ".000Z") for case in cases]


def page_top(locator):
    """The element's top edge from the top of the page, whatever the scroll.

    Compare two readings within a pixel: Firefox reports subpixel edges that
    differ by rounding noise (4151.8499… against 4151.8500…) with no move.
    """
    return locator.evaluate("node => node.getBoundingClientRect().top + window.scrollY")


PANE = "[data-schedule-pane]"


def repeat(page, name):
    """One control of the pane's Repeating: the rule's, or the row's own
    Send time and Email to send, which Once and Repeating share (#1944)."""
    if name == "time":
        return page.locator(f'[name="{NEW}time"]')
    if name == "email":
        return page.locator(f'[name="{NEW}template_version"]')
    return page.locator(f'{PANE} [data-repeat-panel] [data-repeat="{name}"]')


def choose_repeating(page):
    """Choose Reminder and Sends Repeating in the New schedule pane."""
    page.locator(f'[name="{NEW}kind"]').select_option("reminder")
    page.locator(PANE).get_by_role("radio", name="Repeating").check()
    visible(page.locator(f"{PANE} [data-repeat-panel]"))


@pytest.mark.parametrize("width", [320, 1280])
def test_repeating_puts_from_until_and_send_time_on_one_row(
    page, component_origin, width
):
    """The pane's Repeating draws From, Until and Send time side by side on
    desktop and stacked on a phone, without sideways scroll (pk-local R48)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/schedule-editor-mail")
    choose_repeating(page)
    assert_span_row(page, width, f"{PANE} [data-repeat-panel]", f'[name="{NEW}time"]')


@pytest.mark.parametrize("width", [320, 1280])
def test_repeating_posts_one_reminder_row_per_date(
    page, component_origin, axe_source, width
):
    """The rule lists its dates and why any is left out; Review posts one
    ordinary reminder row per date in the pane's place (#1944).

    The saved initial invitation is Thursday, October 1, 2054 at 9:00 AM in
    the campaign's New York time, in a campaign of October 2054, so a Monday
    and Thursday rule at 6am leaves out that first Thursday and posts the
    other eight. Nothing reloads; the rows post like reminders added one at
    a time, and the rule's own controls and Sends never post.
    """
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    path = "/schedule-editor-mail"
    page.goto(component_origin + path)
    pane = page.locator(PANE)
    total = page.locator('[name="schedules-TOTAL_FORMS"]')
    first = int(total.input_value())
    choose_repeating(page)
    panel = pane.locator("[data-repeat-panel]")
    # Repeating replaces the send date; the time and email follow the rule.
    hidden(group(page, "date"))
    assert page.locator(f'[name="{NEW}date"]').is_disabled()
    # The campaign's own dates are the starting range.
    assert repeat(page, "from").input_value() == "2054-10-01"
    assert repeat(page, "until").input_value() == "2054-10-31"
    # Only the fields of the chosen repeat show.
    hidden(panel.locator('[data-repeat-when="monthly"]'))
    summary = pane.locator("[data-repeat-summary]")
    review = page.get_by_role("button", name="Review changes")
    wait = pane.locator("#schedule-pane-wait")
    dates = pane.locator("[data-repeat-dates]")
    # Only the one-line summary is live; the long date list is not.
    assert summary.get_attribute("role") == "status"
    assert dates.get_attribute("aria-live") is None
    assert not dates.locator("[aria-live], [role=status]").count()
    has_text(summary, "Choose at least one day of the week.")
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').check()
    # Without a send time the timed checks cannot run: say what is missing
    # instead of offering a count that could be too high.
    has_text(summary, "Enter a send time to check these dates.")
    assert review.get_attribute("data-pane-gated") == ""
    assert "Dates this rule gives:" in dates.inner_text()
    assert "to add" not in dates.inner_text()
    # The send time is the row's shared time entry field (#1117).
    reading = page.locator(f"#id_{NEW}time_reading")
    assert repeat(page, "time").get_attribute("data-time-entry") == "time"
    reading_height = reading.bounding_box()["height"]
    gap = page_top(repeat(page, "email")) - page_top(reading)
    repeat(page, "time").fill("9xx")
    has_text(reading, "“9xx” isn't a time. Try 2:00 PM, 2pm, 14:00 or 1400.")
    has_attribute(repeat(page, "time"), "aria-invalid", "true")
    # Its two lines are reserved: the refusal moves nothing below it.
    assert reading.bounding_box()["height"] == pytest.approx(reading_height, abs=1)
    assert abs(page_top(repeat(page, "email")) - page_top(reading) - gap) < 1
    repeat(page, "time").fill("6am")
    has_text(reading, "Reads as 06:00 (6:00 AM)")
    repeat(page, "time").blur()
    assert repeat(page, "time").input_value() == "06:00"
    # No email chosen: say so, and Review waits, saying why.
    has_text(summary, "Choose the email to send.")
    assert review.get_attribute("data-pane-gated") == ""
    assert "pane-hint" in (review.get_attribute("aria-describedby") or "")
    # Only reminder emails are offered for a Reminder.
    assert repeat(page, "email").locator("option").evaluate_all(
        "options => options.map(option => option.dataset.kind || '')"
    ) == ["", "reminder"]
    repeat(page, "email").select_option(index=1)
    counted(pane, 8)
    # The summary's line is reserved and the list sits last, so neither the
    # summary's text nor a shorter or longer list moves the help line below
    # it (the chosen email's description above it is drawn by now).
    top = page_top(wait)
    panel.locator('[data-repeat="weekday"][value="3"]').uncheck()
    counted(pane, 4)
    assert abs(page_top(wait) - top) < 1
    panel.locator('[data-repeat="weekday"][value="3"]').check()
    counted(pane, 8)
    assert abs(page_top(wait) - top) < 1
    # The lines are the shared schedule preview (#2092): a date not added is
    # struck, with its reason.
    line = repeat_line(
        dates,
        "Thursday, October 1, 2054",
        "not added: not after the initial invitation",
    )
    assert dates.locator("li s").filter(has_text="not after").count() == 1, line
    assert "8 reminders to add:" in dates.inner_text()
    # A whole schedule: Review covers it.
    available(review)
    assert review.get_attribute("data-pane-gated") is None
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    url = page.url
    fields = posted(page, component_origin, path)
    assert page.url == url
    pane_index = first - 1
    assert fields["schedules-TOTAL_FORMS"] == str(pane_index + 8)
    one = NEW
    reminders = [
        (fields[f"schedules-{index}-date"], fields[f"schedules-{index}-time"])
        for index in range(pane_index, pane_index + 8)
        if fields[f"schedules-{index}-kind"] == "reminder"
    ]
    assert reminders == [
        (date, "06:00")
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
        for index in range(pane_index, pane_index + 8)
    )
    assert not any(name.startswith("repeat") for name in fields)
    assert "schedule_sends" not in fields
    assert not failures


@pytest.mark.parametrize(
    ("zone", "weekday", "until", "clock", "kept", "left_out"),
    [
        # Saturdays at 10:00 PM in Los Angeles: October 31's is 1:00 AM on
        # November 1 in New York, after the campaign ends, so it is left out.
        (
            "America/Los_Angeles",
            "5",
            "2054-10-31",
            "10pm",
            ["2054-10-03", "2054-10-10", "2054-10-17", "2054-10-24"],
            "Saturday, October 31, 2054",
        ),
        # Sundays at 2:00 AM in London: November 1's is 9:00 PM on October
        # 31 in New York, the campaign's last day, so it is kept.
        (
            "Europe/London",
            "6",
            "2054-11-01",
            "2am",
            ["2054-10-04", "2054-10-11", "2054-10-18", "2054-10-25", "2054-11-01"],
            None,
        ),
    ],
)
def test_repeating_judges_each_date_in_the_campaigns_zone(
    browser_engine, component_origin, zone, weekday, until, clock, kept, left_out
):
    """Repeating's dates are judged at the send time in the campaign's zone
    (New York), as a Once send date and the server judge them, whatever the
    browser's zone (#1944): a date the browser puts on the campaign's last
    day but New York puts after it is listed as not added, and one the
    browser puts after the last day but New York puts on it is posted."""
    context = browser_engine.new_context(timezone_id=zone, reduced_motion="reduce")
    page = context.new_page()
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    path = "/schedule-editor-mail"
    page.goto(component_origin + path)
    pane = page.locator(PANE)
    choose_repeating(page)
    panel = pane.locator("[data-repeat-panel]")
    panel.locator(f'[data-repeat="weekday"][value="{weekday}"]').check()
    repeat(page, "until").fill(until)
    repeat(page, "time").fill(clock)
    repeat(page, "time").blur()
    repeat(page, "email").select_option(index=1)
    dates = pane.locator("[data-repeat-dates]")
    counted(pane, len(kept))
    if left_out:
        repeat_line(dates, left_out, "not added: outside the campaign dates")
    else:
        lines = repeat_lines(dates, len(kept))
        assert not [line for line in lines if "not added" in line]
    first = int(page.locator('[name="schedules-TOTAL_FORMS"]').input_value()) - 1
    fields = posted(page, component_origin, path)
    assert [
        fields[f"schedules-{first + offset}-date"] for offset in range(len(kept))
    ] == kept
    assert fields["schedules-TOTAL_FORMS"] == str(first + len(kept))
    assert not failures
    context.close()


def test_repeat_monthly_names_skipped_months_and_refusals(page, component_origin):
    """A month without the chosen day is listed as skipped; dates outside the
    campaign are listed as not added."""
    page.goto(component_origin + "/schedule-editor-mail")
    choose_repeating(page)
    repeat(page, "frequency").select_option("monthly")
    panel = page.locator(f"{PANE} [data-repeat-panel]")
    visible(panel.locator('[data-repeat-when="monthly"]'))
    hidden(panel.locator('[data-repeat-when="weekly"]'))
    repeat(page, "day").fill("31")
    repeat(page, "until").fill("2054-11-30")
    repeat(page, "time").fill("10:00")
    repeat(page, "email").select_option(index=1)
    pane = page.locator(PANE)
    dates = pane.locator("[data-repeat-dates]")
    counted(pane, 1)
    from playwright.sync_api import expect

    repeat_line(dates, "Saturday, October 31, 2054", "Reminder")
    # A skipped month is no send: a line after the shared preview's.
    expect(dates.locator("[data-repeat-preview] > p.help")).to_have_text(
        "November 2054 is skipped: it has no day 31."
    )
    # The nth-weekday form replaces the day of the month.
    repeat(page, "by").select_option("weekday")
    hidden(panel.locator('[data-repeat-by="day"]'))
    repeat(page, "ordinal").select_option("-1")
    repeat(page, "month-weekday").select_option("4")
    repeat_line(
        dates, "Friday, November 27, 2054", "not added: outside the campaign dates"
    )
    # A rule error is said at its fields (#1915), not in the summary.
    repeat(page, "until").fill("2054-09-01")
    has_text(panel.locator("#repeat-until_error"), "The last date is before the first.")
    has_text(pane.locator("[data-repeat-summary]"), "")
    review = page.get_by_role("button", name="Review changes")
    assert review.get_attribute("data-pane-gated") == ""


def test_repeat_rule_errors_show_at_their_fields(page, component_origin):
    """Each rule error marks its fields, with its words in their reserved
    line (#1915): Until before From marks both and is said once, after
    Until; a missing date marks the empty one; a day of the month out of
    range marks the day; no weekday marks the days. Review changes waits
    meanwhile for the pane (it holds part of a schedule), never as a rule
    gate of its own, whose controls are not posted, and nothing below
    moves."""
    page.set_viewport_size({"width": 1280, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/schedule-editor-mail")
    choose_repeating(page)
    pane = page.locator(PANE)
    panel = pane.locator("[data-repeat-panel]")
    summary = pane.locator("[data-repeat-summary]")
    start, until = repeat(page, "from"), repeat(page, "until")
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').check()
    repeat(page, "time").fill("6am")
    repeat(page, "email").select_option(index=1)
    counted(pane, 8)
    places = [pane.locator("#schedule-pane-wait"), summary, repeat(page, "time")]
    before = [page_top(item) for item in places]
    preview = page.locator("form:has([data-repeat-panel]) [type=submit]").first

    def described(node):
        """The ids that describe ``node``."""
        return (node.get_attribute("aria-describedby") or "").split()

    until.fill("2054-09-30")
    line = panel.locator("#repeat-until_error")
    has_text(line, "The last date is before the first.")
    for field in (start, until):
        has_attribute(field, "aria-invalid", "true")
        assert "repeat-until_error" in described(field)
    hidden(panel.locator("#repeat-from_error"))
    has_text(summary, "")
    assert [page_top(item) for item in places] == pytest.approx(before, abs=1)
    # Review waits for the pane, not for the rule.
    assert not preview.evaluate("node => node.hasAttribute('data-repeat-gated')")
    assert preview.evaluate("node => node.hasAttribute('data-pane-gated')")
    # Emptied, only From is marked, with its own line.
    until.fill("2054-10-31")
    start.fill("")
    has_text(
        panel.locator("#repeat-from_error"),
        "Choose the first and last dates to repeat on.",
    )
    has_attribute(start, "aria-invalid", "true")
    has_attribute(until, "aria-invalid", "false")
    hidden(line)
    assert preview.evaluate("node => node.hasAttribute('data-pane-gated')")
    assert [page_top(item) for item in places] == pytest.approx(before, abs=1)
    start.fill("2054-10-01")
    has_attribute(start, "aria-invalid", "false")
    hidden(panel.locator("#repeat-from_error"))
    counted(pane, 8)
    available(preview)
    assert not preview.evaluate("node => node.hasAttribute('data-pane-gated')")
    # A day of the month out of range.
    repeat(page, "frequency").select_option("monthly")
    day = repeat(page, "day")
    day.fill("40")
    has_text(
        panel.locator("#repeat-day_error"),
        "Choose a day of the month from 1 to 31, or a weekday.",
    )
    has_attribute(day, "aria-invalid", "true")
    assert described(day) == ["repeat-day-help", "repeat-day_error"]
    day.fill("15")
    has_attribute(day, "aria-invalid", "false")
    hidden(panel.locator("#repeat-day_error"))
    assert described(day) == ["repeat-day-help"]
    # No weekday, once the days are reached.
    repeat(page, "frequency").select_option("weekly")
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').uncheck()
    days = panel.locator('[data-repeat="weekday"]')
    has_attribute(days.first, "aria-invalid", "true")
    has_text(
        panel.locator("#repeat-weekdays_error"), "Choose at least one day of the week."
    )
    assert preview.evaluate("node => node.hasAttribute('data-pane-gated')")
    panel.locator('[data-repeat="weekday"][value="1"]').check()
    has_attribute(days.first, "aria-invalid", "false")
    hidden(panel.locator("#repeat-weekdays_error"))
    assert not failures


def test_repeat_limit_leaves_out_saved_rows_marked_delete(page, component_origin):
    """The form's schedule limit counts what the server counts (#469).

    The page is served with a limit two rows above its own (the saved
    invitation and the blank pane, which Django's formset counts too). A
    Repeating pane's reminders take the pane's place (#1944), so a Monday
    and Thursday rule fits three. Marking the saved initial invitation
    Delete frees its place (and its date and time), as the server's formset
    does not count a deleted row.
    """
    path = "/schedule-editor-mail"

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
    choose_repeating(page)
    pane = page.locator(PANE)
    panel = pane.locator("[data-repeat-panel]")
    total = int(page.locator('[name="schedules-TOTAL_FORMS"]').input_value())
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').check()
    repeat(page, "time").fill("9:00")
    repeat(page, "email").select_option(index=1)
    counted(pane, 3)
    over = f"not added: over the limit of {total + 2} schedules"
    repeat_line(pane.locator("[data-repeat-dates]"), over, over)
    open_saved(page)
    page.locator('[name="schedules-0-DELETE"]').check()
    counted(pane, 4)


def test_repeat_leaves_out_dates_whose_time_has_passed(page, component_origin):
    """#1948: a date whose send time is already past is listed as not added.

    With the browser's clock at noon on Thursday, October 15, 2054 (UTC, so
    6:00 AM on the 15th has passed in any American zone), a Monday and
    Thursday rule at 6:00 AM counts only the four later dates; the five
    earlier ones, the 15th among them, say their time has passed.
    """
    from datetime import UTC, datetime

    page.clock.set_fixed_time(datetime(2054, 10, 15, 16, tzinfo=UTC))
    page.goto(component_origin + "/schedule-editor-mail")
    choose_repeating(page)
    pane = page.locator(PANE)
    panel = pane.locator("[data-repeat-panel]")
    for day in ("0", "3"):
        panel.locator(f'[data-repeat="weekday"][value="{day}"]').check()
    repeat(page, "time").fill("6am")
    repeat(page, "time").blur()
    repeat(page, "email").select_option(index=1)
    counted(pane, 4)
    lines = repeat_lines(pane.locator("[data-repeat-dates]"), 9)
    for passed in ("Thursday, October 1", "Monday, October 5", "Thursday, October 15"):
        (line,) = [line for line in lines if line.startswith(f"{passed}, 2054,")]
        assert "not added: its time has passed" in line
    (line,) = [line for line in lines if line.startswith("Monday, October 19, 2054,")]
    assert "not added" not in line


def test_a_failed_date_list_read_says_so_in_place(page, component_origin):
    """#2092: while a newer list is awaited the lines are marked busy, and a
    refused read replaces them with the list's failure sentence, under the
    heading, rather than leave the lines of another rule."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/schedule-editor-mail")
    choose_repeating(page)
    pane = page.locator(PANE)
    panel = pane.locator("[data-repeat-panel]")
    panel.locator('[data-repeat="weekday"][value="0"]').check()
    repeat(page, "time").fill("9:00")
    repeat(page, "email").select_option(index=1)
    dates = pane.locator("[data-repeat-dates]")
    repeat_line(dates, "Monday, October 19, 2054", "Reminder")
    held = []
    page.route("**/repeat-dates/", lambda route: held.append(route))
    panel.locator('[data-repeat="weekday"][value="3"]').check()
    preview = dates.locator("[data-repeat-preview]")
    has_attribute(preview, "aria-busy", "true")
    # The earlier lines stay in place while the answer is awaited.
    assert dates.locator("li").count() > 0
    # The held read arrives a moment after the change; refuse it.
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    held[0].fulfill(status=500, body="")
    expect(preview).to_have_text(
        "These dates couldn't be listed right now. Review changes still checks "
        "every date."
    )
    expect(preview).not_to_have_attribute("aria-busy", "true")
    assert dates.locator("[data-repeat-heading]").inner_text().endswith("to add:")
