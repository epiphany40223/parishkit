"""Apply re-checks a reviewed send time against the database clock (#1464).

A signed schedule review lives up to 15 minutes, so a send time that was
ahead at Review can pass before Apply; that reminder would then send at
once. Through real Admin sessions, the command line and the configuration
installer: Apply on Dates and mail schedules (an ordinary Edit, and a series
edit's three scopes) and ``schedule confirm`` refuse once a reviewed time
has passed, changing nothing, and admit while it is still ahead. A time
already past at Review is not re-checked. The database clock the review and
confirmation read (``admin_editing.database_now``) is pinned and advanced
here, as the page's clock is (``schedule_entry_views.timezone``); one test
uses the real ``clock_timestamp()``.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.core import signing
from django.db import connection

from parishkit.stewardship.accounts import admin_editing, schedule_entry_views
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.schedule_changes import preview_salt
from parishkit.stewardship.audit.models import OperationalLog

from .auth_builders import signed_in
from .automation_builders import paired
from .test_admin_schedule_cli_postgresql import (  # noqa: F401
    admin,
    confirm,
    events,
    later,
    preview,
)
from .test_campaign_series_postgresql import FIRST, LAST, reminder, series_deployment
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import LIST, definition, entry, setup
from .test_schedule_series_page_postgresql import edit_post, values_of
from .test_schedule_views_postgresql import fields as window_fields
from .test_schedule_views_postgresql import setup as campaign_with_reminder

pytestmark = pytest.mark.django_db(transaction=True)

# The refusal's words (web.refusals.send_time_passed).
PASSED = (
    "A send time in this change has passed since it was reviewed, so that "
    "email would send at once."
)


@pytest.fixture
def clock(monkeypatch):
    """Pin the database clock and the page's clock; ``clock.at(moment)``
    moves both."""
    pinned = SimpleNamespace(now=None)

    def at(moment):
        """Set both clocks to ``moment``."""
        pinned.now = moment

    monkeypatch.setattr(admin_editing, "database_now", lambda: pinned.now)
    monkeypatch.setattr(
        schedule_entry_views, "timezone", SimpleNamespace(now=lambda: pinned.now)
    )
    pinned.at = at
    return pinned


def fetch(browser, path, values):
    """``post`` as the page script sends it, so a refusal answers JSON."""
    return browser.post(
        path,
        values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        headers={"X-Requested-With": "fetch"},
    )


def refused_apply(browser, review):
    """Apply ``review`` on the list; assert the passed-time refusal, in the
    dialog's JSON, with its durable refusal-log entry, and that nothing was
    recorded."""
    before = ConfigurationChangeRequest.objects.count()
    refused = fetch(browser, LIST, {"action": "confirm", "preview": token(review)})
    assert refused.status_code == 409, refused.content
    body = json.loads(refused.content)
    assert body["refusal"]["message"] == PASSED
    assert body["refusal"]["link"]["label"] == "Review the changes again"
    assert ConfigurationChangeRequest.objects.count() == before
    entry = OperationalLog.objects.filter(event="admin_input_refused").latest(
        "created_at"
    )
    assert entry.level == "INFO"
    assert entry.context["reason"] == "send_time_passed"
    assert entry.context["codes"] == ["stale_version"]
    assert entry.context["status"] == 409


def test_an_edit_whose_reviewed_time_passed_is_refused_at_apply(
    auth_service, google, clock
):
    """The issue's case: at 8:00 AM the reminder is moved to 8:05 AM, and
    Apply at 8:10 AM is refused; reviewed again at 8:30 AM it applies."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder_row = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder_row.pk}/"
    clock.at(datetime(2054, 10, 25, 12, tzinfo=UTC))  # 8:00 AM in New York
    fields = {"date": "2054-10-25", "template_version": emails["reminder"]["id"]}
    review = post(browser, path, entry(store, time="08:05", **fields))
    assert review.status_code == 200, review.content
    clock.at(datetime(2054, 10, 25, 12, 10, tzinfo=UTC))
    refused_apply(browser, review)
    reminder_row.refresh_from_db()
    assert reminder_row.current_revision.values["time"] == "09:00:00"
    # Reviewed again with a later time, Apply goes through before it passes.
    review = post(browser, path, entry(store, time="08:30", **fields))
    clock.at(datetime(2054, 10, 25, 12, 29, tzinfo=UTC))
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    reminder_row.refresh_from_db()
    assert reminder_row.current_revision.values["time"] == "08:30:00"


def test_a_time_already_past_at_review_is_not_rechecked(auth_service, google, clock):
    """The ordinary Edit may move its reminder into the past (it sends at
    once, as the Administrator chose at Review): Apply is not refused."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder_row = definition("reminder")
    clock.at(datetime(2054, 10, 25, 12, tzinfo=UTC))
    review = post(
        browser,
        f"/admin/campaign/schedules/{reminder_row.pk}/",
        entry(
            store,
            date="2054-10-25",
            time="07:00",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert review.status_code == 200, review.content
    clock.at(datetime(2054, 10, 25, 12, 10, tzinfo=UTC))
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    reminder_row.refresh_from_db()
    assert reminder_row.current_revision.values["time"] == "07:00:00"


def test_the_real_database_clock_is_the_one_checked(auth_service, google):
    """No pinned clock: a signed send time a minute before PostgreSQL's
    ``clock_timestamp()`` is refused at Apply."""
    store = auth_service.store
    campaign, emails = setup(store)
    browser, _ = signed_in()
    review = post(
        browser,
        f"/admin/campaign/schedules/{definition('reminder').pk}/",
        entry(
            store,
            date="2054-10-25",
            time="10:30",
            template_version=emails["reminder"]["id"],
        ),
    )
    salt = preview_salt(campaign.pk)
    intent = signing.loads(token(review), salt=salt)
    # The far-future fixture campaign's time is carried as it was reviewed.
    assert intent["due"] == ["2054-10-25T14:30:00+00:00"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp() - interval '1 minute'")
        passed = cursor.fetchone()[0]
    stale = signing.dumps(intent | {"due": [passed.isoformat()]}, salt=salt)
    before = ConfigurationChangeRequest.objects.count()
    refused = fetch(browser, LIST, {"action": "confirm", "preview": stale})
    assert refused.status_code == 409, refused.content
    assert json.loads(refused.content)["refusal"]["message"] == PASSED
    assert ConfigurationChangeRequest.objects.count() == before


# The series' first reminder's day, 8:00 AM in New York: both reminders are
# unsent and ahead, and 8:05 AM is still ahead at Review.
REVIEWED = datetime(2054, 10, 18, 12, tzinfo=UTC)
APPLIED = REVIEWED + timedelta(minutes=10)


@pytest.mark.parametrize(
    ("edited", "scope"),
    [(FIRST, "this"), (FIRST, "following"), (LAST, "series")],
)
def test_a_series_edit_whose_reviewed_time_passed_is_refused(
    auth_service, google, clock, edited, scope
):
    """This reminder only, This and following and The whole series: each
    moves the October 18 reminder to 8:05 AM, which passes before Apply.
    From the 25th, the whole series makes it an unrelated reminder."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    clock.at(REVIEWED)
    identifier = reminder(store, edited)
    fields = edit_post(store, identifier, time="08:05") | {"series-scope": scope}
    if scope == "this":
        fields["schedule-date"] = edited
    review = post(browser, f"/admin/campaign/schedules/{identifier}/", fields)
    assert review.status_code == 200, review.content
    clock.at(APPLIED)
    refused_apply(browser, review)
    assert values_of(store, reminder(store, FIRST))["time"] == "09:00:00"
    assert values_of(store, reminder(store, LAST))["time"] == "09:00:00"


def test_schedule_confirm_refuses_a_passed_time_as_stale(admin, google, clock):  # noqa: F811
    """The command line's parity: a previewed 10:30 AM that has passed is
    ``stale_version`` and records nothing; one still ahead confirms."""
    store = admin.service.store
    campaign, _ = campaign_with_reminder(store)
    _, secret, _ = paired(admin.service)
    clock.at(datetime(2054, 10, 25, 14, tzinfo=UTC))  # 10:00 AM in New York
    code, document = preview(admin, secret, later(store, campaign))
    assert code == 0, document
    stale = document["result"]["preview"]["token"]
    before = ConfigurationChangeRequest.objects.count()
    clock.at(datetime(2054, 10, 25, 14, 30, tzinfo=UTC))
    code, document = confirm(admin, secret, stale)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert ConfigurationChangeRequest.objects.count() == before and not events()
    # The command line's own refusal entry names the reason.
    refusal = OperationalLog.objects.filter(event="admin_input_refused").latest(
        "created_at"
    )
    assert refusal.context["reason"] == "send_time_passed"
    assert refusal.context["codes"] == ["stale_version"]
    assert refusal.context["command"] == "schedule confirm"
    clock.at(datetime(2054, 10, 25, 14, 10, tzinfo=UTC))
    code, document = preview(admin, secret, later(store, campaign))
    code, document = confirm(admin, secret, document["result"]["preview"]["token"])
    assert code == 0, document
    assert document["result"]["created"] is True


def test_a_draft_time_zone_change_whose_moved_time_passed_is_refused(
    auth_service, google, clock
):
    """Campaign settings moves the draft to Chicago: the invitation's 9:00 AM
    is an hour later (October 1, 14:00 UTC). Reviewed at 8:00 AM Eastern, it
    is refused at 9:30 AM Eastern, though no schedule row was edited."""
    store = auth_service.store
    campaign, path = campaign_with_reminder(store)
    browser, _ = signed_in()
    clock.at(datetime(2054, 10, 1, 12, tzinfo=UTC))
    data, _ = window_fields(store, campaign)
    review = post(browser, path, data | {"window-timezone": "America/Chicago"})
    assert review.status_code == 200, review.content
    intent = signing.loads(token(review), salt=preview_salt(campaign.pk))
    assert "2054-10-01T14:00:00+00:00" in intent["due"]
    clock.at(datetime(2054, 10, 1, 14, 30, tzinfo=UTC))
    refused_apply(browser, review)
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["timezone"] == "America/New_York"
