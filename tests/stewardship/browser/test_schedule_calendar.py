"""The read-only Campaign calendar (#469).

The page script draws the table of sends as month grids, each send on its day
in the browser's time zone. The campaign (October 1–31, 2054) is in New
York's zone; the default page is Los Angeles's, three hours behind, and
Auckland is a day ahead. The schedules are schedule_list_components': the
Initial invitation (sent, October 1), Reminder 1 (October 10), Reminder 2
(October 20) and a weekly digest on Mondays, each at 9:00 AM New York time,
plus a daily digest at 7:00 AM from October 2 through November 1, the day
after the campaign closes; "now" is October 5, so its first four sends have
passed.
"""

import pytest

from .schedule_list_components import CALENDAR, LIST
from .test_components import axe_violations
from .test_schedule_list import watch
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
MONDAYS = [5, 12, 19, 26]


def cell(page, number, month=0):
    """Day ``number``'s cell in the ``month``-th grid (0 is October 2054)."""
    grid = page.locator("table.calendar-month").nth(month)
    cells = grid.locator("td:has(.calendar-day)")
    texts = cells.locator(".calendar-day").all_inner_texts()
    return cells.nth(texts.index(str(number)))


@pytest.mark.parametrize("width", [320, 1280])
def test_calendar_draws_each_send_on_its_local_day(
    page, component_origin, axe_source, width
):
    """October and November grids, Sunday first, sends at Los Angeles times."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + CALENDAR)
    failures = watch(page)
    grids = page.locator("table.calendar-month")
    assert grids.locator("caption").all_inner_texts() == [
        "October 2054",
        "November 2054",
    ]
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
    # Its last send, the day after the campaign closes, is in November's grid,
    # which starts on a Sunday.
    november = cell(page, 1, month=1)
    assert grids.nth(1).locator("tbody td").first.inner_text().startswith("1")
    visible(november.get_by_role("link", name="4:00 AM Daily Admin digest"))
    assert "Campaign" not in november.inner_text()
    # The table lists the same sends as text, with compact times.
    rows = page.locator("table.calendar-sends tbody tr")
    assert rows.count() == 3 + len(MONDAYS) + 31
    assert rows.first.locator("time").inner_text() == "Oct 1, 2054 6:00 AM"
    assert rows.first.locator("th").inner_text() == "Initial invitation"
    assert rows.first.get_by_role("link").count() == 0
    visible(page.get_by_role("link", name="Edit Reminder 1"))
    # A phone scrolls the grid inside its region; the page never scrolls sideways.
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
