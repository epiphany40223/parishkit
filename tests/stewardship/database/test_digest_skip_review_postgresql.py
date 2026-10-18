"""Skip one send of an Admin digest: Edit's This email only and ``skip`` (#1658).

Through real Admin sessions, the command line and the configuration
installer: Edit opened for one of a digest's calendar sends offers This email
only, whose review records nothing until Apply; Apply stores the send in the
digest's ``skip_dates``, so the database's slot check answers no slot for
it, and later changes keep it. The refusals (a send already past or with
work, one already skipped, a date that is not a send, a skip of anything but
a digest, a malformed post) record nothing, and Apply refuses once the send
has passed or work for it appeared after the review.
"""

import json
from datetime import timedelta
from html import unescape
from uuid import UUID, uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts import admin_editing, campaign_wizard
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.digest_skips import send_of
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from .auth_builders import signed_in
from .automation_builders import paired
from .campaign_builders import campaign_clock, occurrence
from .test_admin_schedule_cli_postgresql import (  # noqa: F401
    admin,
    confirm,
    one,
    preview,
)
from .test_campaign_views_postgresql import apply, post
from .test_digest_schedule_planning_postgresql import add_digest
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import DELETE, LIST, definition, setup

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
    """When the digest's send at ``slot`` is due (UTC)."""
    campaign.refresh_from_db()
    return send_of(
        saved_values(store, identifier), campaign.active_configuration.values, slot
    ).due_at


def worked(identifier, due):
    """Plan one Admins occurrence of the digest's ``DAY`` send."""
    with campaign_clock(due):
        return occurrence(
            ScheduleDefinition.objects.get(pk=identifier),
            uuid4(),
            "admins",
            slot=DAY,
            due_at=due,
        )


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
    assert "This email only skips just the digest's send at" in page
    # Opened from the list, a digest's Delete is the whole digest alone.
    plain = unescape(browser.get(edit_path(digest)).content.decode())
    assert 'name="scope"' not in plain and 'name="send"' not in plain
    before = ConfigurationChangeRequest.objects.count()
    review = post(browser, DELETE, skipping(store, digest))
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    assert 'data-review-done="Email skipped."' in text
    assert f"the digest skips its send for {DAY}, at" in text
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
    worked_due = due_of(store, campaign, digest, other)
    with campaign_clock(worked_due):
        occurrence(
            ScheduleDefinition.objects.get(pk=digest),
            uuid4(),
            "admins",
            slot=other,
            due_at=worked_due,
        )
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
    assert "skip_dates" not in saved_values(store, digest)


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
