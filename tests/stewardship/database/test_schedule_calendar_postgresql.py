"""The Campaign calendar against the real configuration (#469, #1521).

The draft's Initial invitation (October 1) and reminder (October 25) come
from the New and Edit scheduled email tests' setup; the calendar lists them
with the same names, statuses and Edit pages as the scheduled emails list,
and its Add on a day opens New scheduled email with that send date.
"""

import re
from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import campaign_wizard, schedule_calendar_views
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_admin_navigation_postgresql import flow_steps
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import LIST, NEW, definition, entry
from .test_schedule_entry_postgresql import setup as draft_with_schedules
from .test_schedule_views_postgresql import pending

pytestmark = pytest.mark.django_db(transaction=True)

CALENDAR = "/admin/campaign/schedules/calendar/"


def test_calendar_lists_each_send_and_changes_nothing(auth_service, google):
    """Every send by date, linked to Edit; no form, no change recorded."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    response = browser.get(CALENDAR)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    page = unescape(response.content.decode())
    assert "<h1>Campaign calendar</h1>" in page
    assert "<title>Campaign calendar" in page
    assert "Campaign dates: " in page
    assert 'data-start="2054-10-01" data-end="2054-10-31"' in page
    assert page.count("data-calendar-send ") == 2
    reminder = definition("reminder")
    assert f'data-edit-url="/admin/campaign/schedules/{reminder.pk}/"' in page
    assert 'aria-label="Edit Reminder 1"' in page
    assert "data-local-instant data-compact" in page
    assert "calendar-v1.js" in page
    # No form of its own (the session's renewal form is every page's).
    assert 'action="/admin/campaign/' not in page
    assert flow_steps(response.content) is None
    # The trail is the way back (navigation rule 3), not a link of its own
    # (#521).
    assert f'<li><a href="{LIST}">Dates and mail schedules</a></li>' in page
    # While the Campaign setup stepper applies, its action row is the one
    # way back to its step (#1404), and the page adds no link of its own.
    assert page.count("Return to Dates and mail schedules") == 1
    actions = page.split("data-campaign-step-actions", 1)[1]
    assert "Return to Dates and mail schedules" in actions.split("</div>", 1)[0]
    assert ConfigurationChangeRequest.objects.count() == before
    # The list links it; the calendar takes no parameters and no POST.
    assert f'href="{CALENDAR}"' in unescape(browser.get(LIST).content.decode())
    assert browser.get(f"{CALENDAR}?sort=when").status_code == 400
    assert browser.post(CALENDAR).status_code in {403, 405}


def test_a_schedule_that_has_run_is_listed_without_edit(
    auth_service, google, monkeypatch
):
    """Once its time has passed with planned work, the invitation is read-only."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    initial = definition("initial")
    pending(initial, uuid4())
    later = initial.current_revision.due_at + timedelta(hours=1)
    monkeypatch.setattr(
        schedule_calendar_views, "timezone", SimpleNamespace(now=lambda: later)
    )
    page = unescape(browser.get(CALENDAR).content.decode())
    assert 'data-schedule-state="sending"' in page
    assert f'data-edit-url="/admin/campaign/schedules/{initial.pk}/"' not in page
    assert 'aria-label="Edit Initial invitation"' not in page
    assert 'aria-label="Edit Reminder 1"' in page


def test_the_calendar_is_for_administrators_signed_in(auth_service, google, client):
    """Signed out, the calendar shows nothing of the campaign."""
    draft_with_schedules(auth_service.store)
    response = client.get(CALENDAR)
    assert response.status_code in {302, 401, 403}
    assert "data-calendar-send" not in response.content.decode()


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_an_admin_without_configure_is_refused(auth_service, google, role):
    """Staff and Ministry leaders lack CONFIGURE, so the calendar refuses them."""
    store = auth_service.store
    draft_with_schedules(store)
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    response = browser.get(CALENDAR)
    assert response.status_code == 403
    assert "data-calendar-send" not in response.content.decode()


def test_add_on_a_day_opens_new_with_that_date_and_returns_here(
    auth_service, google, monkeypatch
):
    """The calendar's Add (#1521) is New with the day filled in; Apply comes back.

    The day is the only thing filled in, so the review is New's: a reminder
    added from it is reviewed and applied exactly as from the list, and the
    calendar keeps the reserved status line the dialog ends on.

    Outside the campaign wizard (``shown`` says so here, as a live campaign
    would): inside it New saves directly, with no Cancel or review dialog
    of its own (#1404 PR 4b).
    """
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    store = auth_service.store
    _, emails = draft_with_schedules(store)
    browser, _ = signed_in()
    calendar = unescape(browser.get(CALENDAR).content.decode())
    assert f'data-add-url="{NEW}"' in calendar
    assert 'data-add-name="Add a scheduled email on {date}"' in calendar
    assert re.search(
        r'<p class="review-outcome" id="schedules-outcome" role="status" '
        r'tabindex="-1" data-review-outcome></p>',
        calendar,
    )
    response = browser.get(f"{NEW}?date=2054-10-12")
    assert response.status_code == 200
    page = unescape(response.content.decode())
    # Drawn as a reminder on that day (the page script would clear a date
    # with no mail type).
    assert re.search(
        r'<input type="date" name="schedule-date" value="2054-10-12"', page
    )
    assert '<option value="reminder" selected>' in page
    # Cancel and the dialog's return are the calendar on that day's month
    # (#1656); Apply still confirms on the list, and the page posts to its
    # own path.
    assert f'<a href="{CALENDAR}?month=2054-10">Cancel</a>' in page
    dialog = page[page.index('<dialog id="schedule-review"') :]
    assert f'data-review-return="{CALENDAR}?month=2054-10"' in dialog
    assert 'data-review-outcome="schedules-outcome"' in dialog
    assert f'<form method="post" action="{LIST}" data-review-apply>' in dialog
    assert f'action="{NEW}" id="schedule-entry-form"' in page
    # Plain New is unchanged: blank, back to the list.
    plain = unescape(browser.get(NEW).content.decode())
    assert 'name="schedule-date" value=' not in plain
    assert f'<a href="{LIST}">Cancel</a>' in plain
    review = post(
        browser,
        NEW,
        entry(
            store,
            kind="reminder",
            date="2054-10-12",
            time="9am",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert ScheduleDefinition.objects.filter(
        kind="reminder",
        current_revision__isnull=False,
        current_revision__values__date="2054-10-12",
    ).exists()


@pytest.mark.parametrize(
    "query",
    [
        "date=2054-13-01",
        "date=20541012",
        "date=",
        "date=2054-10-12&date=2054-10-13",
        "date=2054-10-12&sort=when",
    ],
)
def test_new_refuses_an_add_day_that_is_not_one_date(auth_service, google, query):
    """Only one ISO day is taken; anything else is an unknown address."""
    draft_with_schedules(auth_service.store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    assert browser.get(f"{NEW}?{query}").status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before


def test_edit_takes_no_add_day(auth_service, google):
    """Edit's date is the saved schedule's; ?date= is refused there."""
    draft_with_schedules(auth_service.store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    assert browser.get(f"{path}?date=2054-10-12").status_code == 400


def test_edit_from_the_calendar_returns_to_its_month(auth_service, google, monkeypatch):
    """?calendar=<month> (#1656) points Cancel and Apply at that month.

    Outside the campaign wizard, as the Add test above is.
    """
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    path = f"/admin/campaign/schedules/{definition('reminder').pk}/"
    page = unescape(browser.get(f"{path}?calendar=2054-11").content.decode())
    back = f"{CALENDAR}?month=2054-11"
    assert f'<a href="{back}">Cancel</a>' in page
    assert f'data-review-return="{back}"' in page
    assert f'data-review-return="{LIST}"' not in page
    plain = unescape(browser.get(path).content.decode())
    assert f'data-review-return="{LIST}"' in plain
    for refused in (
        f"{path}?calendar=2054-13",
        f"{path}?calendar=",
        f"{path}?date=2054-10-12",
    ):
        assert browser.get(refused).status_code == 400, refused
    # New from a day's Add returns to that day's month.
    added = unescape(browser.get(f"{NEW}?date=2054-10-12").content.decode())
    assert f'<a href="{CALENDAR}?month=2054-10">Cancel</a>' in added


def test_the_calendar_takes_only_a_well_formed_month(auth_service, google):
    """?month= (#1656) is the page script's; a malformed one is an unknown
    address."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    page = browser.get(f"{CALENDAR}?month=2054-11")
    assert page.status_code == 200
    assert b"data-calendar-step" in page.content
    # A month outside the campaign is the script's to move to the nearer end.
    assert browser.get(f"{CALENDAR}?month=2060-01").status_code == 200
    for month in ("2054-13", "2054-1", "november", ""):
        assert browser.get(f"{CALENDAR}?month={month}").status_code == 400, month
    assert browser.get(f"{CALENDAR}?month=2054-10&month=2054-11").status_code == 400
