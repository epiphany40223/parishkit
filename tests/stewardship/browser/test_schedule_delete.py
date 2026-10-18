"""Edit scheduled email's Delete (#1656), through the shared review dialog.

Edit's Delete posts its review without a page load (``EDIT_DELETE``,
answered with the review of removing Reminder 1), opens its own review
dialog, and Apply confirms on Dates and mail schedules (``APPLY``, Applied at
once), then returns to where Cancel goes: the list, or the calendar's
October for ``EDIT_FROM_CALENDAR``. A series' reminder chooses This reminder
only or The whole series (``SERIES_DELETE``); the Initial invitation's
delete is refused in the server's words (``INITIAL_DELETE``).
"""

import re
from urllib.parse import parse_qs

import pytest

from .schedule_list_components import (
    APPLY,
    CALENDAR_OCTOBER,
    EDIT,
    EDIT_DELETE,
    EDIT_DIGEST,
    EDIT_FROM_CALENDAR,
    EDIT_INITIAL,
    EDIT_SERIES,
    INITIAL_DELETE,
    LIST,
    REFUSAL,
    SERIES_DELETE,
)
from .test_components import axe_violations
from .test_schedule_calendar import page_place
from .test_schedule_list import APPLIED_LINE, FOCUSED, watch
from .waits import eventually, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
DIALOG = "#schedule-delete-review"
TITLE = "Review deleting this scheduled email"


def delete_button(page):
    """Edit's Delete button (not the page's other buttons)."""
    return page.locator("#schedule-delete-form").get_by_role(
        "button", name="Delete", exact=True
    )


def open_review(page, path):
    """Choose Delete and return the POST it sent and the dialog it opened."""
    with page.expect_request(lambda request: request.method == "POST") as sent:
        delete_button(page).click()
    shown = page.locator(DIALOG)
    visible(shown)
    return sent.value, shown


def fields(request):
    """A captured POST's fields, one value each."""
    posted = parse_qs(request.post_data or "", keep_blank_values=True)
    return {name: values[0] for name, values in posted.items()}


def applied(page, shown, back, done):
    """Apply the review; the page returns to ``back`` and says ``done``."""
    with page.expect_request(lambda request: request.method == "POST") as sent:
        shown.get_by_role("button", name="Apply").click()
    assert sent.value.url.endswith(APPLY)
    assert fields(sent.value)["preview"] == "synthetic-preview"
    page.wait_for_url(re.compile(re.escape(back) + "$"))
    eventually(page, "document.getElementById('schedules-outcome').textContent !== ''")
    line = page.locator("#schedules-outcome").inner_text()
    assert line.startswith(f"{done} ")
    assert APPLIED_LINE.search(line)
    assert page.evaluate(FOCUSED) == "schedules-outcome"


@pytest.mark.parametrize("width", [320, 1280])
def test_delete_reviews_in_place_and_returns_to_the_list(
    page, component_origin, axe_source, width
):
    """Delete posts its review without a page load; the dialog names what is
    removed, focus on its title; Apply returns to the list, which says so."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + EDIT)
    failures = watch(page)
    visible(page.get_by_role("heading", name="Delete this scheduled email"))
    assert page.locator('#schedule-delete-form [name="scope"]').count() == 0
    help_line = page.locator("#schedule-delete-help").inner_text()
    assert help_line.startswith("Deleting cancels this email's sends")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    request, shown = open_review(page, EDIT)
    assert request.url.endswith(EDIT_DELETE)
    posted = fields(request)
    assert posted["action"] == "preview"
    assert posted["base_digest"] == "a" * 64
    assert set(posted) == {
        "csrfmiddlewaretoken",
        "action",
        "base_digest",
        "schedule_id",
    }
    assert shown.locator("[data-review-title]").inner_text() == TITLE
    assert page.evaluate(FOCUSED) == "schedule-delete-review-title"
    text = shown.inner_text()
    assert "Reminder — Remove" in text
    assert "Applying deletes these schedules" in text
    assert page.evaluate("window.notReloaded === true")
    # Edit's own review dialog stays shut.
    hidden(page.locator("#schedule-review"))
    assert axe_violations(page, axe_source) == []
    assert not failures
    applied(page, shown, LIST, "Scheduled email deleted.")


def test_cancel_changes_nothing_and_returns_focus_to_delete(page, component_origin):
    """Escape or Cancel closes the review; focus goes back to Delete."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT)
    failures = watch(page)
    before = page_place(delete_button(page))
    _request, shown = open_review(page, EDIT)
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)
    eventually(page, "document.activeElement.textContent.trim() === 'Delete'")
    assert same_place(page_place(delete_button(page)), before)
    _request, shown = open_review(page, EDIT)
    page.keyboard.press("Escape")
    hidden(shown)
    assert page.url.endswith(EDIT)
    assert page.evaluate("window.notReloaded === true")
    assert not failures


def test_a_series_reminder_deletes_this_one_or_the_whole_series(page, component_origin):
    """The choice is This reminder only or The whole series; choosing moves
    nothing, and the whole series is posted and reviewed as such."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_SERIES)
    failures = watch(page)
    form = page.locator("#schedule-delete-form")
    this = form.get_by_role("radio", name="This reminder only")
    whole = form.get_by_role("radio", name="The whole series")
    assert this.is_checked() and not whole.is_checked()
    before = page_place(delete_button(page))
    whole.check()
    assert same_place(page_place(delete_button(page)), before)
    request, shown = open_review(page, EDIT_SERIES)
    assert request.url.endswith(SERIES_DELETE)
    assert fields(request)["scope"] == "series"
    text = shown.inner_text()
    assert text.count("Reminder — Remove") == 2
    assert "The whole series is deleted: Every day, October 1, 2054" in text
    assert not failures
    applied(page, shown, LIST, "Reminder series deleted.")


def test_a_refused_delete_says_why_in_the_dialog(page, component_origin):
    """The Initial invitation can't go while reminders need it: the server's
    words in the dialog, with no Apply."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_INITIAL)
    failures = watch(page)
    request, shown = open_review(page, EDIT_INITIAL)
    assert request.url.endswith(INITIAL_DELETE)
    error = shown.locator("[data-confirm-error]")
    visible(error)
    assert REFUSAL in error.inner_text()
    assert shown.get_by_role("button", name="Apply").count() == 0
    shown.get_by_role("button", name="Cancel").click()
    hidden(shown)
    assert not failures


def test_delete_from_the_calendar_returns_to_its_month(page, component_origin):
    """Opened from the calendar's October: Cancel and Apply go back there."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_FROM_CALENDAR)
    cancel = page.get_by_role("link", name="Cancel")
    assert cancel.get_attribute("href") == CALENDAR_OCTOBER
    _request, shown = open_review(page, EDIT_FROM_CALENDAR)
    applied(page, shown, CALENDAR_OCTOBER, "Scheduled email deleted.")
    assert page.locator("table.calendar-month caption").inner_text() == "October 2054"


def test_a_digest_says_its_delete_stops_every_later_send(page, component_origin):
    """A digest has no single-day exceptions: its help says Delete stops it."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + EDIT_DIGEST)
    help_line = page.locator("#schedule-delete-help").inner_text()
    assert help_line.startswith("This digest repeats, so deleting it stops every")
