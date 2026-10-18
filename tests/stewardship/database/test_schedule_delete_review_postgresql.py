"""Edit scheduled email's Delete (#1656).

Through real Admin sessions and the configuration installer: Edit's Delete
posts ``action=preview`` to Delete scheduled emails and is answered with the
review dialog's fragment, which records nothing; its Apply confirms on Dates
and mail schedules like every schedule review. A series' reminder deletes
itself alone or the whole series (the command line's series delete). The
refusals (a broken rule, a stale page, an unknown, sent or sending schedule,
a malformed post, a send in progress) record nothing.
"""

import json
from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import campaign_wizard, schedule_entry_views
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from .auth_builders import signed_in
from .campaign_builders import advance, campaign_clock, claimed_task
from .test_campaign_series_postgresql import (
    FIRST,
    LAST,
    remembered,
    reminder,
    series_deployment,
)
from .test_campaign_views_postgresql import apply, post
from .test_digest_schedule_planning_postgresql import add_digest
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import DELETE, LIST, definition, setup
from .test_schedule_views_postgresql import pending

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def outside_the_wizard(monkeypatch):
    """Each page as a live campaign sees it, outside the campaign wizard.

    The drafts here are inside it, where Edit saves directly and draws no
    review dialog or Delete (#1404 PR 4b); ``shown`` says otherwise, as the
    calendar's own tests do.
    """
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)


def edit_path(identifier):
    """Edit scheduled email's address for one schedule."""
    return f"/admin/campaign/schedules/{identifier}/"


def deletion(store, identifier, **fields):
    """Edit's Delete post for ``identifier``: a preview, as the page sends it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "schedule_id": str(identifier),
        **fields,
    }


def schedule_ids(store):
    """The ids of the active configuration's saved schedules."""
    return {row["id"] for row in store.active().document()["sections"]["schedules"]}


def test_edit_offers_delete_whose_review_records_nothing_until_apply(
    auth_service, google
):
    """The review lists the removal; Apply on the list deletes the schedule."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    target = definition("reminder")
    page = unescape(browser.get(edit_path(target.pk)).content.decode())
    assert 'id="schedule-delete-form"' in page
    assert f'action="{DELETE}"' in page
    assert f'name="schedule_id" value="{target.pk}"' in page
    assert 'data-review-open="schedule-delete-review"' in page
    assert "Review deleting this scheduled email" in page
    # Not a series' reminder: no scope to choose.
    assert 'name="scope"' not in page
    # New has nothing to delete.
    new = unescape(browser.get("/admin/campaign/schedules/addition/").content.decode())
    assert "schedule-delete-form" not in new
    before = ConfigurationChangeRequest.objects.count()
    review = post(browser, DELETE, deletion(store, target.pk))
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    assert 'data-review-done="Scheduled email deleted."' in text
    assert text.count("— Remove</h3>") == 1
    assert "Applying deletes these schedules" in text
    assert ConfigurationChangeRequest.objects.count() == before
    assert str(target.pk) in schedule_ids(store)
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    target.refresh_from_db()
    assert target.current_revision_id is None
    assert definition("initial")


def test_delete_review_refusals_record_nothing(auth_service, google):
    """A broken rule, a stale page, an unknown schedule or a malformed post."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    initial = definition("initial")
    # Reminders need an initial invitation: said in the dialog.
    alone = post(browser, DELETE, deletion(store, initial.pk))
    assert alone.status_code == 400
    refusal = json.loads(alone.content)["refusal"]
    assert "Reminders need an initial invitation" in refusal["message"]
    assert refusal["fix"].startswith("Nothing was deleted.")
    stale = post(browser, DELETE, deletion(store, initial.pk, base_digest="b" * 64))
    assert stale.status_code == 409
    assert post(browser, DELETE, deletion(store, uuid4())).status_code == 404
    target = definition("reminder").pk
    for fields in (
        {"scope": "everything"},
        {"action": "confirm"},
        {"schedule_id": "not-a-uuid"},
        {"extra": "1"},
    ):
        assert (
            post(browser, DELETE, deletion(store, target, **fields)).status_code == 400
        )
    # The whole series of a reminder in none: reload.
    lonely = post(browser, DELETE, deletion(store, target, scope="series"))
    assert lonely.status_code == 400
    assert (
        "no longer part of a series" in json.loads(lonely.content)["refusal"]["message"]
    )
    # Without the CSRF token, nothing at all.
    assert browser.post(DELETE, deletion(store, target)).status_code == 403
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_sent_or_sending_email_cannot_be_deleted(auth_service, google, monkeypatch):
    """Once its time has passed with planned work, the invitation is read-only."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    initial = definition("initial")
    pending(initial, uuid4())
    later = initial.current_revision.due_at + timedelta(hours=1)
    monkeypatch.setattr(
        schedule_entry_views, "timezone", SimpleNamespace(now=lambda: later)
    )
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, DELETE, deletion(store, initial.pk))
    assert refused.status_code == 403
    assert "already run" in json.loads(refused.content)["refusal"]["message"]
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_send_in_progress_refuses_the_delete(auth_service, google):
    """A running send makes its schedule read-only: refused, nothing recorded.

    The review's own "Sends blocking the change" (no Apply) is the shared
    schedule review's; here the schedule itself is already refused.
    """
    store = auth_service.store
    setup(store)
    actor = uuid4()
    row = pending(ScheduleDefinition.objects.get(kind="reminder"), actor)
    with campaign_clock(row.due_at):
        run = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, DELETE, deletion(store, definition("reminder").pk))
    assert refused.status_code == 403
    message = json.loads(refused.content)["refusal"]["message"]
    assert "already run, or is being prepared" in message
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_digest_delete_stops_the_whole_digest(auth_service, google):
    """A digest has no single-day exceptions: Delete removes it all."""
    store = auth_service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign)
    browser, _ = signed_in()
    page = unescape(browser.get(edit_path(digest)).content.decode())
    assert "This digest repeats, so deleting it stops every send" in page
    review = post(browser, DELETE, deletion(store, digest))
    text = unescape(review.content.decode())
    assert "deleting it stops every send of it still to come" in text
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert str(digest) not in schedule_ids(store)


def test_a_series_reminder_deletes_itself_or_the_whole_series(auth_service, google):
    """This reminder only leaves the series; The whole series removes it."""
    store = auth_service.store
    _campaign, identifier = series_deployment(store)
    browser, _ = signed_in()
    first, last = reminder(store, FIRST), reminder(store, LAST)
    page = unescape(browser.get(edit_path(first)).content.decode())
    assert 'name="scope" value="this" id="schedule-delete-this" checked' in page
    assert 'name="scope" value="series"' in page
    # This reminder only: the other stays, alone in the series.
    review = post(browser, DELETE, deletion(store, first))
    assert 'data-review-done="Scheduled email deleted."' in unescape(
        review.content.decode()
    )
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    ids = schedule_ids(store)
    assert first not in ids and last in ids
    [row] = remembered(store)
    assert row["id"] == identifier and row["values"]["schedules"] == [last]
    # The whole series: its last reminder and the series go.
    review = post(browser, DELETE, deletion(store, last, scope="series"))
    text = unescape(review.content.decode())
    assert 'data-review-done="Reminder series deleted."' in text
    assert "The whole series is deleted: Every " in text
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert last not in schedule_ids(store)
    assert not store.active().document()["sections"].get("schedule_series")


def test_the_whole_series_keeps_a_reminder_that_has_sent(
    auth_service, google, monkeypatch
):
    """A reminder that already sent is not removed: it stays, ordinary."""
    store = auth_service.store
    _campaign, _identifier = series_deployment(store)
    browser, _ = signed_in()
    first, last = reminder(store, FIRST), reminder(store, LAST)
    # The first reminder's time has passed with work planned (so it counts as
    # sent); the last one's time has not come.
    sent = ScheduleDefinition.objects.get(pk=first)
    pending(sent, uuid4())
    later = sent.current_revision.due_at + timedelta(hours=1)
    monkeypatch.setattr(
        schedule_entry_views, "timezone", SimpleNamespace(now=lambda: later)
    )
    review = post(browser, DELETE, deletion(store, last, scope="series"))
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    ids = schedule_ids(store)
    assert first in ids and last not in ids
    # The series record goes; the reminder that sent is no longer in a series.
    assert not store.active().document()["sections"].get("schedule_series")
    assert ScheduleDefinition.objects.get(pk=first).current_revision_id is not None


def test_work_prepared_after_the_review_refuses_apply(auth_service, google):
    """Delete's review, then work for that email, then Apply: refused, unrecorded."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    target = definition("reminder")
    review = post(browser, DELETE, deletion(store, target.pk))
    assert review.status_code == 200, review.content
    pending(target, uuid4())
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, LIST, {"action": "confirm", "preview": token(review)})
    assert refused.status_code == 409
    assert ConfigurationChangeRequest.objects.count() == before
    assert str(target.pk) in schedule_ids(store)
    target.refresh_from_db()
    assert target.current_revision_id is not None
