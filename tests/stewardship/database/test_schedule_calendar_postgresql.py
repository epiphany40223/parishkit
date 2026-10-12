"""The read-only Campaign calendar against the real configuration (#469).

The draft's Initial invitation (October 1) and reminder (October 25) come
from the New and Edit scheduled email tests' setup; the calendar lists them
with the same names, statuses and Edit pages as the scheduled emails list.
"""

from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import schedule_calendar_views
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_admin_navigation_postgresql import flow_steps
from .test_schedule_entry_postgresql import LIST, definition
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
