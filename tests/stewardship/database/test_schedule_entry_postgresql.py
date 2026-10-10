"""The scheduled emails list, New/Edit scheduled email and Delete (#878).

Through real Admin sessions, the configuration installer and the campaign's
real work counts: a New or Edit page reviews and applies exactly like the
date-change review; Delete records one configuration change; a schedule that
has started sending can be neither changed nor deleted; refusals change
nothing. Pages post times in the browser's zone (#558); most posts here come
from a browser in the campaign's own zone (New York), and one from Los
Angeles, three hours behind.
"""

import json
from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import schedule_entry_views, schedule_views
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign, ScheduleDefinition

from ..campaign_factory import campaign as campaign_record
from ..campaign_factory import schedule
from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_views_postgresql import pending

pytestmark = pytest.mark.django_db(transaction=True)

LIST = "/admin/campaign/schedules/"
NEW = "/admin/campaign/schedules/addition/"
DELETE = "/admin/campaign/schedules/deletion/"
CHICAGO = "America/Chicago"


def setup(store, row=None, **reminder_values):
    """A draft whose invitation (Oct 1) and reminder (Oct 25) send saved emails.

    ``row`` replaces the factory campaign and ``reminder_values`` the
    reminder's date or time. Returns the campaign and the saved emails by
    mail type.
    """
    result, owner, invitation = add_draft(store, store.active(), uuid4(), row)
    assert result.state == "applied"
    emails = {
        kind: content(owner["id"], kind="email", slot=kind, subject=f"{kind} mail")
        for kind in ("initial", "reminder")
    }
    reminder = schedule(
        owner["id"],
        kind="reminder",
        template_version=emails["reminder"]["id"],
        subject="reminder mail",
        **({"date": "2054-10-25"} | reminder_values),
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                *(
                    {"operation": "add", "section": "content", **row}
                    for row in emails.values()
                ),
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": invitation["id"],
                    "values": {
                        "template_version": emails["initial"]["id"],
                        "subject": "initial mail",
                    },
                },
                {"operation": "add", "section": "schedules", **reminder},
            ],
        ).state
        == "applied"
    )
    return Campaign.objects.get(), emails


def definition(kind):
    """The campaign's one current schedule of ``kind``."""
    return ScheduleDefinition.objects.get(kind=kind, current_revision__isnull=False)


def entry(store, zone="America/New_York", **fields):
    """A New or Edit page's post from a browser in ``zone``: a preview."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "zone": zone,
        **{f"schedule-{name}": value for name, value in fields.items()},
    }


def test_times_are_shown_and_taken_in_the_browser_zone(auth_service, google):
    """Edit carries the saved moment; a Los Angeles post is saved 3 hours on.

    Two reminders at the same moment, typed in different zones, are the
    conflict the review refuses; a post without a zone is refused.
    """
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    page = unescape(browser.get(path).content.decode())
    # October 25, 9:00 AM in New York, for the page script to show.
    assert 'data-schedule-local="2054-10-25T13:00:00+00:00"' in page
    assert "data-browser-zone" in page and "campaign's time zone" not in page
    review = post(
        browser,
        NEW,
        entry(
            store,
            "America/Los_Angeles",
            kind="reminder",
            date="2054-10-15",
            time="9am",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    added = ScheduleDefinition.objects.get(
        kind="reminder",
        current_revision__isnull=False,
        current_revision__values__date="2054-10-15",
    )
    assert added.current_revision.values["time"] == "12:00:00"
    before = ConfigurationChangeRequest.objects.count()
    # 6:00 AM in Los Angeles on the 25th is Reminder 1's own moment.
    clash = post(
        browser,
        NEW,
        entry(
            store,
            "America/Los_Angeles",
            kind="reminder",
            date="2054-10-25",
            time="06:00",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert clash.status_code == 400
    assert "cannot be sent at the same date and time" in unescape(
        clash.content.decode()
    )
    for zone in ("", "Mars/Olympus_Mons"):
        refused = post(
            browser,
            NEW,
            entry(
                store,
                zone,
                kind="reminder",
                date="2054-10-16",
                time="09:00",
                template_version=emails["reminder"]["id"],
            ),
        )
        assert refused.status_code == 400
        assert "didn't report a time zone" in unescape(refused.content.decode())
    assert ConfigurationChangeRequest.objects.count() == before


def test_list_offers_new_and_row_actions_without_a_window_panel(auth_service, google):
    """One line of dates, New scheduled email, Edit and Delete per row."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    response = browser.get(LIST)
    assert response.status_code == 200
    page = unescape(response.content.decode())
    assert "Campaign window" not in page
    assert "Campaign dates: " in page
    assert f'href="{NEW}"' in page
    assert f'action="{DELETE}"' in page
    reminder = definition("reminder")
    assert 'aria-label="Edit Reminder 1"' in page
    assert f'href="/admin/campaign/schedules/{reminder.pk}/"' in page
    assert 'aria-label="Delete Initial invitation"' in page
    assert "(your time)" not in page
    # A list, not a step of a change.
    assert flow_steps(response.content) is None
    assert browser.get(f"{LIST}?sort=-when").status_code == 200
    assert browser.get(f"{LIST}?sort=subject").status_code == 400


def test_a_new_reminder_is_reviewed_then_applied_from_the_list(auth_service, google):
    """Review and save shows the review; Apply confirms on the list."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    page = browser.get(NEW)
    assert page.status_code == 200
    assert flow_steps(page.content) == (STEPS, "Make changes")
    before = ConfigurationChangeRequest.objects.count()
    review = post(
        browser,
        NEW,
        entry(
            store,
            kind="reminder",
            date="2054-10-15",
            time="9am",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert review.status_code == 200, review.content
    assert flow_steps(review.content) == (STEPS, "Review")
    assert f'action="{LIST}"' in review.content.decode()
    assert ConfigurationChangeRequest.objects.count() == before
    accepted = post(browser, LIST, {"action": "confirm", "preview": token(review)})
    apply(store, accepted)
    dates = sorted(
        row.current_revision.values["date"]
        for row in ScheduleDefinition.objects.filter(
            kind="reminder", current_revision__isnull=False
        )
    )
    assert dates == ["2054-10-15", "2054-10-25"]
    # The change's status page leads back to the list.
    status = browser.get(accepted["Location"]).content.decode()
    assert "Return to Dates and mail schedules" in status


def test_a_repeat_adds_one_ordinary_reminder_per_date(auth_service, google):
    """Each posted date is its own reminder; the rule itself is not saved."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    data = entry(
        store, kind="reminder", time="09:00", template_version=emails["reminder"]["id"]
    ) | {
        "repeat-repeat": "on",
        "repeat-frequency": "weekly",
        "repeat-weekdays": "1",
        "repeat-start": "2054-10-01",
        "repeat-until": "2054-10-14",
        "repeat-dates": ["2054-10-06", "2054-10-13"],
    }
    review = post(browser, NEW, data)
    assert review.status_code == 200, review.content
    assert review.content.decode().count("Reminder — Add") == 2
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert (
        ScheduleDefinition.objects.filter(
            kind="reminder", current_revision__isnull=False
        ).count()
        == 3
    )
    # A repeat is for reminders only, and is offered on New only.
    refused = post(
        browser,
        NEW,
        data | {"schedule-kind": "initial", "base_digest": store.active().digest},
    )
    assert refused.status_code == 400
    assert "Only reminders repeat." in unescape(refused.content.decode())


def test_a_refused_new_schedule_shows_its_errors_and_records_nothing(
    auth_service, google
):
    """A date outside the campaign at its field; a second invitation at the top."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    outside = post(
        browser,
        NEW,
        entry(
            store,
            kind="reminder",
            date="2054-11-15",
            time="09:00",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert outside.status_code == 400
    page = unescape(outside.content.decode())
    assert "Choose a date within the campaign" in page
    assert "data-error-summary" in page
    assert 'href="#id_schedule-date"' in page
    second = post(
        browser,
        NEW,
        entry(
            store,
            kind="initial",
            date="2054-10-02",
            time="09:00",
            template_version=emails["initial"]["id"],
        ),
    )
    assert second.status_code == 400
    assert "Only one Initial invitation is allowed" in unescape(second.content.decode())
    assert ConfigurationChangeRequest.objects.count() == before


def test_edit_is_filled_in_and_saves_only_that_schedule(auth_service, google):
    """The saved values fill the page; its mail type cannot change."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    page = unescape(browser.get(path).content.decode())
    assert 'value="2054-10-25"' in page
    assert "Edit scheduled email" in page and "Reminder 1" in page
    assert "data-repeat-entry" not in page
    data = entry(
        store,
        date="2054-10-25",
        time="10:30",
        template_version=emails["reminder"]["id"],
    )
    # A posted mail type is ignored (the field is fixed); a repeat is refused.
    assert post(browser, path, data | {"repeat-repeat": "on"}).status_code == 400
    review = post(browser, path, data | {"schedule-kind": "initial"})
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    reminder.refresh_from_db()
    assert reminder.current_revision.values["time"] == "10:30:00"
    assert reminder.current_revision.values["kind"] == "reminder"
    assert browser.get(f"/admin/campaign/schedules/{uuid4()}/").status_code == 404


def test_a_save_that_changes_nothing_says_so_in_place(auth_service, google):
    """Edit saved as shown, or New copying a saved email, names the problem.

    Neither shows the catch-all rule text, which would suggest a schedule
    breaks a campaign rule, and neither records anything.
    """
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    before = ConfigurationChangeRequest.objects.count()
    shown = {
        "date": "2054-10-25",
        "time": "09:00",
        "template_version": emails["reminder"]["id"],
    }
    same = post(browser, path, entry(store, **shown))
    assert same.status_code == 400
    page = unescape(same.content.decode())
    assert "Nothing has changed. Change the date, time or email, then save." in page
    assert "data-error-summary" in page
    assert "must fit the campaign" not in page
    copy = post(browser, NEW, entry(store, kind="reminder", **shown))
    assert copy.status_code == 400
    page = unescape(copy.content.decode())
    assert "cannot be sent at the same date and time" in page
    assert "Nothing has changed" not in page
    assert "must fit the campaign" not in page
    assert ConfigurationChangeRequest.objects.count() == before


def test_an_unchanged_edit_keeps_its_send_when_clocks_fall_back(auth_service, google):
    """Saving Edit as shown keeps the saved send; a typed 1:30 is the first.

    New York changes its clocks an hour before Chicago. The campaign's
    reminder at 2:30 AM New York time on November 1, 2054 (07:30 UTC) shows
    in Chicago as its second 1:30 AM. Reading that 1:30 again would be the
    first (06:30 UTC, 1:30 AM in New York); an Edit that changes only the
    email keeps 2:30 instead, while a New reminder typed at 1:30 in Chicago
    is the first. (The unit tests cover the same case with the zones the
    other way round. A save with nothing changed at all is refused as no
    change, so this one changes the email.)
    """
    store = auth_service.store
    row = campaign_record(end_date="2054-11-05")
    _, emails = setup(store, row, date="2054-11-01", time="02:30:00")
    other = content(row["id"], kind="email", slot="reminder", subject="Second mail")
    added = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "content", **other}],
    )
    assert added.state == "applied"
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    page = unescape(browser.get(path).content.decode())
    assert 'data-schedule-local="2054-11-01T07:30:00+00:00"' in page
    shown = {
        "date": "2054-11-01",
        "time": "01:30",
        "template_version": emails["reminder"]["id"],
    }
    edited = shown | {"template_version": other["id"]}
    review = post(browser, path, entry(store, CHICAGO, **edited))
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    reminder.refresh_from_db()
    values = reminder.current_revision.values
    assert (values["date"], values["time"]) == ("2054-11-01", "02:30:00")
    assert values["template_version"] == other["id"]
    review = post(browser, NEW, entry(store, CHICAGO, kind="reminder", **shown))
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    added = ScheduleDefinition.objects.exclude(pk=reminder.pk).get(
        kind="reminder", current_revision__isnull=False
    )
    values = added.current_revision.values
    assert (values["date"], values["time"]) == ("2054-11-01", "01:30:00")


def test_delete_records_one_change_that_removes_the_schedule(auth_service, google):
    """The dialog's post records the change; the status page leads back."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    accepted = post(
        browser,
        DELETE,
        {"base_digest": store.active().digest, "schedule_id": str(reminder.pk)},
    )
    assert accepted["Location"].startswith("/admin/changes/")
    apply(store, accepted)
    reminder.refresh_from_db()
    assert reminder.current_revision_id is None
    assert definition("initial")
    status = browser.get(accepted["Location"]).content.decode()
    assert "Return to Dates and mail schedules" in status


def test_delete_cancels_only_the_deleted_schedules_planned_sends(auth_service, google):
    """Two Upcoming reminders with planned sends; deleting one skips only its own.

    The sends are pending (not started), so neither reminder is read-only:
    the real clock is long before their 2054 send times.
    """
    store = auth_service.store
    _, emails = setup(store)
    owner = str(Campaign.objects.get().pk)
    other = schedule(
        owner,
        kind="reminder",
        date="2054-10-20",
        template_version=emails["reminder"]["id"],
        subject="reminder mail",
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **other}],
        ).state
        == "applied"
    )
    deleted = ScheduleDefinition.objects.get(
        kind="reminder", current_revision__values__date="2054-10-25"
    )
    kept = ScheduleDefinition.objects.get(
        kind="reminder", current_revision__values__date="2054-10-20"
    )
    gone, stays = pending(deleted, uuid4()), pending(kept, uuid4())
    browser, _ = signed_in()
    page = unescape(browser.get(LIST).content.decode())
    assert 'aria-label="Delete Reminder 2"' in page
    accepted = post(
        browser,
        DELETE,
        {"base_digest": store.active().digest, "schedule_id": str(deleted.pk)},
    )
    apply(store, accepted)
    deleted.refresh_from_db()
    assert deleted.current_revision_id is None
    gone.refresh_from_db()
    stays.refresh_from_db()
    assert (gone.state, gone.reason) == ("skipped", "schedule_removed")
    assert stays.state == "pending"


def test_delete_refuses_a_broken_rule_a_stale_page_or_an_unknown_schedule(
    auth_service, google
):
    """Each refusal says why and records nothing."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    initial = definition("initial")
    alone = post(
        browser,
        DELETE,
        {"base_digest": store.active().digest, "schedule_id": str(initial.pk)},
    )
    assert alone.status_code == 400
    refusal = json.loads(alone.content)["refusal"]
    assert "Reminders need an initial invitation" in refusal["message"]
    stale = post(
        browser, DELETE, {"base_digest": "b" * 64, "schedule_id": str(initial.pk)}
    )
    assert stale.status_code == 409
    unknown = post(
        browser,
        DELETE,
        {"base_digest": store.active().digest, "schedule_id": str(uuid4())},
    )
    assert unknown.status_code == 404
    assert (
        post(browser, DELETE, {"base_digest": store.active().digest}).status_code == 400
    )
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_sending_schedule_can_be_neither_changed_nor_deleted(
    auth_service, google, monkeypatch
):
    """Once its time has passed with planned work, the invitation is read-only."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    initial = definition("initial")
    pending(initial, uuid4())
    later = initial.current_revision.due_at + timedelta(hours=1)
    clock = SimpleNamespace(now=lambda: later)
    monkeypatch.setattr(schedule_views, "timezone", clock)
    monkeypatch.setattr(schedule_entry_views, "timezone", clock)
    page = unescape(browser.get(LIST).content.decode())
    assert 'data-schedule-state="sending"' in page
    assert 'aria-label="Edit Initial invitation"' not in page
    assert 'aria-label="Delete Initial invitation"' not in page
    assert 'aria-label="Edit Reminder 1"' in page
    before = ConfigurationChangeRequest.objects.count()
    edit = f"/admin/campaign/schedules/{initial.pk}/"
    assert browser.get(edit).status_code == 403
    # An old page's Review and save is refused the same way.
    changed = post(
        browser,
        edit,
        entry(
            store,
            date="2054-10-02",
            time="09:00",
            template_version=str(initial.current_revision.values["template_version"]),
        ),
    )
    assert changed.status_code == 403
    assert "already run" in unescape(changed.content.decode())
    refused = post(
        browser,
        DELETE,
        {"base_digest": store.active().digest, "schedule_id": str(initial.pk)},
    )
    assert refused.status_code == 403
    assert "already run" in json.loads(refused.content)["refusal"]["message"]
    assert ConfigurationChangeRequest.objects.count() == before
