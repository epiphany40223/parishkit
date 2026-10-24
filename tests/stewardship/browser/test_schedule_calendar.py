"""The Campaign calendar (#469), its Add on a day and Move… (#1521) and its month view.

The page script draws the table of sends as one month at a time, with
Previous month and Next month limited to the campaign's months (#1656),
each send on its day in the browser's time zone. The campaign (October
1–31, 2054) is in New York's zone; the default page is Los Angeles's,
three hours behind, and Auckland is a day ahead. The schedules are
schedule_list_components': the Initial invitation (sent, October 1),
Reminder 1 (October 10), Reminder 2 (October 20) and the Weekly report
email on Mondays, each at 9:00 AM New York time, plus the Daily report
email at 7:00 AM from October 2 through November 1, the day after the
campaign closes; "now" is October 5, so it has made its first four sends.

Each campaign day's (+) Add opens New scheduled email with that send date
(``ADD_ON``, October 12), whose Cancel and Apply return to the calendar's
October (``CALENDAR_OCTOBER``). Without a clock the browser's today (2026) is
outside the campaign, so the calendar opens on its first month, October.

Each reminder has a Move… button (and drags, on a fine pointer) that opens
the Move dialog; its Review posts to the reminder's Edit page, which answers
with Edit's review for Reminder 1 and a refused send date for Reminder 2
(schedule_list_components).
"""

import re

import pytest

from .keyboard import tab_to_next
from .schedule_list_components import (
    ADD_DAY,
    ADD_ON,
    APPLY,
    CALENDAR,
    CALENDAR_OCTOBER,
    DATE_REFUSAL,
    EDIT_SERIES,
    LIST,
    MONTHS,
    NEW,
    NOW,
)
from .test_components import axe_violations
from .test_schedule_list import (
    ADD,
    APPLIED_LINE,
    FOCUSED,
    REVIEW_DIALOG,
    choose,
    email_for,
    near,
    posted,
    review,
    watch,
)
from .waits import eventually, focused, has_class, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
MONDAYS = [5, 12, 19, 26]
# Every email by date's rows (#1656): every send (three one-time schedules,
# the weekly report's Mondays, its first Monday after the close (November 2:
# the converted Weekly sends until then, #1786) and the daily one's 31
# days), and the upcoming ones, without the sent invitation and the daily
# report's four sends already made.
ALL_ROWS = 3 + len(MONDAYS) + 1 + 31
UPCOMING_ROWS = ALL_ROWS - 5


def page_place(locator):
    """``locator``'s top-left corner on the page, not in the window, so a
    scroll (Playwright scrolls a control into view before using it) is not
    read as the control moving."""
    return locator.evaluate(
        "node => { const box = node.getBoundingClientRect();"
        " return [box.left + window.scrollX, box.top + window.scrollY]; }"
    )


def cell(page, number):
    """Day ``number``'s cell in the month shown."""
    grid = page.locator("table.calendar-month")
    cells = grid.locator("td:has(.calendar-day)")
    texts = cells.locator(".calendar-day").all_inner_texts()
    return cells.nth(texts.index(str(number)))


@pytest.mark.parametrize("width", [320, 1280])
def test_calendar_draws_each_send_on_its_local_day(
    page, component_origin, axe_source, width
):
    """October, then November, Sunday first, sends at Los Angeles times."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    grids = page.locator("table.calendar-month")
    assert grids.locator("caption").all_inner_texts() == ["October 2054"]
    headings = grids.locator("thead th .visually-hidden").all_inner_texts()
    assert headings[0] == "Sunday" and headings[-1] == "Saturday"
    # October 1, 2054 is a Thursday: four blank days before it.
    first_week = grids.first.locator("tbody tr").first.locator("td")
    assert [first_week.nth(n).inner_text().strip() for n in range(4)] == [""] * 4
    opening = cell(page, 1)
    assert "Campaign opens" in opening.inner_text()
    # The sent invitation is listed but not linked; its status is read out.
    invitation = opening.locator("li").first
    assert invitation.inner_text().startswith("6:00 AM Initial invitation")
    assert invitation.get_by_role("link").count() == 0
    assert invitation.locator(".visually-hidden").inner_text() == ", Sent"
    reminder = cell(page, 10).get_by_role("link", name="6:00 AM Reminder 1")
    assert reminder.get_attribute("href").startswith("/admin/campaign/schedules/")
    for monday in MONDAYS:
        assert "Weekly report" in cell(page, monday).inner_text()
    assert "Campaign closes" in cell(page, 31).inner_text()
    # A daily report send already made is muted, unlinked and read as Sent;
    # a later one links to the report's Edit report email page.
    past = cell(page, 3).locator("li")
    assert past.inner_text().startswith("4:00 AM Daily report")
    assert past.get_attribute("class") == "schedule-past"
    assert past.get_by_role("link").count() == 0
    assert past.locator(".visually-hidden").inner_text() == ", Sent"
    visible(cell(page, 6).get_by_role("link", name="4:00 AM Daily report"))
    # A phone scrolls the grid inside its region; the page never scrolls sideways.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Its last send, the day after the campaign closes, is in November's grid,
    # which starts on a Sunday.
    page.get_by_role("button", name="Next month").click()
    assert grids.locator("caption").all_inner_texts() == ["November 2054"]
    november = cell(page, 1)
    assert grids.locator("tbody td").first.inner_text().startswith("1")
    visible(november.get_by_role("link", name="4:00 AM Daily report"))
    assert "Campaign" not in november.inner_text()
    # The table lists the same sends as text, with compact times: by default
    # the upcoming ones (not the sent invitation or the four reports sent).
    rows = page.locator("table.calendar-sends tbody tr")
    assert rows.count() == UPCOMING_ROWS
    assert rows.first.locator("time").inner_text() == "Oct 5, 2054 6:00 AM"
    assert rows.first.locator("th").inner_text() == "Weekly report"
    visible(page.get_by_role("link", name="Edit Reminder 1"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_a_zone_a_day_ahead_moves_each_send_to_its_own_day(
    browser_engine, component_origin
):
    """In Auckland, 9:00 AM in New York is 2:00 AM the next day.

    The campaign's dates are calendar dates and stay where they are.
    """
    context = browser_engine.new_context(timezone_id="Pacific/Auckland")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(component_origin + CALENDAR)
        failures = watch(page)
        opening = cell(page, 1)
        assert "Campaign opens" in opening.inner_text()
        assert opening.locator("li").count() == 0
        assert (
            cell(page, 2)
            .locator("li")
            .inner_text()
            .startswith("2:00 AM Initial invitation")
        )
        for monday in MONDAYS:
            assert "Weekly report" in cell(page, monday + 1).inner_text()
            assert "Weekly report" not in cell(page, monday).inner_text()
        assert not failures
    finally:
        context.close()


def test_the_list_links_the_calendar(page, component_origin):
    """Dates and mail schedules opens the calendar beside New scheduled email."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + LIST)
    link = page.get_by_role("link", name="Campaign calendar")
    assert link.get_attribute("href") == "/admin/campaign/schedules/calendar/"


def at_now(page):
    """Give the browser the server's "now" (October 5), as the page's own
    clock decides which days are already past."""
    page.clock.install(time=NOW)


def add_link(page, day):
    """October ``day``'s Add, by the name a screen reader hears."""
    return page.get_by_role(
        "link", name=f"Add a scheduled email on October {day}, 2054"
    )


def test_each_campaign_day_has_an_add_named_with_its_date(
    page, component_origin, axe_source
):
    """With "now" October 5, today and every later day through October 31
    has one (+) Add; October 1–4 (passed) and November 1 (after the
    campaign) have none. Each opens New with its own day, is an icon whose
    tooltip is its name, and the grid is the same before and after an Add
    takes focus (nothing moves)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    adds = page.locator("table.calendar-month a.calendar-add")
    assert adds.count() == 27
    # An icon only: no visible word, a decorative (+) and a matching tooltip.
    assert set(adds.all_inner_texts()) == {""}
    assert adds.first.locator("svg[aria-hidden=true]").count() == 1
    for day in (1, 2, 3, 4):
        assert cell(page, day).locator("a.calendar-add").count() == 0
    first = add_link(page, 5)
    assert first.get_attribute("href") == f"{NEW}?date=2054-10-05"
    assert first.get_attribute("title") == "Add a scheduled email on October 5, 2054"
    assert add_link(page, 31).get_attribute("href") == f"{NEW}?date=2054-10-31"
    # The Add sits on its day's number line, inside the cell.
    twelve = cell(page, 12)
    link = add_link(page, 12)
    number = twelve.locator(".calendar-day").bounding_box()
    box = link.bounding_box()
    holder = twelve.bounding_box()
    assert (
        box["x"] >= holder["x"]
        and box["x"] + box["width"] <= holder["x"] + holder["width"] + 1
    )
    assert box["y"] < number["y"] + number["height"]
    # A target at least 24px square (WCAG 2.2 SC 2.5.8).
    assert box["width"] >= 24 and box["height"] >= 24
    reminder = cell(page, 10).get_by_role("link", name="6:00 AM Reminder 1")
    before = page_place(reminder)
    link.hover()
    link.focus()
    assert same_place(page_place(reminder), before)
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("window.notReloaded === true")
    page.get_by_role("button", name="Next month").click()
    assert cell(page, 1).locator("a.calendar-add").count() == 0
    assert not failures


def test_a_zone_a_day_ahead_keeps_add_on_the_campaign_dates(
    browser_engine, component_origin
):
    """The campaign's dates are calendar dates: in Auckland the Adds are
    still the campaign's dates, though the sends move a day on and the
    browser's "today" is October 6, so the Adds start there."""
    context = browser_engine.new_context(timezone_id="Pacific/Auckland")
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        at_now(page)
        page.goto(component_origin + CALENDAR)
        adds = page.locator("table.calendar-month a.calendar-add")
        assert adds.count() == 26
        assert adds.first.get_attribute("href") == f"{NEW}?date=2054-10-06"
    finally:
        context.close()


def test_a_keyboard_reaches_a_day_add_and_opens_new_with_that_date(
    page, component_origin
):
    """Tab reaches each day's Add in reading order; Enter opens New with
    the day filled in, and its Cancel returns to the calendar."""
    page.set_viewport_size({"width": 1280, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    # From October 11's Add, Tab reaches that day's send (the daily report),
    # then October 12's Add: each day's Add comes before its sends.
    add_link(page, 11).focus()
    tab_to_next(page)
    assert page.evaluate("document.activeElement.textContent") == (
        "4:00 AM Daily report"
    )
    tab_to_next(page)
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
        "Add a scheduled email on October 12, 2054"
    )
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert page.url.endswith(ADD_ON)
    failures = watch(page)
    # Drawn as a reminder on that day; the Initial invitation keeps the day.
    assert page.locator('[name="schedule-kind"]').input_value() == "reminder"
    date = page.locator('[name="schedule-date"]')
    visible(date)
    assert date.input_value() == ADD_DAY
    choose(page, "kind", "initial")
    assert date.input_value() == ADD_DAY
    cancel = page.get_by_role("link", name="Cancel")
    assert cancel.get_attribute("href") == CALENDAR_OCTOBER
    assert not failures


def test_add_on_a_day_applies_and_returns_to_the_calendar(page, component_origin):
    """A pointer click on October 12's Add, then New's Review and Apply: the
    page returns to the calendar, whose reserved status line says what was
    applied and takes focus."""
    page.set_viewport_size({"width": 1280, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    line = page.locator("#schedules-outcome")
    assert line.inner_text() == ""
    heading = page.locator("#calendar-months-title").bounding_box()["y"]
    with page.expect_navigation():
        add_link(page, 12).click()
    assert page.url.endswith(ADD_ON)
    failures = watch(page)
    # The day (and its type) is the form as drawn: nothing to add yet.
    button = page.get_by_role("button", name=ADD)
    assert button.is_disabled()
    visible(page.get_by_text("Fill in the scheduled email to add it."))
    assert page.locator('[name="schedule-date"]').input_value() == ADD_DAY
    page.locator('[name="schedule-time"]').fill("9am")
    # A whole schedule needs its email too (#2025).
    assert button.is_disabled()
    choose(page, "template_version", email_for(page, "reminder"))
    assert not button.is_disabled()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown = review(page)
    assert "schedule-date=2054-10-12" in (sent.value.post_data or "")
    assert page.evaluate("window.notReloaded === true")
    assert not failures
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(APPLY)
    page.wait_for_url(re.compile(re.escape(CALENDAR_OCTOBER) + "$"))
    eventually(page, "document.getElementById('schedules-outcome').textContent !== ''")
    line = page.locator("#schedules-outcome")
    assert line.inner_text().startswith("Scheduled email added. ")
    assert APPLIED_LINE.search(line.inner_text())
    assert page.evaluate(FOCUSED) == "schedules-outcome"
    # The reserved line kept its space: the grids did not move down.
    assert near(page.locator("#calendar-months-title").bounding_box()["y"], heading)


def month_shown(page):
    """The caption of the month grid shown."""
    return page.locator("table.calendar-month caption").inner_text()


@pytest.mark.parametrize("width", [320, 1280])
def test_months_change_in_place_within_the_campaign(
    page, component_origin, axe_source, width
):
    """Previous and Next step through October and November 2054 only.

    At an end the button stays where it is, focusable but unavailable, with
    its reason in the reserved line; a change makes no request, keeps focus
    on the button, announces the month, replaces the address and never moves
    the buttons. Every month has six week rows.
    """
    page.set_viewport_size({"width": width, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    previous = page.get_by_role("button", name="Previous month")
    following = page.get_by_role("button", name="Next month")
    reason = page.locator("[data-calendar-reason]")
    assert month_shown(page) == "October 2054"
    assert page.locator("[data-calendar-name]").inner_text() == "October 2054"
    assert previous.get_attribute("aria-disabled") == "true"
    assert previous.get_attribute("aria-describedby") == "calendar-nav-reason"
    assert following.get_attribute("aria-disabled") is None
    assert reason.inner_text() == "This is the campaign's first month."
    assert page.locator("table.calendar-month tbody tr").count() == 6
    places = [page_place(previous), page_place(following), page_place(reason)]
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    # The unavailable button does nothing (Playwright waits for an
    # aria-disabled button to be enabled, so the click is forced).
    previous.click(force=True)
    assert month_shown(page) == "October 2054"
    # By pointer, then (since macOS WebKit does not focus a clicked button)
    # by keyboard from the focused button, which keeps focus.
    following.click()
    assert month_shown(page) == "November 2054"
    assert page.url.endswith(f"{CALENDAR}?month=2054-11")
    previous.click()
    assert month_shown(page) == "October 2054"
    following.focus()
    page.keyboard.press("Enter")
    assert month_shown(page) == "November 2054"
    assert page.url.endswith(f"{CALENDAR}?month=2054-11")
    assert page.evaluate("document.activeElement.textContent.trim()") == "Next month"
    assert following.get_attribute("aria-disabled") == "true"
    assert previous.get_attribute("aria-disabled") is None
    assert reason.inner_text() == "This is the campaign's last month."
    assert page.locator("[data-calendar-said]").inner_text() == "November 2054"
    assert page.locator("table.calendar-month tbody tr").count() == 6
    assert same_place(
        [page_place(previous), page_place(following), page_place(reason)], places
    )
    # Focused and unavailable, Enter does nothing either.
    page.keyboard.press("Enter")
    assert month_shown(page) == "November 2054"
    previous.focus()
    page.keyboard.press("Enter")
    assert month_shown(page) == "October 2054"
    assert page.url.endswith(CALENDAR_OCTOBER)
    assert requests == []
    assert page.evaluate("window.notReloaded === true")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_the_address_keeps_the_month_through_refresh_and_back(page, component_origin):
    """Refresh and Back show the month the address names; a month outside the
    campaign opens the nearer end, which the address then names."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + CALENDAR)
    page.get_by_role("button", name="Next month").click()
    assert page.url.endswith(MONTHS[1])
    page.reload()
    assert month_shown(page) == "November 2054"
    page.goto(component_origin + LIST)
    page.go_back()
    assert page.url.endswith(MONTHS[1])
    assert month_shown(page) == "November 2054"
    page.goto(component_origin + MONTHS[2])
    assert month_shown(page) == "October 2054"
    assert page.url.endswith(CALENDAR_OCTOBER)
    page.goto(component_origin + MONTHS[3])
    assert month_shown(page) == "November 2054"
    assert page.url.endswith(MONTHS[1])
    # With "now" inside the campaign and no month named, today's month.
    at_now(page)
    page.goto(component_origin + CALENDAR)
    assert month_shown(page) == "October 2054"


def test_edit_links_carry_the_month_shown(page, component_origin):
    """Each Edit link, in the grid and the table, returns to the month shown."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + CALENDAR)
    reminder = cell(page, 10).get_by_role("link", name="6:00 AM Reminder 1")
    assert reminder.get_attribute("href").endswith("/?calendar=2054-10")
    table = page.get_by_role("link", name="Edit Reminder 1")
    assert table.get_attribute("href").endswith("/?calendar=2054-10")
    page.get_by_role("button", name="Next month").click()
    assert table.get_attribute("href").endswith("/?calendar=2054-11")
    # A report email's send also names its own slot (#1786): the daily report
    # sent on November 1 reports on October 31.
    report = cell(page, 1).get_by_role("link", name="4:00 AM Daily report")
    href = report.get_attribute("href")
    assert href.startswith("/admin/mail/report-emails/")
    assert href.endswith("/?send=2054-10-31&calendar=2054-11")


def unavailable_look(button):
    """``button``'s pointer, opacity and background, as drawn now."""
    return button.evaluate(
        "node => { const style = getComputedStyle(node);"
        " return [style.cursor, style.opacity, style.backgroundColor]; }"
    )


@pytest.mark.parametrize(
    ("scheme", "width"), [("light", 1280), ("dark", 1280), ("light", 390)]
)
def test_an_end_button_looks_unavailable_not_busy(
    page, component_origin, scheme, width
):
    """At the first and last months the button there is greyed with the
    not-allowed pointer, never the busy (progress) one, and hovering it
    changes nothing; the other button keeps the ordinary pointer (#1656)."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CALENDAR)
    previous = page.get_by_role("button", name="Previous month")
    following = page.get_by_role("button", name="Next month")
    # October (the first month), then November (the last).
    for end, other in ((previous, following), (following, previous)):
        if end is following:
            following.click()
            assert month_shown(page) == "November 2054"
        before = unavailable_look(end)
        assert before[:2] == ["not-allowed", "0.55"]
        end.hover(force=True)
        # Past the buttons' colour transition: hovering changed nothing.
        page.wait_for_timeout(300)
        assert unavailable_look(end) == before
        assert unavailable_look(other)[:2] == ["pointer", "1"]
        # Still focusable, and its reason is still named.
        end.focus()
        assert end.evaluate("node => node === document.activeElement")
        assert end.get_attribute("aria-describedby") == "calendar-nav-reason"


def table_rows(page):
    """Every email by date's rows."""
    return page.locator("table.calendar-sends tbody tr")


def first_row(page):
    """The first row's time and schedule, as shown."""
    row = table_rows(page).first
    return row.locator("time").inner_text(), row.locator("th").inner_text()


@pytest.mark.parametrize(
    ("scheme", "width"), [("light", 1280), ("dark", 1280), ("light", 390)]
)
def test_show_and_sort_refresh_the_table_in_place(
    page, component_origin, axe_source, scheme, width
):
    """Show and the When and Schedule headings refresh only the table, with
    no page load; each keeps the other's choice and the month shown, focus
    stays on the chosen control, and the grid never changes (#1656)."""
    from playwright.sync_api import expect

    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    upcoming = page.get_by_role("link", name="Upcoming emails")
    past = page.get_by_role("link", name="Include past emails")
    assert upcoming.get_attribute("aria-current") == "page"
    assert table_rows(page).count() == UPCOMING_ROWS
    page.get_by_role("button", name="Next month").click()
    grid = page.locator("[data-calendar]").inner_html()
    choice = page_place(past)
    past.click()
    expect(table_rows(page)).to_have_count(ALL_ROWS)
    assert first_row(page) == ("Oct 1, 2054 6:00 AM", "Initial invitation")
    expect(page.get_by_role("link", name="Include past emails")).to_have_attribute(
        "aria-current", "page"
    )
    assert page.evaluate("document.activeElement.textContent") == "Include past emails"
    assert same_place([page_place(past)], [choice])
    query = page.url.partition("?")[2]
    assert sorted(query.partition("#")[0].split("&")) == [
        "month=2054-11",
        "show=all",
        "sort=when",
    ]
    # When, newest first on its first choice; Show is kept.
    when = page.locator("th[data-sort-column=when] a.sort-link")
    when.click()
    expect(page.locator("th[data-sort-column=when]")).to_have_attribute(
        "aria-sort", "descending"
    )
    # The newest is the converted Weekly's send after the close (#1786).
    assert first_row(page) == ("Nov 2, 2054 6:00 AM", "Weekly report")
    assert table_rows(page).count() == ALL_ROWS
    # Schedule: the schedules in sending order, each one's sends by time.
    page.locator("th[data-sort-column=schedule] a.sort-link").click()
    expect(page.locator("th[data-sort-column=schedule]")).to_have_attribute(
        "aria-sort", "ascending"
    )
    assert first_row(page) == ("Oct 1, 2054 6:00 AM", "Initial invitation")
    # Back to the upcoming emails, keeping the Schedule sort.
    page.get_by_role("link", name="Upcoming emails").click()
    expect(table_rows(page)).to_have_count(UPCOMING_ROWS)
    assert (
        page.locator("th[data-sort-column=schedule]").get_attribute("aria-sort")
        == "ascending"
    )
    assert "sort=schedule" in page.url and "month=2054-11" in page.url
    # The swapped-in Edit links carry the month shown, the grid is as it was.
    edit = page.get_by_role("link", name="Edit Reminder 1")
    assert edit.get_attribute("href").endswith("/?calendar=2054-11")
    assert page.locator("[data-calendar]").inner_html() == grid
    assert month_shown(page) == "November 2054"
    # A month change after the swap is carried by the new links too.
    page.get_by_role("button", name="Previous month").click()
    assert "month=2054-10" in when.get_attribute("href")
    assert edit.get_attribute("href").endswith("/?calendar=2054-10")
    assert page.evaluate("window.notReloaded === true")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


@pytest.mark.parametrize(
    ("scheme", "width"), [("light", 1280), ("dark", 1280), ("light", 390)]
)
def test_each_radio_sits_beside_its_words(page, component_origin, scheme, width):
    """Apply this edit to and Delete this scheduled email: every radio is on
    the line of its own label, the circle first (#1656)."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDIT_SERIES)
    radios = page.locator(
        "fieldset[data-series-scope] input[type=radio],"
        " #schedule-delete-form input[type=radio]"
    )
    assert radios.count() == 5
    for index in range(radios.count()):
        radio = radios.nth(index)
        label = page.locator(f"label[for='{radio.get_attribute('id')}']")
        circle, words = radio.bounding_box(), label.bounding_box()
        # Side by side: the words start after the circle and their first line
        # shares its height.
        assert words["x"] >= circle["x"] + circle["width"]
        assert words["y"] < circle["y"] + circle["height"]
        assert circle["y"] < words["y"] + words["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def hover_look(page, markup, *, in_table=False):
    """Hover the one element ``markup`` adds to the page; return its
    background, text colour and shadow once the colour transition ends.

    ``in_table`` draws it in a data table's cell, where the portal uses it
    (a recipient name, whose rest look is reset there so hovering it moves
    nothing, #736); elsewhere it sits in a paragraph."""
    wrap = (
        (
            '<table class="data-table" id="hover-probe"><tbody><tr><td>',
            "</td></tr></tbody></table>",
        )
        if in_table
        else ('<p id="hover-probe">', "</p>")
    )
    page.evaluate(
        "([markup, open, close]) => document.querySelector('main')"
        ".insertAdjacentHTML('beforeend', open + markup + close)",
        [markup, *wrap],
    )
    probe = page.locator("#hover-probe button")
    probe.hover()
    page.wait_for_timeout(300)
    look = probe.evaluate(
        "node => { const style = getComputedStyle(node);"
        " return [style.backgroundColor, style.color, style.boxShadow]; }"
    )
    page.evaluate("document.getElementById('hover-probe').remove()")
    return look


def test_the_aria_disabled_exclusion_leaves_class_hovers_alone(page, component_origin):
    """The shared hover rule skips aria-disabled buttons with :where(), so
    it stays as weak as it was before (#1656): the per-class hover looks
    below still beat it. The expected colours are what main renders."""
    page.emulate_media(color_scheme="light")
    page.goto(component_origin + CALENDAR)
    # Delete: dark red, not the shared accent hover.
    assert hover_look(
        page, '<button type="button" class="button-danger">Delete</button>'
    ) == [
        "rgb(143, 29, 18)",
        "rgb(255, 255, 255)",
        "rgba(16, 24, 40, 0.16) 0px 2px 6px 0px",
    ]
    # Icon delete: the error tint with red text, not a filled teal button.
    icon = hover_look(
        page,
        '<button type="button" class="icon-button-danger" aria-label="x">x</button>',
    )
    assert icon[0] == "rgb(252, 235, 232)"
    assert icon[1] != "rgb(255, 255, 255)"
    # The "i" tip: its own accent, with no shadow.
    tip = hover_look(
        page, '<button type="button" class="toggletip-button" aria-label="x">i</button>'
    )
    assert tip[0] == "rgb(17, 94, 86)"
    assert tip[2] == "none"
    # A link-styled recipient name stays transparent.
    assert (
        hover_look(
            page,
            '<button type="button" class="recipient-link">A</button>',
            in_table=True,
        )[0]
        == "rgba(0, 0, 0, 0)"
    )


# The Move dialog (#1521) and the page script's mark of a dragged send.
MOVE_DIALOG = "#calendar-move"


def move_button(page, day, name):
    """October ``day``'s Move… for the send ``name``, as a screen reader hears it."""
    return cell(page, day).get_by_role("button", name=f"Move {name}…")


def is_open(page, selector):
    """Whether the dialog ``selector`` names is open."""
    return page.locator(selector).evaluate("node => node.open")


@pytest.mark.parametrize("width", [320, 1280])
def test_move_reviews_in_its_dialog_and_applies_in_place(
    page, component_origin, axe_source, width
):
    """Move… on Reminder 1, a new date, Review, then Apply (#1521).

    Only the reminders have Move… (not the sent invitation, not a report),
    each a named icon at least 24px square. The dialog says the send's time
    and that only its date changes; Review the move waits for another date.
    Review posts the move to the reminder's Edit page, whose review opens
    over the Move dialog; its Cancel returns there. Apply redraws the list of
    sends and the grid in place (no reload, the same month, nothing above it
    moved), closes the Move dialog, and the calendar's status line says what
    was applied and takes focus.
    """
    page.set_viewport_size({"width": width, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    assert page.locator("table.calendar-month button.calendar-move").count() == 2
    assert cell(page, 1).locator("button.calendar-move").count() == 0
    assert cell(page, 12).locator("button.calendar-move").count() == 0
    button = move_button(page, 10, "Reminder 1")
    assert button.get_attribute("title") == "Move Reminder 1…"
    assert button.locator("svg[aria-hidden=true]").count() == 1
    box = button.bounding_box()
    assert box["width"] >= 24 and box["height"] >= 24
    heading = page_place(page.locator("#calendar-months-title"))
    page.evaluate("document.getElementById('calendar-source').dataset.old = 'yes'")
    button.click()
    dialog = page.locator(MOVE_DIALOG)
    visible(dialog)
    assert dialog.locator("h2").inner_text() == "Move Reminder 1…"
    what = dialog.locator("#calendar-move-what").inner_text()
    assert what.startswith("Reminder 1 sends at 6:00 AM with the email “")
    assert "keeps the same time of day in this computer’s time zone" in what
    date = dialog.locator('[name="schedule-date"]')
    assert date.input_value() == "2054-10-10"
    assert page.evaluate(FOCUSED) == "id_schedule-date"
    submit = dialog.get_by_role("button", name="Review the move")
    assert submit.is_disabled()
    visible(dialog.get_by_text("Choose another date to review the move."))
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert axe_violations(page, axe_source) == []
    date.fill("2054-10-14")
    assert not submit.is_disabled()
    with page.expect_request(lambda request: request.method == "POST") as sent:
        submit.click()
    shown = page.locator(REVIEW_DIALOG)
    visible(shown)
    # Reminder 1 has sent nothing, so its review has nothing to recall (R40b).
    assert "Reminder — " in shown.inner_text()
    assert "cannot be recalled" not in shown.inner_text()
    assert sent.value.url.startswith(component_origin + "/admin/campaign/schedules/")
    fields = posted(sent.value)
    fields.pop("csrfmiddlewaretoken", None)
    assert fields == {
        "action": ["preview"],
        "move": ["yes"],
        "base_digest": ["a" * 64],
        "zone": ["America/Los_Angeles"],
        "schedule-date": ["2054-10-14"],
    }
    assert is_open(page, MOVE_DIALOG)
    # The review's Cancel returns to the Move dialog, as it was.
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)
    assert is_open(page, MOVE_DIALOG)
    assert date.input_value() == "2054-10-14"
    # Focus returns to Review the move once the review dialog has closed.
    focused(submit)
    submit.click()
    visible(shown)
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(APPLY)
    eventually(page, "document.getElementById('schedules-outcome').textContent !== ''")
    line = page.locator("#schedules-outcome")
    assert line.inner_text().startswith("Scheduled email changed. ")
    assert APPLIED_LINE.search(line.inner_text())
    assert page.evaluate(FOCUSED) == "schedules-outcome"
    assert not is_open(page, MOVE_DIALOG)
    assert not is_open(page, REVIEW_DIALOG)
    # Redrawn in place from the fresh page: a new list, the same month.
    assert (
        page.evaluate("document.getElementById('calendar-source').dataset.old") is None
    )
    assert page.locator("table.calendar-month caption").inner_text() == "October 2054"
    assert page.locator("table.calendar-month button.calendar-move").count() == 2
    assert page.evaluate("window.notReloaded === true")
    assert same_place(page_place(page.locator("#calendar-months-title")), heading)
    # A second move from the redrawn page posts the fresh form's version and
    # zone, and its gate starts again from that send's day.
    move_button(page, 20, "Reminder 2").click()
    dialog = page.locator(MOVE_DIALOG)
    visible(dialog)
    submit = dialog.get_by_role("button", name="Review the move")
    assert submit.is_disabled()
    dialog.locator('[name="schedule-date"]').fill("2054-10-22")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        submit.click()
    fields = posted(sent.value)
    assert fields["base_digest"] == ["a" * 64]
    assert fields["zone"] == ["America/Los_Angeles"]
    assert fields["schedule-date"] == ["2054-10-22"]
    assert not failures


@pytest.mark.parametrize("width", [320, 1280])
def test_a_refused_move_is_said_in_the_dialog(page, component_origin, width):
    """Reminder 2's Edit refuses the date (#1521): the Move dialog stays open,
    its reserved line says why and describes the date field, nothing moves
    and no review dialog opens. Escape closes it, focus back on Move…, and
    the next Move… starts clean. The line is after the buttons, so a
    refusal moves neither of them, on a phone too."""
    page.set_viewport_size({"width": width, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    button = move_button(page, 20, "Reminder 2")
    button.click()
    dialog = page.locator(MOVE_DIALOG)
    visible(dialog)
    assert dialog.locator("h2").inner_text() == "Move Reminder 2…"
    date = dialog.locator('[name="schedule-date"]')
    assert date.input_value() == "2054-10-20"
    date.fill("2054-11-15")
    submit = dialog.get_by_role("button", name="Review the move")
    before = submit.bounding_box()["y"]
    submit.click()
    refusal = dialog.locator("#calendar-move-refusal")
    eventually(
        page, "document.getElementById('calendar-move-refusal').textContent !== ''"
    )
    assert DATE_REFUSAL in refusal.inner_text()
    assert date.get_attribute("aria-invalid") == "true"
    assert "calendar-move-refusal" in date.get_attribute("aria-describedby")
    assert submit.bounding_box()["y"] == before
    assert is_open(page, MOVE_DIALOG)
    assert not is_open(page, REVIEW_DIALOG)
    assert page.locator("[data-error-summary]").count() == 0
    page.keyboard.press("Escape")
    assert not is_open(page, MOVE_DIALOG)
    focused(button)
    move_button(page, 10, "Reminder 1").click()
    visible(dialog)
    assert refusal.inner_text() == ""
    assert date.get_attribute("aria-invalid") == "false"
    assert date.input_value() == "2054-10-10"
    assert not failures


def test_a_keyboard_reaches_move_and_cancels_it(page, component_origin):
    """Tab goes from a reminder's link to its Move…, Enter opens the dialog
    on the date field, and Cancel closes it with focus back on Move…."""
    page.set_viewport_size({"width": 1280, "height": 900})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    cell(page, 10).get_by_role("link", name="6:00 AM Reminder 1").focus()
    tab_to_next(page)
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
        "Move Reminder 1…"
    )
    page.keyboard.press("Enter")
    visible(page.locator(MOVE_DIALOG))
    assert page.evaluate(FOCUSED) == "id_schedule-date"
    page.locator(MOVE_DIALOG).get_by_role("button", name="Cancel").click()
    assert not is_open(page, MOVE_DIALOG)
    focused(move_button(page, 10, "Reminder 1"))
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def centre(locator):
    """The middle of ``locator`` in the window, for the mouse."""
    box = locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_dragging_a_send_onto_a_day_opens_move_with_that_day(page, component_origin):
    """A drag (#1521) outlines the day under the pointer and moves nothing;
    the drop opens the Move dialog with that day, and changes nothing until
    Apply. Escape cancels a drag, a drop on the send's own day does nothing,
    and a plain click still follows the link. The window is tall enough to
    show the whole month, since the mouse moves in window coordinates."""
    page.set_viewport_size({"width": 1280, "height": 1600})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    page.locator("table.calendar-month").scroll_into_view_if_needed()
    link = cell(page, 10).get_by_role("link", name="6:00 AM Reminder 1")
    target = cell(page, 14)
    place = page_place(target)
    page.mouse.move(*centre(link))
    page.mouse.down()
    page.mouse.move(*centre(target), steps=8)
    has_class(target, "calendar-drop-target")
    has_class(cell(page, 10).locator("li.calendar-movable"), "calendar-dragging")
    assert same_place(page_place(target), place)
    page.mouse.up()
    dialog = page.locator(MOVE_DIALOG)
    visible(dialog)
    assert dialog.locator('[name="schedule-date"]').input_value() == "2054-10-14"
    assert not dialog.get_by_role("button", name="Review the move").is_disabled()
    assert page.url.endswith(CALENDAR)
    assert "calendar-drop-target" not in (target.get_attribute("class") or "")
    dialog.get_by_role("button", name="Cancel").click()
    assert not is_open(page, MOVE_DIALOG)
    # The drop changed nothing: Reminder 1 is still on October 10.
    visible(cell(page, 10).get_by_role("link", name="6:00 AM Reminder 1"))
    # Escape cancels a drag: no dialog, no outline, no navigation.
    page.mouse.move(*centre(link))
    page.mouse.down()
    page.mouse.move(*centre(cell(page, 15)), steps=8)
    has_class(cell(page, 15), "calendar-drop-target")
    page.keyboard.press("Escape")
    # Released back over the link, the click that ends the drag still
    # does not follow it.
    page.mouse.move(*centre(link), steps=4)
    page.mouse.up()
    assert not is_open(page, MOVE_DIALOG)
    assert page.url.endswith(CALENDAR)
    assert page.locator(".calendar-drop-target, .calendar-dragging").count() == 0
    # Dropped back on its own day: nothing either.
    page.mouse.move(*centre(link))
    page.mouse.down()
    page.mouse.move(*centre(cell(page, 11)), steps=8)
    page.mouse.move(*centre(cell(page, 10)), steps=8)
    page.mouse.up()
    assert not is_open(page, MOVE_DIALOG)
    assert page.evaluate("window.notReloaded === true")
    assert page.url.endswith(CALENDAR)
    assert not failures
    # A click without a drag follows the link. The component server does not
    # serve the real Edit address it names (it answers 404), and Firefox
    # shows its own error page for that, whose "load" Playwright never sees;
    # so wait only for the navigation to commit, then check where it went.
    href = link.get_attribute("href")
    with page.expect_navigation(wait_until="commit"):
        link.click()
    assert page.url == component_origin + href


def test_dragging_move_onto_a_day_opens_move_with_that_day(page, component_origin):
    """A drag may also start from a send's Move… button (R40b): it outlines
    the day under the pointer and the drop opens the Move dialog with that
    day, focus going back to the button when it closes. Escape cancels a
    drag, and a drop on the send's own day does nothing, even when the
    mouse is released over the button; a plain click on the button still
    opens the dialog on the send's own day."""
    page.set_viewport_size({"width": 1280, "height": 1600})
    at_now(page)
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    page.locator("table.calendar-month").scroll_into_view_if_needed()
    button = move_button(page, 10, "Reminder 1")
    target = cell(page, 14)
    place = page_place(target)
    page.mouse.move(*centre(button))
    page.mouse.down()
    page.mouse.move(*centre(target), steps=8)
    has_class(target, "calendar-drop-target")
    has_class(cell(page, 10).locator("li.calendar-movable"), "calendar-dragging")
    assert same_place(page_place(target), place)
    page.mouse.up()
    dialog = page.locator(MOVE_DIALOG)
    visible(dialog)
    assert dialog.locator('[name="schedule-date"]').input_value() == "2054-10-14"
    assert page.locator(".calendar-drop-target, .calendar-dragging").count() == 0
    dialog.get_by_role("button", name="Cancel").click()
    assert not is_open(page, MOVE_DIALOG)
    focused(button)
    # Escape cancels a drag; released back over the button, the click that
    # ends the drag does not open Move….
    page.mouse.move(*centre(button))
    page.mouse.down()
    page.mouse.move(*centre(cell(page, 15)), steps=8)
    has_class(cell(page, 15), "calendar-drop-target")
    page.keyboard.press("Escape")
    page.mouse.move(*centre(button), steps=4)
    page.mouse.up()
    assert not is_open(page, MOVE_DIALOG)
    assert page.locator(".calendar-drop-target, .calendar-dragging").count() == 0
    # Dropped back on its own day, over the button: nothing either.
    page.mouse.move(*centre(button))
    page.mouse.down()
    page.mouse.move(*centre(cell(page, 11)), steps=8)
    page.mouse.move(*centre(button), steps=8)
    page.mouse.up()
    assert not is_open(page, MOVE_DIALOG)
    # A click without a drag opens Move… on the send's own day.
    button.click()
    visible(dialog)
    assert dialog.locator('[name="schedule-date"]').input_value() == "2054-10-10"
    assert page.evaluate("window.notReloaded === true")
    assert page.url.endswith(CALENDAR)
    assert not failures


def week_shown(page):
    """The caption of the week grid shown (#1808)."""
    return page.locator("table.calendar-week caption").inner_text()


def view_segment(page, name):
    """The View choice's ``name`` segment (Month or Week, #1808)."""
    return page.get_by_role("group", name="View").get_by_role("link", name=name)


@pytest.mark.parametrize("width", [320, 1280])
def test_the_week_view_is_drawn_in_place_and_steps_by_week(
    page, component_origin, axe_source, width
):
    """View's Week (#1808) draws one row of seven days with no request.

    Without a clock, today is outside the campaign, so Week opens on the
    week of October's first day (September 27). The segment keeps its place
    and focus, the view is announced with the segment's word, the address
    names the week's Sunday, and the buttons step a week, unavailable at
    either end with the week's reason. Month goes back to the month of the
    week's Sunday, still with no request.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    month, week = view_segment(page, "Month"), view_segment(page, "Week")
    assert month.get_attribute("aria-current") == "page"
    assert week.get_attribute("aria-current") is None
    assert (
        week.get_attribute("href")
        == f"{CALENDAR}?view=week&week=2054-09-27#calendar-source"
    )
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    places = [page_place(month), page_place(week)]
    week.focus()
    page.keyboard.press("Enter")
    assert week_shown(page) == "Week of September 27, 2054"
    assert page.locator("[data-calendar-name]").inner_text() == (
        "Week of September 27, 2054"
    )
    assert page.url.endswith(f"{CALENDAR}?view=week&week=2054-09-27")
    assert week.get_attribute("aria-current") == "page"
    assert month.get_attribute("aria-current") is None
    assert page.evaluate("document.activeElement.textContent.trim()") == "Week"
    assert page.locator("[data-calendar-said]").inner_text() == (
        "Week: Week of September 27, 2054"
    )
    assert same_place([page_place(month), page_place(week)], places)
    rows = page.locator("table.calendar-week tbody tr")
    assert rows.count() == 1
    assert rows.locator(".calendar-day").all_inner_texts() == [
        "27", "28", "29", "30", "1", "2", "3"
    ]  # fmt: skip
    previous = page.get_by_role("button", name="Previous week")
    following = page.get_by_role("button", name="Next week")
    reason = page.locator("[data-calendar-reason]")
    assert previous.get_attribute("aria-disabled") == "true"
    assert reason.inner_text() == "This is the campaign's first week."
    following.click()
    assert week_shown(page) == "Week of October 4, 2054"
    assert page.url.endswith(f"{CALENDAR}?view=week&week=2054-10-04")
    assert reason.inner_text() == ""
    # Monday, October 5: the weekly report; Saturday the 10th: Reminder 1,
    # with its Move….
    assert "Weekly report" in cell(page, 5).inner_text()
    assert move_button(page, 10, "Reminder 1").count() == 1
    for _ in range(4):
        following.click()
    assert week_shown(page) == "Week of November 1, 2054"
    assert following.get_attribute("aria-disabled") == "true"
    assert reason.inner_text() == "This is the campaign's last week."
    month.click()
    assert month_shown(page) == "November 2054"
    assert page.url.endswith(f"{CALENDAR}?month=2054-11")
    assert (
        page.get_by_role("button", name="Next month").get_attribute("aria-disabled")
        == "true"
    )
    assert page.locator("table.calendar-month tbody tr").count() == 6
    assert requests == []
    assert page.evaluate("window.notReloaded === true")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    assert not failures


def test_the_address_keeps_the_week_and_its_links_return_to_it(page, component_origin):
    """The week view's address (#1808) survives a reload; any day names its
    week, which the address then names by its Sunday; a week outside the
    campaign opens the nearer end. Its Edit links, Add links and table links
    carry the week, and with "now" in the campaign Week opens on this week.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + f"{CALENDAR}?view=week&week=2054-10-14")
    assert week_shown(page) == "Week of October 11, 2054"
    assert page.url.endswith(f"{CALENDAR}?view=week&week=2054-10-11")
    page.reload()
    assert week_shown(page) == "Week of October 11, 2054"
    # Monday the 12th's weekly report, a send still to come, names the week.
    report = cell(page, 12).get_by_role("link", name="6:00 AM Weekly report")
    assert report.get_attribute("href").endswith("&calendar=2054-10-11")
    table = page.get_by_role("link", name="Edit Reminder 1")
    assert table.get_attribute("href").endswith("/?calendar=2054-10-11")
    add = add_link(page, 12).get_attribute("href")
    assert add == f"{NEW}?date=2054-10-12&calendar=2054-10-11"
    show = page.get_by_role("group", name="Show").get_by_role(
        "link", name="Include past emails"
    )
    assert "view=week&week=2054-10-11" in show.get_attribute("href")
    assert "month=" not in show.get_attribute("href")
    page.goto(component_origin + f"{CALENDAR}?view=week&week=2054-01-04")
    assert week_shown(page) == "Week of September 27, 2054"
    assert page.url.endswith(f"{CALENDAR}?view=week&week=2054-09-27")
    # With "now" (October 5) inside the campaign: this week, and Month
    # opens on today's month.
    at_now(page)
    page.goto(component_origin + f"{CALENDAR}?view=week")
    assert week_shown(page) == "Week of October 4, 2054"
    view_segment(page, "Month").click()
    assert month_shown(page) == "October 2054"
    view_segment(page, "Week").click()
    assert week_shown(page) == "Week of October 4, 2054"


def test_move_works_the_same_in_the_week_view(page, component_origin):
    """Move… and a drag (#1808) open the Move dialog from the week view."""
    page.set_viewport_size({"width": 1280, "height": 1600})
    at_now(page)
    page.goto(component_origin + f"{CALENDAR}?view=week&week=2054-10-04")
    failures = watch(page)
    button = move_button(page, 10, "Reminder 1")
    button.click()
    dialog = page.locator(MOVE_DIALOG)
    visible(dialog)
    assert dialog.locator('[name="schedule-date"]').input_value() == "2054-10-10"
    dialog.get_by_role("button", name="Cancel").click()
    assert not is_open(page, MOVE_DIALOG)
    page.locator("table.calendar-week").scroll_into_view_if_needed()
    page.mouse.move(*centre(button))
    page.mouse.down()
    page.mouse.move(*centre(cell(page, 7)), steps=8)
    has_class(cell(page, 7), "calendar-drop-target")
    page.mouse.up()
    visible(dialog)
    assert dialog.locator('[name="schedule-date"]').input_value() == "2054-10-07"
    assert page.url.endswith(f"{CALENDAR}?view=week&week=2054-10-04")
    assert not failures
