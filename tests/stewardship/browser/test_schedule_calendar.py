"""The Campaign calendar (#469), its Add on a day (#1521) and its month view.

The page script draws the table of sends as one month at a time, with
Previous month and Next month limited to the campaign's months (#1656),
each send on its day in the browser's time zone. The campaign (October
1–31, 2054) is in New York's zone; the default page is Los Angeles's,
three hours behind, and Auckland is a day ahead. The schedules are
schedule_list_components': the Initial invitation (sent, October 1),
Reminder 1 (October 10), Reminder 2 (October 20) and a weekly digest on
Mondays, each at 9:00 AM New York time, plus a daily digest at 7:00 AM
from October 2 through November 1, the day after the campaign closes;
"now" is October 5, so its first four sends have passed.

Each campaign day's (+) Add opens New scheduled email with that send date
(``ADD_ON``, October 12), whose Cancel and Apply return to the calendar's
October (``CALENDAR_OCTOBER``). Without a clock the browser's today (2026) is
outside the campaign, so the calendar opens on its first month, October.
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
    choose,
    email_for,
    near,
    review,
    watch,
)
from .waits import eventually, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
MONDAYS = [5, 12, 19, 26]
# Every email by date's rows (#1656): every send (three one-time schedules,
# the weekly digest's Mondays and the daily one's 31 days), and the upcoming
# ones, without the sent invitation and the daily digest's four past days.
ALL_ROWS = 3 + len(MONDAYS) + 31
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
        assert "Weekly Admin digest" in cell(page, monday).inner_text()
    assert "Campaign closes" in cell(page, 31).inner_text()
    # A daily digest day that has passed is muted, unlinked and read as Past;
    # a later one links to the digest's Edit page.
    past = cell(page, 3).locator("li")
    assert past.inner_text().startswith("4:00 AM Daily Admin digest")
    assert past.get_attribute("class") == "schedule-past"
    assert past.get_by_role("link").count() == 0
    assert past.locator(".visually-hidden").inner_text() == ", Past"
    visible(cell(page, 6).get_by_role("link", name="4:00 AM Daily Admin digest"))
    # A phone scrolls the grid inside its region; the page never scrolls sideways.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # Its last send, the day after the campaign closes, is in November's grid,
    # which starts on a Sunday.
    page.get_by_role("button", name="Next month").click()
    assert grids.locator("caption").all_inner_texts() == ["November 2054"]
    november = cell(page, 1)
    assert grids.locator("tbody td").first.inner_text().startswith("1")
    visible(november.get_by_role("link", name="4:00 AM Daily Admin digest"))
    assert "Campaign" not in november.inner_text()
    # The table lists the same sends as text, with compact times: by default
    # the upcoming ones (not the sent invitation or four past digest days).
    rows = page.locator("table.calendar-sends tbody tr")
    assert rows.count() == UPCOMING_ROWS
    assert rows.first.locator("time").inner_text() == "Oct 5, 2054 6:00 AM"
    assert rows.first.locator("th").inner_text() == "Weekly Admin digest"
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
            assert "Weekly Admin digest" in cell(page, monday + 1).inner_text()
            assert "Weekly Admin digest" not in cell(page, monday).inner_text()
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
    # From October 11's Add, Tab reaches that day's send (a daily digest),
    # then October 12's Add: each day's Add comes before its sends.
    add_link(page, 11).focus()
    tab_to_next(page)
    assert page.evaluate("document.activeElement.textContent") == (
        "4:00 AM Daily Admin digest"
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
    assert not button.is_disabled()
    choose(page, "template_version", email_for(page, "reminder"))
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
    digest = cell(page, 1).get_by_role("link", name="4:00 AM Daily Admin digest")
    assert digest.get_attribute("href").endswith("/?calendar=2054-11")


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
    assert first_row(page) == ("Nov 1, 2054 4:00 AM", "Daily Admin digest")
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


def hover_look(page, markup):
    """Hover the one element ``markup`` adds to the page; return its
    background, text colour and shadow once the colour transition ends."""
    page.evaluate(
        "markup => document.querySelector('main').insertAdjacentHTML('beforeend',"
        " '<p id=\"hover-probe\">' + markup + '</p>')",
        markup,
    )
    probe = page.locator("#hover-probe > *")
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
        hover_look(page, '<button type="button" class="recipient-link">A</button>')[0]
        == "rgba(0, 0, 0, 0)"
    )
