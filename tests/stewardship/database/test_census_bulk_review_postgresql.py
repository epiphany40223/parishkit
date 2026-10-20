"""An Administrator reviews several Automatic census changes at once (#528).

Bulk review adds no schema: each change is reviewed exactly as one is, so
0096's guard and effect triggers check every review row on its own. These
tests run the service on the restricted web login and the worklist's bulk
dialog post over HTTP.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_review import (
    MAX_BULK,
    BulkStale,
    bulk_key_for,
    review_census_changes,
)
from parishkit.stewardship.responses.models import ProposalReview, ProposedChange

from ..census_factory import address
from ..policy_factory import address as rule
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import leader
from .test_census_review_postgresql import fresh, review
from .test_ministry_responses_postgresql import respond, start
from .test_policy_postgresql import user
from .test_publication_plans_postgresql import owner
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def prepared(harness):
    """A live response with two Automatic rows (Member 3's first and last
    name) and a By-hand row (the home address), an Administrator and Staff."""
    start(harness)
    harness = activate_response_service(harness)
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["members"]["3"]["last_name"] = "Surname"
        answers["family"]["home_address"] = address(line1="9 Census Way")
        respond(harness, form, answers)
    admin = user("admin@example.org")
    store = harness.service.store
    change(
        store,
        store.active(),
        admin.pk,
        [
            {"operation": "add", "section": "login_rules", **rule(*item)}
            for item in (("staff@example.org", ("staff",)),)
        ],
    )
    rows = {row.field: row for row in ProposedChange.objects.all()}
    return (
        harness,
        admin.pk,
        user("staff@example.org").pk,
        rows["home_address"],
        [rows["first_name"], rows["last_name"]],
    )


def bulk(harness, actor, proposals, decision, *, key=None, note="", versions=None):
    """Review ``proposals`` at once through the service on the web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return review_census_changes(
            harness.service.store,
            actor,
            versions or {row.pk: row.version for row in proposals},
            decision=decision,
            note=note,
            bulk_key=key or uuid4(),
        )


def test_bulk_review_records_each_change_as_one_review(response_service):
    """Each chosen change gets its own review row, version and audit event,
    with the derived request key; a repeated post replays and records
    nothing more."""
    harness, admin, _, _, names = prepared(response_service)
    key = uuid4()
    reviews = bulk(harness, admin, names, "approved", key=key, note="Checked")
    assert [row.proposal_id for row in reviews] == sorted(row.pk for row in names)
    assert {row.request_key for row in reviews} == {
        bulk_key_for(key, row.pk) for row in names
    }
    assert all(row.note == "Checked" and row.actor_id == admin for row in reviews)
    for row in names:
        stored = fresh(row)
        assert (stored.decision, stored.version) == ("approved", row.version + 1)
    events = AuditEvent.objects.filter(event_type="census_change_updated")
    assert {event.subject_id for event in events} == {row.pk for row in reviews}
    again = bulk(harness, admin, names, "approved", key=key, note="Checked")
    assert [row.pk for row in again] == [row.pk for row in reviews]
    assert ProposalReview.objects.count() == 2


def test_bulk_review_is_all_or_none(response_service):
    """A stale change, a change that cannot take the decision, a Ministry
    leader and an over-long batch each refuse the whole request. Staff review
    in bulk too, By hand changes included (#1683)."""
    harness, admin, staff, home, names = prepared(response_service)
    first, second = names
    review(harness, admin, second, "approved")
    with pytest.raises(BulkStale) as stale:
        bulk(harness, admin, names, "ignored")
    assert stale.value.proposal_id == second.pk
    # The second is approved now: approving both cannot apply to it.
    with pytest.raises(ValueError):
        bulk(harness, admin, [first, fresh(second)], "approved")
    with pytest.raises(PermissionError):
        bulk(harness, leader(harness, admin), [first], "approved")
    with pytest.raises(ValueError):
        bulk(
            harness,
            admin,
            [],
            "approved",
            versions={uuid4(): 1 for _ in range(MAX_BULK + 1)},
        )
    with pytest.raises(ProposedChange.DoesNotExist):
        bulk(harness, admin, [], "approved", versions={first.pk: 1, uuid4(): 1})
    assert ProposalReview.objects.count() == 1
    assert fresh(first).decision == "unreviewed"
    bulk(harness, staff, [first, home], "approved")
    assert {fresh(first).decision, fresh(home).decision} == {"approved"}


def test_bulk_review_refuses_a_queued_change(response_service):
    """Review note 5: a change queued for publication is never reviewed,
    alone or in a batch."""
    harness, admin, _, _, names = prepared(response_service)
    owner(
        "UPDATE stewardship_proposed_change SET execution='queued' WHERE id=%s",
        [names[1].pk],
    )
    with pytest.raises(ValueError):
        bulk(harness, admin, [names[0], fresh(names[1])], "ignored")
    assert ProposalReview.objects.count() == 0
