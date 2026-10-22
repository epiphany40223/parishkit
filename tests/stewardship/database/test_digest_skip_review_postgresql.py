"""Skip one send of an Admin digest: Edit's This email only and ``skip`` (#1658).

Through real Admin sessions, the command line and the configuration
installer: Edit opened for one of a digest's calendar sends offers This email
only, whose review records nothing until Apply; Apply stores the send in the
digest's ``skip_dates``, so the database's slot check answers no slot for
it, and later changes keep it. The refusals (a send already past or with
Production work, one already skipped, a date that is not a send, a skip of
anything but a digest, a malformed post) record nothing, and Apply refuses
once the send has passed or work for it appeared after the review. A
Testing rehearsal's work does not block a skip. A weekday change drops a
skip of a send still ahead that it moves off the digest (listed in the
review), so changing back does not skip it again; a past skip is kept.
"""

import json
from datetime import date, timedelta
from html import unescape
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.accounts import admin_editing, campaign_wizard
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.digest_skips import send_of
from parishkit.stewardship.accounts.report_rows import digest_view
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from ..configuration_factory import PARISH_TIMEZONE
from .auth_builders import signed_in
from .automation_builders import paired
from .campaign_builders import campaign_clock, occurrence
from .report_builders import add_digest
from .test_admin_schedule_cli_postgresql import (  # noqa: F401
    admin,
    confirm,
    one,
    preview,
)
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import DELETE, LIST, definition, entry, setup

pytestmark = pytest.mark.django_db(transaction=True)

# A daily digest's send, as its slot key names it (the reported day).
DAY = "2054-10-05"


@pytest.fixture(autouse=True)
def outside_the_wizard(monkeypatch):
    """Each page as a live campaign sees it, outside the campaign wizard."""
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)


def edit_path(identifier, **parameters):
    """Edit scheduled email's address for one schedule, with its parameters."""
    query = "&".join(f"{name}={value}" for name, value in parameters.items())
    return f"/admin/campaign/schedules/{identifier}/" + (f"?{query}" if query else "")


def skipping(store, identifier, **fields):
    """Edit's Delete post with This email only for ``DAY``, as the page sends it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "schedule_id": str(identifier),
        "scope": "send",
        "send": DAY,
        **fields,
    }


def saved_values(store, identifier):
    """The active configuration's values of one saved schedule."""
    return next(
        row["values"]
        for row in store.active().document()["sections"]["schedules"]
        if row["id"] == str(identifier)
    )


def slot_due(identifier, slot):
    """The database's canonical slot check for one digest send."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_catchup_due_v1(%s,%s)", [identifier, slot])
        return cursor.fetchone()[0]


def due_of(store, campaign, identifier, slot=DAY):
    """When the digest's send at ``slot`` is due (UTC).

    The converted report is read as the digest view the editors skip its
    sends through (``report_rows.digest_view``).
    """
    campaign.refresh_from_db()
    return send_of(
        digest_view(saved_values(store, identifier)),
        campaign.active_configuration.values,
        slot,
    ).due_at


def worked(identifier, due, slot=DAY, *, live=True):
    """Plan one Admins occurrence of the digest's send at ``slot``.

    The builder plans Testing rehearsal work; ``live`` then makes it
    Production work, with user triggers off for just that update (the
    campaign would otherwise need a real activation, which changes every
    schedule rule these tests read).
    """
    with campaign_clock(due):
        row = occurrence(
            ScheduleDefinition.objects.get(pk=identifier),
            uuid4(),
            "admins",
            slot=slot,
            due_at=due,
        )
    if live:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            cursor.execute(
                "UPDATE stewardship_schedule_occurrence SET mode='production',"
                "routing='production' WHERE id=%s",
                [row.pk],
            )
    return row


def refusal(response):
    """A refused post's plain message."""
    return json.loads(response.content)["refusal"]["message"]


def test_this_email_only_skips_one_send_and_keeps_the_digest(auth_service, google):
    """Offered from a calendar send; Apply stores the skip, nothing else moves."""
    store = auth_service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign)
    browser, _ = signed_in()
    page = unescape(
        browser.get(edit_path(digest, send=DAY, calendar="2054-10")).content.decode()
    )
    assert 'name="scope" value="send" id="schedule-delete-send" checked' in page
    assert 'name="scope" value="this" id="schedule-delete-digest"' in page
    assert f'name="send" value="{DAY}"' in page
    assert "This email only skips just the report's send at" in page
    # Opened from the list, a digest's Delete is the whole digest alone.
    plain = unescape(browser.get(edit_path(digest)).content.decode())
    assert 'name="scope"' not in plain and 'name="send"' not in plain
    before = ConfigurationChangeRequest.objects.count()
    review = post(browser, DELETE, skipping(store, digest))
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    assert 'data-review-done="Email skipped."' in text
    # The send is named by its send time, shown browser-local; a Daily
    # digest's slot key (the reported day) is not shown.
    due = due_of(store, campaign, digest)
    assert (
        "This email only: the report skips its send at "
        f'<time datetime="{due.isoformat()}" data-local-instant>'
    ) in text
    assert f"for {DAY}" not in text
    assert "Applying skips this send" in text
    assert ConfigurationChangeRequest.objects.count() == before
    assert slot_due(digest, DAY) is not None
    # The whole digest, with the send still posted, is the digest's delete.
    whole = post(browser, DELETE, skipping(store, digest, scope="this"))
    assert 'data-review-done="Scheduled email deleted."' in unescape(
        whole.content.decode()
    )
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert ConfigurationChangeRequest.objects.count() == before + 1
    values = saved_values(store, digest)
    assert values["skip_dates"] == [DAY]
    assert ScheduleDefinition.objects.get(pk=digest).current_revision_id is not None
    # The database answers no slot for it, and the days around it are kept.
    assert slot_due(digest, DAY) is None
    assert slot_due(digest, "2054-10-04") is not None
    assert slot_due(digest, "2054-10-06") is not None
    # A skipped send is no longer offered: Edit draws the whole digest only.
    again = unescape(browser.get(edit_path(digest, send=DAY)).content.decode())
    assert 'name="scope"' not in again


def test_skip_refusals_record_nothing(auth_service, google, monkeypatch):
    """Past, with work, already skipped, not a send, not a digest, malformed."""
    store = auth_service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    for fields in (
        {"send": "2054-12-25"},  # not one of the digest's sends
        {"send": "20541005"},  # not the canonical date
        {"send": ""},
    ):
        response = post(browser, DELETE, skipping(store, digest, **fields))
        assert response.status_code == 400, fields
    without = skipping(store, digest)
    del without["send"]
    assert post(browser, DELETE, without).status_code == 400
    # A reminder has no sends to skip.
    reminder = definition("reminder").pk
    assert post(browser, DELETE, skipping(store, reminder)).status_code == 400
    # An address naming a send that is not a date.
    assert browser.get(edit_path(digest, send="tomorrow")).status_code == 400
    due = due_of(store, campaign, digest)
    # A send whose time has passed, by the database's clock.
    with monkeypatch.context() as patched:
        patched.setattr(admin_editing, "database_now", lambda: due)
        passed = post(browser, DELETE, skipping(store, digest))
    assert passed.status_code == 403
    assert "already past" in refusal(passed)
    assert ConfigurationChangeRequest.objects.count() == before
    # Skipped already: a stale page.
    review = post(browser, DELETE, skipping(store, digest))
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    stale = post(browser, DELETE, skipping(store, digest))
    assert stale.status_code == 409
    after = ConfigurationChangeRequest.objects.count()
    # A send with work: refused, and Edit offers only the whole digest.
    other = "2054-10-07"
    worked(digest, due_of(store, campaign, digest, other), other)
    refused = post(browser, DELETE, skipping(store, digest, send=other))
    assert refused.status_code == 403
    assert "already been prepared or sent" in refusal(refused)
    page = unescape(browser.get(edit_path(digest, send=other)).content.decode())
    assert 'name="scope"' not in page
    assert ConfigurationChangeRequest.objects.count() == after


def test_apply_refuses_once_the_send_passed_or_has_work(
    auth_service, google, monkeypatch
):
    """The reviewed send's time and the work fingerprint are rechecked."""
    store = auth_service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign)
    browser, _ = signed_in()
    due = due_of(store, campaign, digest)
    review = post(browser, DELETE, skipping(store, digest))
    assert review.status_code == 200, review.content
    before = ConfigurationChangeRequest.objects.count()
    with monkeypatch.context() as patched:
        patched.setattr(admin_editing, "database_now", lambda: due + timedelta(1))
        late = post(browser, LIST, {"action": "confirm", "preview": token(review)})
    assert late.status_code == 409
    worked(digest, due)
    stale = post(browser, LIST, {"action": "confirm", "preview": token(review)})
    assert stale.status_code == 409
    assert ConfigurationChangeRequest.objects.count() == before
    assert saved_values(store, digest)["skip_dates"] == []


def test_the_command_line_skips_a_send_and_later_changes_keep_it(admin, google):  # noqa: F811
    """``skip`` reviews as the page does; ``skip_dates`` survives a later edit."""
    store = admin.service.store
    campaign, _emails = setup(store)
    digest = str(add_digest(store, campaign))
    _, secret, _ = paired(admin.service)
    code, document = preview(
        admin, secret, {"schedules": [{"id": digest, "skip": DAY}]}
    )
    assert code == 0, document
    [change] = document["result"]["changes"]
    assert change["before"]["skip_dates"] == []
    assert change["after"]["skip_dates"] == [DAY]
    code, confirmed = confirm(admin, secret, document["result"]["preview"]["token"])
    assert code == 0, confirmed
    install_request(
        store,
        request_id=UUID(confirmed["result"]["request"]["request_id"]),
        correlation_id=uuid4(),
    )
    code, shown = one(admin, "schedule", "show", secret=secret)
    assert code == 0, shown
    [row] = [item for item in shown["result"]["schedules"] if item["id"] == digest]
    assert row["skip_dates"] == [DAY]
    # A later time change keeps the skipped send.
    code, document = preview(
        admin, secret, {"schedules": [{"id": digest, "time": "01:30:00"}]}
    )
    assert code == 0, document
    assert document["result"]["changes"][0]["after"]["skip_dates"] == [DAY]
    # A skip of a send skipped already, or of a reminder, is refused.
    code, document = preview(
        admin, secret, {"schedules": [{"id": digest, "skip": DAY}]}
    )
    assert code == 1 and document["error"]["code"] == "stale_version", document
    reminder = str(definition("reminder").pk)
    code, document = preview(
        admin, secret, {"schedules": [{"id": reminder, "skip": DAY}]}
    )
    assert code == 1 and document["error"]["code"] == "invalid", document


def test_testing_rehearsal_work_does_not_block_a_skip(auth_service, google):
    """Only Production work blocks This email only; a rehearsal's does not."""
    store = auth_service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign)
    browser, _ = signed_in()
    worked(digest, due_of(store, campaign, digest), live=False)
    page = unescape(browser.get(edit_path(digest, send=DAY)).content.decode())
    assert 'name="scope" value="send" id="schedule-delete-send" checked' in page
    review = post(browser, DELETE, skipping(store, digest))
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert saved_values(store, digest)["skip_dates"] == [DAY]


def wednesday(campaign, weeks=2):
    """A Wednesday of the campaign, ``weeks`` after its first."""
    start = campaign.active_configuration.values["start_date"]
    first = date.fromisoformat(str(start))
    first += timedelta(days=(2 - first.weekday()) % 7)
    return (first + timedelta(weeks=weeks)).isoformat()


def cli_change(admin, secret, document):  # noqa: F811
    """Preview, confirm and install one ``schedule`` change; return the preview."""
    code, previewed = preview(admin, secret, document)
    assert code == 0, previewed
    code, confirmed = confirm(admin, secret, previewed["result"]["preview"]["token"])
    assert code == 0, confirmed
    install_request(
        admin.service.store,
        request_id=UUID(confirmed["result"]["request"]["request_id"]),
        correlation_id=uuid4(),
    )
    return previewed


def test_a_weekday_change_drops_a_future_skip_it_moves_off_the_digest(
    admin,  # noqa: F811
    google,
):
    """Wednesday, Thursday, Wednesday again: the skipped Wednesday sends.

    The Thursday change's review lists the dropped skip by its old send
    time, and the digest no longer carries it, so changing back to
    Wednesday leaves that Wednesday a send.
    """
    store = admin.service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign, weekly=True)
    skip = wednesday(campaign)
    _, secret, _ = paired(admin.service)
    cli_change(admin, secret, {"schedules": [{"id": str(digest), "skip": skip}]})
    assert saved_values(store, digest)["skip_dates"] == [skip]
    old_due = due_of(store, campaign, digest, skip)
    # The page's review of moving the digest to Thursday lists the skip it
    # drops, and each side lists the sends it skips, by their send times.
    browser, _ = signed_in()
    review = post(
        browser,
        edit_path(digest),
        entry(
            store,
            zone=PARISH_TIMEZONE,
            time="00:15",
            weekday="3",
            # A converted report sends no email template of its own.
            template_version="",
        ),
    )
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    moment = f'<time datetime="{old_due.isoformat()}" data-local-instant>'
    assert "so they are no longer skipped" in text
    assert text.count(moment) == 2  # Current schedule's skipped send, dropped
    assert "Skipped sends" in text
    # Through the command line, as the page applies it.
    thursday = cli_change(
        admin, secret, {"schedules": [{"id": str(digest), "weekday": 3}]}
    )
    [change] = thursday["result"]["changes"]
    assert change["before"]["skip_dates"] == [skip]
    assert change["after"]["skip_dates"] == []
    # An update merges into the saved values, so the list is left empty.
    assert saved_values(store, digest)["skip_dates"] == []
    cli_change(admin, secret, {"schedules": [{"id": str(digest), "weekday": 2}]})
    assert saved_values(store, digest)["skip_dates"] == []
    assert slot_due(digest, skip) == old_due


def test_a_weekday_change_keeps_a_past_skip(admin, google, monkeypatch):  # noqa: F811
    """A skip whose send has passed stays, so it never comes back as missed."""
    store = admin.service.store
    campaign, _emails = setup(store)
    digest = add_digest(store, campaign, weekly=True)
    skip = wednesday(campaign)
    _, secret, _ = paired(admin.service)
    cli_change(admin, secret, {"schedules": [{"id": str(digest), "skip": skip}]})
    passed = due_of(store, campaign, digest, skip) + timedelta(hours=1)
    monkeypatch.setattr(admin_editing, "database_now", lambda: passed)
    code, document = preview(
        admin, secret, {"schedules": [{"id": str(digest), "weekday": 3}]}
    )
    assert code == 0, document
    assert document["result"]["changes"][0]["after"]["skip_dates"] == [skip]


def test_the_command_line_refuses_a_skip_with_a_moved_send(admin, google):  # noqa: F811
    """``skip`` with ``date``, ``time`` or ``weekday`` is invalid, recording nothing."""
    store = admin.service.store
    campaign, _emails = setup(store)
    digest = str(add_digest(store, campaign, weekly=True))
    _, secret, _ = paired(admin.service)
    before = ConfigurationChangeRequest.objects.count()
    for moved in ({"weekday": 3}, {"time": "01:30:00"}):
        code, document = preview(
            admin,
            secret,
            {"schedules": [{"id": digest, "skip": wednesday(campaign), **moved}]},
        )
        assert code == 1 and document["error"]["code"] == "invalid", document
        assert "cannot be combined" in json.dumps(document), document
    assert ConfigurationChangeRequest.objects.count() == before
