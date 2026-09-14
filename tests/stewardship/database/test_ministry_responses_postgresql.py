"""Real final Ministry answers, exact web authority and retained request history."""

from contextlib import nullcontext
from uuid import uuid4

import pytest
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.models import ProposedChange, Submission
from parishkit.stewardship.workflows.models import MinistryRequest

from ..census_factory import member
from .campaign_builders import change as change_configuration
from .response_builders import response_source
from .test_family_auth_postgresql import login
from .test_response_http_postgresql import answers_for, load_form, post
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def ministry_source():
    """One current Ministry and one offered join, with deterministic labels."""
    data = response_source()
    data.ministry_types[9] = {"id": 9, "name": "Food pantry"}
    data.ministry_type_memberships[9] = {"membership": []}
    return data


def configure(harness, *, census=True, selected=(4, 9), patches=()):
    """Use actual journaled configuration application, never direct projections."""
    store = harness.service.store
    result = change_configuration(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(harness.campaign.pk),
                "values": {
                    "modules": ["census", "ministry"] if census else ["ministry"],
                    "ministry_duids": list(selected),
                },
            },
            *patches,
        ],
    )
    assert result.state == "applied"
    harness.campaign.refresh_from_db()


def start(harness, *, census=True):
    """Promote complete Ministry inputs, then admit a newly issued exact form."""
    snapshot, claim = prepare(ministry_source())
    harness.snapshot = promote(snapshot, claim, harness.campaign, harness.rings)
    configure(harness, census=census)
    return load_form(harness)


def respond(harness, form, answers):
    """A real CSRF-protected definitive submission ends its Family session."""
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 200, result.content
    assert result.json()["accepted"]
    return Submission.objects.order_by("-family_version").first()


def revisit(harness):
    """Use the still-valid campaign credential for a new authenticated session."""
    harness.client, result = login(harness.code)
    assert result.status_code == 302
    harness.request = result.wsgi_request
    return load_form(harness)


@pytest.mark.parametrize("census", [True, False])
@pytest.mark.parametrize("restricted", [False, True])
def test_ministry_complete_submission_and_same_intent_revisit(
    response_service, census, restricted
):
    """Both enabled combinations preserve requests without mutating census/roster."""
    harness = response_service
    start(harness, census=census)
    with web_login() if restricted else nullcontext():
        form = load_form(harness)
        assert form["ministries"]["members"]["3"] == {
            "current": [4],
            "join": [],
            "leave": [],
        }
        if not census:
            assert form["household"] is None and form["members"][0]["fields"] == []
            assert form["members"][0]["display_name"] == "Member Example"
            assert form["new_member_fields"] == []
        answers = answers_for(form)
        answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
        first = respond(harness, form, answers)
        assert not ProposedChange.objects.exists()
        old = list(MinistryRequest.objects.filter(submission=first))
        assert {(row.ministry_duid, row.action, row.state) for row in old} == {
            (9, "join", "new"),
            (4, "leave", "new"),
        }
        form = revisit(harness)
        assert form["ministries"]["members"]["3"] == {
            "current": [4],
            "join": [9],
            "leave": [4],
        }
        second = respond(harness, form, answers_for(form))
        for row in old:
            row.refresh_from_db()
            assert row.state == "superseded"
            assert row.superseded_by.submission_id == second.pk
        form = revisit(harness)
        answers = answers_for(form)
        answers["ministries"]["members"]["3"] = {"join": [], "leave": []}
        respond(harness, form, answers)
        assert set(
            MinistryRequest.objects.filter(submission=second).values_list(
                "state", flat=True
            )
        ) == {"cancelled"}


def test_hidden_request_survives_omission_and_returns_on_reactivation(response_service):
    """Local activity hiding is not a Family withdrawal or a roster change."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    record_id = str(uuid4())
    configure(
        harness,
        patches=[
            {
                "operation": "add",
                "section": "ministries",
                "id": record_id,
                "values": {
                    "organization_id": 12345,
                    "ministry_duid": 9,
                    "active": False,
                },
            }
        ],
    )
    form = revisit(harness)
    assert form["ministries"]["options"] == [{"id": 4, "name": "Choir"}]
    respond(harness, form, answers_for(form))
    assert MinistryRequest.objects.get().state == "new"
    configure(
        harness,
        patches=[
            {
                "operation": "update",
                "section": "ministries",
                "id": record_id,
                "values": {"active": True},
            }
        ],
    )
    form = revisit(harness)
    assert form["ministries"]["members"]["3"]["join"] == [9]


@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("action", ["join", "leave"])
def test_source_roster_catchup_resolves_without_provider_write(
    response_service, restricted, action
):
    """Current promoted membership provides owned resolution proof and a pin."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"][action] = [9 if action == "join" else 4]
    respond(harness, form, answers)
    data = ministry_source()
    data.ministry_type_memberships[9]["membership"] = [
        {
            "memberId": 3,
            "ministryRoleId": 1,
            "ministryRoleName": "Participant",
            "startDate": "2026-01-01",
            "endDate": None,
        }
    ]
    if action == "leave":
        data.ministry_type_memberships[4]["membership"] = []
        data.ministry_type_memberships[9]["membership"] = []
    snapshot, claim = prepare(data)
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    with task_login(ServiceRole.WORKER, exact=True) if restricted else nullcontext():
        snapshot = promote(snapshot, claim, harness.campaign, harness.rings)
    row = MinistryRequest.objects.get()
    assert row.state == "resolved"
    assert row.outcome == ("joined" if action == "join" else "leave_confirmed")
    assert row.resolution_source_id == snapshot.pk and row.resolved_at is not None
    from parishkit.stewardship.source.models import SourceSnapshotPin

    assert SourceSnapshotPin.objects.filter(
        snapshot=snapshot,
        parent_kind="submission",
        parent_id=row.submission_id,
        expires_at__isnull=True,
    ).exists()
    form = revisit(harness)
    assert form["ministries"]["members"]["3"] == {
        "current": [4, 9] if action == "join" else [],
        "join": [],
        "leave": [],
    }


def test_proposed_member_ministry_choices_keep_local_identity(response_service):
    """A proposed UUID can join but cannot invent or write a source Member DUID."""
    harness = response_service
    form = start(harness)
    identifier = str(uuid4())
    answers = answers_for(form)
    answers["proposed_members"][identifier] = member(
        first_name="New", last_name="Person"
    )
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    with web_login():
        respond(harness, form, answers)
        row = MinistryRequest.objects.get()
        assert row.entity_kind == "proposed_member" and row.entity_key == identifier
        assert ProposedChange.objects.get().field == "new_member"
        form = revisit(harness)
        assert form["ministries"]["proposed_members"][identifier] == {
            "current": [],
            "join": [9],
        }
        answers = answers_for(form)
        answers["proposed_members"] = {}
        answers["ministries"]["proposed_members"] = {}
        respond(harness, form, answers)
        row.refresh_from_db()
        assert row.state == "cancelled"


def test_resolved_proposed_member_omission_preserves_ministry_intent(response_service):
    """Completed Member work disappears from the form, not its Ministry queue."""
    harness = response_service
    form = start(harness)
    identifier = str(uuid4())
    answers = answers_for(form)
    answers["proposed_members"][identifier] = member()
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    respond(harness, form, answers)
    # Model an owning Staff workflow's completed manual association. That UI is
    # a later increment; use the same guarded privileged transition as sibling
    # terminal-outcome tests, never weaken runtime web/worker permissions.
    with work_transaction():
        ProposedChange.objects.filter(field="new_member").update(
            execution="resolved_external", version=F("version") + 1
        )
    with web_login():
        form = revisit(harness)
        assert form["proposed_members"] == []
        assert form["ministries"]["proposed_members"] == {}
        respond(harness, form, answers_for(form))
    assert MinistryRequest.objects.get().state == "new"
    assert ProposedChange.objects.get().execution == "resolved_external"


def test_testing_module_toggle_preserves_last_census_response(response_service):
    """Intervening Ministry-only answers cannot erase or reset census decisions."""
    harness = response_service
    form = start(harness)
    identifier = str(uuid4())
    answers = answers_for(form)
    answers["members"]["3"]["email"] = "updated@example.test"
    answers["members"]["3"]["birth_date"] = "unknown"
    answers["proposed_members"][identifier] = member()
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    first = respond(harness, form, answers)
    with work_transaction():
        ProposedChange.objects.filter(field="email").update(
            decision="ignored", version=F("version") + 1
        )
    configure(harness, census=False)
    with web_login():
        form = revisit(harness)
        assert form["proposed_members"] == []
        middle = respond(harness, form, answers_for(form))
    assert not ProposedChange.objects.filter(submission=middle).exists()
    assert MinistryRequest.objects.get().state == "new"
    configure(harness)
    with web_login():
        form = revisit(harness)
        restored = answers_for(form)
        assert restored["members"]["3"]["email"] == "updated@example.test"
        assert restored["members"]["3"]["birth_date"] == "unknown"
        assert identifier in restored["proposed_members"]
        assert restored["ministries"]["proposed_members"][identifier] == {"join": [9]}
        last = respond(harness, form, restored)
    assert (
        ProposedChange.objects.get(submission=last, field="email").decision == "ignored"
    )
    for row in ProposedChange.objects.filter(submission=first):
        assert row.execution == "superseded"
        assert row.superseded_by.submission_id == last.pk
    assert MinistryRequest.objects.get(submission=first).state == "superseded"

    # A subsequent complete census response really does withdraw the edit and
    # proposed UUID. Another module gap must not resurrect that older history.
    with web_login():
        form = revisit(harness)
        withdrawn = answers_for(form)
        withdrawn["members"]["3"]["email"] = ""
        withdrawn["proposed_members"] = {}
        withdrawn["ministries"]["proposed_members"] = {}
        respond(harness, form, withdrawn)
    configure(harness, census=False)
    form = revisit(harness)
    respond(harness, form, answers_for(form))
    configure(harness)
    with web_login():
        form = revisit(harness)
        assert form["proposed_members"] == []
        assert answers_for(form)["members"]["3"]["email"] == ""


def test_terminal_member_omits_ministry_controls_and_withdraws_visible_work(
    response_service,
):
    """Confirmed household status replaces prior visible choices, not source rosters."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    form = revisit(harness)
    answers = answers_for(form)
    answers["members"]["3"] = {"moved_household": True, "confirmed": True}
    answers["ministries"]["members"] = {}
    with web_login():
        respond(harness, form, answers)
        assert MinistryRequest.objects.get().state == "cancelled"
        form = revisit(harness)
        assert form["members"][0]["request"]["moved_household"]
        # The response omits terminal selections, but retaining current public
        # membership permits undoing the terminal choice before another Submit.
        assert form["ministries"]["members"]["3"]["current"] == [4]
        assert answers_for(form)["ministries"]["members"] == {}


def test_activity_change_during_form_requires_review_before_submission(
    response_service,
):
    """A valid but stale edit cannot bypass a newly applied inactive policy."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    configure(
        harness,
        patches=[
            {
                "operation": "add",
                "section": "ministries",
                "id": str(uuid4()),
                "values": {
                    "organization_id": 12345,
                    "ministry_duid": 9,
                    "active": False,
                },
            }
        ],
    )
    with web_login():
        result = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        )
    assert result.status_code == 409 and result.json()["error"] == "review_required"
    assert result.json()["form"]["ministries"]["options"] == [
        {"id": 4, "name": "Choir"}
    ]
    assert not Submission.objects.exists() and not MinistryRequest.objects.exists()


@pytest.mark.parametrize(
    "scope", ["missing_member", "foreign_family", "inactive_member", "absent_ministry"]
)
def test_scope_loss_cannot_prove_leave_completion(response_service, scope):
    """An absent/foreign record is unavailable, never proof of a requested leave."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["leave"] = [4]
    respond(harness, form, answers)
    data = ministry_source()
    data.ministry_type_memberships[4]["membership"] = []
    if scope == "missing_member":
        del data.members[3]
    elif scope == "foreign_family":
        data.members[3]["familyDUID"] = 2
    elif scope == "inactive_member":
        data.members[3]["memberStatus"] = "Inactive"
    else:
        del data.ministry_types[4]
        del data.ministry_type_memberships[4]
    snapshot, claim = prepare(data)
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    row = MinistryRequest.objects.get()
    assert row.state == "new" and row.resolution_source_id is None


def test_rehearsal_cleanup_removes_request_chains_and_source_proof(response_service):
    """Only explicit invalidated-epoch cleanup deletes test Ministry history."""
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
    )

    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    first = respond(harness, form, answers)
    form = revisit(harness)
    respond(harness, form, answers_for(form))
    epoch = invalidate_rehearsal(
        campaign_id=harness.campaign.pk, admit=lambda *args: True
    )
    assert epoch == first.rehearsal_epoch_id
    for _ in range(100):
        if not cleanup_rehearsal(epoch, batch_size=1):
            break
    else:
        pytest.fail("Bounded Ministry rehearsal cleanup did not complete")
    assert not MinistryRequest.objects.exists() and not Submission.objects.exists()


def test_new_rehearsal_namespace_does_not_prefill_or_cancel_old_requests(
    response_service,
):
    """Retained prior-epoch rows cannot become live choices in a new test epoch."""
    from .test_member_request_authority_postgresql import restart_rehearsal

    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    first = respond(harness, form, answers)
    restart_rehearsal(harness)
    with web_login():
        form = revisit(harness)
        assert form["ministries"]["members"]["3"]["join"] == []
        second = respond(harness, form, answers_for(form))
    assert second.rehearsal_epoch_id != first.rehearsal_epoch_id
    assert second.prior_submission_id is None
    old = MinistryRequest.objects.get()
    assert old.submission_id == first.pk and old.state == "new"
