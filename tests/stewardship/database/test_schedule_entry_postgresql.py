"""The scheduled emails list, New/Edit scheduled email and Delete (#878).

Through real Admin sessions, the configuration installer and the campaign's
real work counts: a New or Edit page reviews and applies exactly like the
date-change review, its review a fragment for the shared review dialog
(#750); Delete records one configuration change; a schedule that
has started sending can be neither changed nor deleted; refusals change
nothing. Pages post times in the browser's zone (#558); most posts here come
from a browser in the campaign's own zone (New York), and one from Los
Angeles, three hours behind.
"""

import json
import re
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
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_campaign_views_postgresql import apply, post
from .test_content_views_postgresql import assert_lacks_capability
from .test_parish_views_postgresql import token
from .test_schedule_views_postgresql import pending

pytestmark = pytest.mark.django_db(transaction=True)

LIST = "/admin/campaign/schedules/"
NEW = "/admin/campaign/schedules/addition/"
DELETE = "/admin/campaign/schedules/deletion/"
SETTINGS = "/admin/campaign/settings/"
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
    """Review changes answers the dialog's review; Apply confirms on the list."""
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
    # The dialog's fragment: no page, no form of its own; the page names
    # where its Apply posts and where the reader returns.
    text = review.content.decode()
    assert "data-review-answer" in text
    assert 'data-review-done="Scheduled email added."' in text
    assert "<html" not in text and "<form" not in text and "<h1" not in text
    assert "data-review-preview" in text
    # One schedule that sends once: a confirmation (#1356), no schedule or
    # counts repeated.
    assert 'data-review-title="Add this scheduled email?"' in text
    assert '<p id="schedule-review-summary">' in text
    assert "Current schedule" not in text and "Reminder — Add" not in text
    dialog = unescape(page.content.decode())
    assert 'data-review-open="schedule-review"' in dialog
    assert 'data-require-change="base_digest"' in dialog
    dialog = dialog[dialog.index('<dialog id="schedule-review"') :]
    assert f'data-review-return="{LIST}"' in dialog
    assert 'data-review-outcome="schedules-outcome"' in dialog
    assert f'<form method="post" action="{LIST}" data-review-apply>' in dialog
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
    # The change's status page, which the dialog follows, leads back to the
    # list, whose reserved status line the dialog ends on.
    status = browser.get(accepted["Location"]).content.decode()
    assert "Return to Dates and mail schedules" in status
    listing = browser.get(LIST).content.decode()
    assert re.search(
        r'<p class="review-outcome" id="schedules-outcome" role="status" '
        r'tabindex="-1" data-review-outcome></p>',
        listing,
    )


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
    # Its generated reminders keep the full review (#1356).
    assert "data-review-title" not in review.content.decode()
    assert 'data-review-done="2 reminders added."' in review.content.decode()
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


def test_a_save_that_changes_nothing_is_refused_for_the_dialog(auth_service, google):
    """Edit saved as shown, or New copying a saved email, names the problem.

    The page's change gate keeps an unchanged Edit from reaching Review
    (#750); a request that bypasses it is a plain refusal for the review
    dialog, not the form drawn again. A New copying a saved email breaks a
    rule between schedules, which stays on the form. Neither shows the
    catch-all rule text, which would suggest a schedule breaks a campaign
    rule, and neither records anything.
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
    same = browser.post(
        path,
        entry(store, **shown)
        | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        headers={"X-Requested-With": "fetch"},
    )
    assert same.status_code == 400
    assert same["Content-Type"].startswith("application/json")
    refusal = json.loads(same.content)["refusal"]
    assert refusal["message"] == (
        "Nothing has changed. Change the date, time, email or comparison, "
        "then choose Review changes."
    )
    assert refusal["fix"] == "Nothing was changed."
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
    # An old page's Review changes is refused the same way.
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


def test_delete_needs_the_csrf_token_and_configure(auth_service, google):
    """Without the CSRF token, or signed in as Staff, nothing is recorded (#879)."""
    store = auth_service.store
    setup(store)
    staff = address("staff@example.org", roles=("staff",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **staff}],
        ).state
        == "applied"
    )
    reminder = definition("reminder")
    values = {"base_digest": store.active().digest, "schedule_id": str(reminder.pk)}
    before = ConfigurationChangeRequest.objects.count()
    browser, _ = signed_in()
    assert browser.post(DELETE, values).status_code == 403
    # Staff sign in from their own Google account, so the Administrator's
    # session stays signed in for the accepted post below.
    google[0].update(sub="synthetic-staff-subject", email="staff@example.org")
    staff_browser, _ = signed_in()
    assert_lacks_capability(staff_browser, post(staff_browser, DELETE, values))
    assert ConfigurationChangeRequest.objects.count() == before
    reminder.refresh_from_db()
    assert reminder.current_revision_id is not None
    # The same post with the token, from the Administrator, is accepted, so
    # the refusals above came from the missing token and capability alone.
    accepted = post(browser, DELETE, values)
    assert accepted.status_code == 302, accepted.content


def email_fields(**fields):
    """Edit's own email, ticked, with ``fields`` (#446)."""
    return {"edit_email": "on", "generate_text": "on"} | fields


def test_an_edited_email_is_a_new_email_only_that_schedule_sends(auth_service, google):
    """Reviewed with the schedule; the shared email and its other sender stay.

    Reminder 1 and Reminder 2 share an email with a planned, not-started
    send. Editing Reminder 2's email adds a new email in the same change,
    re-plans only Reminder 2, and leaves Reminder 1 and the old email as
    they were.
    """
    store = auth_service.store
    _, emails = setup(store)
    owner = str(Campaign.objects.get().pk)
    first = schedule(
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
            [{"operation": "add", "section": "schedules", **first}],
        ).state
        == "applied"
    )
    edited = ScheduleDefinition.objects.get(
        kind="reminder", current_revision__values__date="2054-10-25"
    )
    kept = ScheduleDefinition.objects.get(
        kind="reminder", current_revision__values__date="2054-10-20"
    )
    replaced, stays = pending(edited, uuid4()), pending(kept, uuid4())
    browser, _ = signed_in()
    path = f"/admin/campaign/schedules/{edited.pk}/"
    page = unescape(browser.get(path).content.decode())
    assert "Edit this schedule's email" in page
    assert 'id="schedule-email-sources"' in page
    before = ConfigurationChangeRequest.objects.count()
    html = "<p>Still time to respond. {{ family_code }} {{ family_url }}</p>"
    review = post(
        browser,
        path,
        entry(
            store,
            date="2054-10-25",
            time="09:00",
            template_version=emails["reminder"]["id"],
        )
        | email_fields(subject="Last call", html=html),
    )
    assert review.status_code == 200, review.content
    shown = unescape(review.content.decode())
    assert "New email for this schedule" in shown
    assert "Last call" in shown and "Still time to respond." in shown
    # The fictional sample's code, never a real one.
    assert "ABCDEFGH" in shown
    assert ConfigurationChangeRequest.objects.count() == before
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    edited.refresh_from_db()
    kept.refresh_from_db()
    values = edited.current_revision.values
    assert values["subject"] == "Last call"
    assert values["template_version"] != emails["reminder"]["id"]
    assert (values["date"], values["time"]) == ("2054-10-25", "09:00:00")
    assert kept.current_revision.values["template_version"] == emails["reminder"]["id"]
    document = store.active().document()
    content_rows = {row["id"]: row["values"] for row in document["sections"]["content"]}
    assert content_rows[emails["reminder"]["id"]] == emails["reminder"]["values"]
    copy = content_rows[values["template_version"]]
    assert (copy["kind"], copy["slot"], copy["subject"]) == (
        "email",
        "reminder",
        "Last call",
    )
    assert "Still time to respond." in copy["text"]
    # Only the edited schedule's planned send is replaced.
    replaced.refresh_from_db()
    stays.refresh_from_db()
    assert replaced.state == "skipped"
    assert stays.state == "pending"


def test_new_with_an_edited_email_adds_one_email_its_reminders_share(
    auth_service, google
):
    """New's edited email is one new email every repeated reminder sends.

    The chosen reminder email stays as it was and keeps its sender; a
    reminder's email without its Family code is refused for the posted
    mail type; the chosen email's own text adds nothing.
    """
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    page = unescape(browser.get(NEW).content.decode())
    assert "Edit this schedule's email" in page
    assert 'id="schedule-email-sources"' in page
    base = entry(
        store, kind="reminder", time="09:00", template_version=emails["reminder"]["id"]
    ) | {
        "repeat-repeat": "on",
        "repeat-frequency": "weekly",
        "repeat-weekdays": "1",
        "repeat-start": "2054-10-01",
        "repeat-until": "2054-10-14",
        "repeat-dates": ["2054-10-06", "2054-10-13"],
    }
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        NEW,
        base | email_fields(subject="Reminder", html="<p>Please respond.</p>"),
    )
    assert refused.status_code == 400
    assert "{{ family_code }}" in unescape(refused.content.decode())
    saved = emails["reminder"]["values"]
    same = post(
        browser, NEW, base | email_fields(subject=saved["subject"], html=saved["html"])
    )
    assert same.status_code == 200, same.content
    assert "New email for this schedule" not in unescape(same.content.decode())
    assert ConfigurationChangeRequest.objects.count() == before
    html = "<p>Still time to respond. {{ family_code }} {{ family_url }}</p>"
    review = post(browser, NEW, base | email_fields(subject="Last call", html=html))
    assert review.status_code == 200, review.content
    shown = unescape(review.content.decode())
    assert "New email for this schedule" in shown
    assert "every reminder it adds shares it" in shown
    assert 'data-review-done="2 reminders added."' in shown
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    added = [
        definition_on(day).current_revision.values
        for day in ("2054-10-06", "2054-10-13")
    ]
    assert {row["subject"] for row in added} == {"Last call"}
    (version,) = {row["template_version"] for row in added}
    assert version != emails["reminder"]["id"]
    document = store.active().document()
    content_rows = {row["id"]: row["values"] for row in document["sections"]["content"]}
    assert content_rows[emails["reminder"]["id"]] == saved
    assert (content_rows[version]["slot"], content_rows[version]["subject"]) == (
        "reminder",
        "Last call",
    )
    assert (
        definition_on("2054-10-25").current_revision.values["template_version"]
        == emails["reminder"]["id"]
    )


def test_a_tampered_new_email_post_records_nothing(auth_service, google):
    """A ticked New email with a mismatched or unknown mail type is refused.

    A page script never posts these: a digest type with a reminder's email,
    and a type the page does not offer. Each is refused for its fields
    (400), and no request or content record is added.
    """
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    html = "<p>Still time to respond. {{ family_code }} {{ family_url }}</p>"
    fields = email_fields(subject="Last call", html=html)
    before = (ConfigurationChangeRequest.objects.count(), content_count(store))
    for kind in ("daily_digest", "page"):
        refused = post(
            browser,
            NEW,
            entry(
                store,
                kind=kind,
                date="2054-10-15",
                time="09:00",
                template_version=emails["reminder"]["id"],
            )
            | fields,
        )
        assert refused.status_code == 400, (kind, refused.content)
        assert "data-error-summary" in refused.content.decode()
    assert (ConfigurationChangeRequest.objects.count(), content_count(store)) == before


def definition_on(day):
    """The campaign's one current reminder sent on ``day``."""
    return ScheduleDefinition.objects.get(
        kind="reminder", current_revision__values__date=day
    )


def test_an_unchanged_or_refused_email_records_nothing(auth_service, google):
    """Text left as chosen adds no email; a reminder without its code is refused."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    saved = emails["reminder"]["values"]
    shown = {
        "date": "2054-10-25",
        "time": "09:00",
        "template_version": emails["reminder"]["id"],
    }
    before = ConfigurationChangeRequest.objects.count()
    # The chosen email's own text, with a new time: no new email.
    same = post(
        browser,
        path,
        entry(store, **(shown | {"time": "10:00"}))
        | email_fields(subject=saved["subject"], html=saved["html"]),
    )
    assert same.status_code == 200, same.content
    assert "New email for this schedule" not in unescape(same.content.decode())
    unchanged = post(
        browser,
        path,
        entry(store, **shown)
        | email_fields(subject=saved["subject"], html=saved["html"]),
    )
    assert unchanged.status_code == 400
    assert "Nothing has changed." in unescape(unchanged.content.decode())
    refused = post(
        browser,
        path,
        entry(store, **shown)
        | email_fields(subject="Reminder", html="<p>Please respond.</p>"),
    )
    assert refused.status_code == 400
    page = unescape(refused.content.decode())
    assert "{{ family_code }}" in page and "data-error-summary" in page
    # The posted text is kept on the refused page.
    assert "Please respond." in page
    assert ConfigurationChangeRequest.objects.count() == before


def content_count(store):
    """How many content records the applied configuration holds."""
    return len(store.active().document()["sections"]["content"])


def test_disallowed_markup_is_removed_from_the_stored_copy(auth_service, google):
    """The copy is stored cleaned, as Pages and emails stores an email."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    html = (
        "<p>Respond. {{ family_code }} {{ family_url }}</p><script>alert('x')</script>"
    )
    review = post(
        browser,
        path,
        entry(
            store,
            date="2054-10-25",
            time="09:00",
            template_version=emails["reminder"]["id"],
        )
        | email_fields(subject="Cleaned", html=html),
    )
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    reminder.refresh_from_db()
    rows = {
        row["id"]: row["values"]
        for row in store.active().document()["sections"]["content"]
    }
    stored = rows[reminder.current_revision.values["template_version"]]
    assert "<script" not in stored["html"] and "alert" not in stored["html"]
    assert "Respond." in stored["html"]


def test_a_stale_confirm_after_an_email_edit_adds_no_email(auth_service, google):
    """Work planned after the review refuses Apply; no copy is recorded."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    review = post(
        browser,
        path,
        entry(
            store,
            date="2054-10-25",
            time="09:00",
            template_version=emails["reminder"]["id"],
        )
        | email_fields(
            subject="Too late",
            html="<p>Respond. {{ family_code }} {{ family_url }}</p>",
        ),
    )
    assert review.status_code == 200, review.content
    before = content_count(store)
    pending(reminder, uuid4())
    refused = post(browser, LIST, {"action": "confirm", "preview": token(review)})
    assert refused.status_code == 409
    assert content_count(store) == before
    reminder.refresh_from_db()
    sent = reminder.current_revision.values["template_version"]
    assert sent == emails["reminder"]["id"]


def test_a_refused_candidate_names_the_email(auth_service, google, monkeypatch):
    """A configuration refusal of a change with an edited email shows there."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts import schedule_changes

    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"

    def refuse(base, patch, **_kwargs):
        """Refuse the candidate, as an email the configuration rejects is."""
        raise ConfigError("refused")

    monkeypatch.setattr(schedule_changes, "build_candidate", refuse)
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        path,
        entry(
            store,
            date="2054-10-25",
            time="09:00",
            template_version=emails["reminder"]["id"],
        )
        | email_fields(
            subject="Refused",
            html="<p>Respond. {{ family_code }} {{ family_url }}</p>",
        ),
    )
    assert refused.status_code == 400
    page = unescape(refused.content.decode())
    assert "Check this email's content and placeholders." in page
    assert "must fit the campaign" not in page
    # At the HTML source, so the in-place answer marks it and the summary
    # links to it.
    assert 'id="id_html_error"' in page
    assert 'href="#id_html"' in page
    assert ConfigurationChangeRequest.objects.count() == before


def test_an_archived_campaign_refuses_every_schedule_page(auth_service, google):
    """Every Mail schedules page and save refuses an archived campaign (#1258).

    Each answers 409 with the menu's own reason (ui-conventions,
    "Unavailable menu pages"), never the held message that says to try
    again later, by GET and by POST: the list, the date-change review, New,
    Edit with its inline email editor, the calendar, Delete, the unsaved
    email's test and the confirmation of a review signed before archiving,
    on the list or on Campaign settings, which shares the review's salt.
    Nothing is requested and no email is added, so no page of an archived
    campaign can lead to an email's editor either.
    """
    from django.db import connection

    from parishkit.stewardship.accounts import admin_navigation as navigation

    from .auth_builders import unguarded

    store = auth_service.store
    _, emails = setup(store)
    reminder = definition("reminder")
    edit = f"/admin/campaign/schedules/{reminder.pk}/"
    browser, _ = signed_in()
    edited = entry(
        store,
        date="2054-10-25",
        time="09:00",
        template_version=emails["reminder"]["id"],
    ) | email_fields(
        subject="Archived", html="<p>Archived. {{ family_code }} {{ family_url }}</p>"
    )
    review = post(browser, edit, edited)
    assert review.status_code == 200, review.content
    with unguarded(), connection.cursor() as cursor:
        cursor.execute("UPDATE public.stewardship_campaign SET state='archived'")
    before = ConfigurationChangeRequest.objects.count()
    records = content_count(store)
    page = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}
    for path in (
        LIST,
        f"{LIST}?end_date=2054-10-20",
        NEW,
        edit,
        "/admin/campaign/schedules/calendar/",
    ):
        response = browser.get(path, **page)
        body = response.content.decode()
        assert response.status_code == 409, (path, body)
        assert f"{navigation.ARCHIVED}." in body, path
        assert "Try again later" not in body, path
        assert "data-edit-url" not in body, path
    window = {"action": "preview", "base_digest": store.active().digest}
    for path, data in (
        (edit, edited),
        (NEW, entry(store, kind="reminder", date="2054-10-26", time="09:00")),
        (LIST, window),
        (LIST, {"action": "confirm", "preview": token(review)}),
        # Campaign settings confirms a live end date signed with the same
        # salt, so it must not confirm the earlier review either.
        (SETTINGS, {"action": "confirm", "preview": token(review)}),
        (DELETE, {"base_digest": store.active().digest, "schedule_id": reminder.pk}),
        ("/admin/campaign/schedules/test-email/", {}),
    ):
        response = post(browser, path, data)
        assert response.status_code == 409, (path, response.content)
        refusal = json.loads(response.content)["refusal"]["message"]
        assert refusal == f"{navigation.ARCHIVED}.", path
    assert ConfigurationChangeRequest.objects.count() == before
    assert content_count(store) == records
    reminder.refresh_from_db()
    sent = reminder.current_revision.values["template_version"]
    assert sent == emails["reminder"]["id"]
