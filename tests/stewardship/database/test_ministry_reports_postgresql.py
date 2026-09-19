"""Actual web-role reporting over submitted Ministry intent and current policy."""

import json
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministries import MinistryQuery, ministry_page

from ..campaign_factory import campaign as campaign_record
from ..census_factory import member
from ..policy_factory import address, assignment
from .auth_builders import signed_in
from .campaign_builders import add_draft, change, close_campaign
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_ministry_responses_postgresql import (
    configure,
    ministry_source,
    respond,
    revisit,
    start,
)
from .test_report_workspace_postgresql import read
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def setup(harness):
    """Create live requests only after selected Ministries survive activation."""
    start(harness)
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    return harness


def page(
    harness, *, roles=("staff",), scope=(), ministry=None, action="join", **filters
):
    """Read through actual restricted SQL grants, not the migration owner."""
    actor = Principal(uuid4(), frozenset(roles), frozenset(scope))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return ministry_page(
            harness.campaign.pk,
            MinistryQuery.parse(filters, detail=ministry is not None),
            actor,
            ministry_id=ministry,
            action=action,
        )


def test_current_intent_history_and_contact_projection(response_service):
    """History never resurrects cancelled requests or bypasses leader publication."""
    harness = setup(response_service)
    summary = page(harness)
    assert [
        (row["duid"], row["joining"], row["leaving"], row["unresolved"])
        for row in summary["summaries"]
    ] == [(4, 0, 1, 1), (9, 1, 0, 1)]
    leader = dict(roles=("ministry_leader",), scope=(9,))
    assert [row["duid"] for row in page(harness, **leader)["summaries"]] == [9]
    with pytest.raises(PermissionError):
        page(harness, ministry=4, **leader)
    with pytest.raises(PermissionError):
        page(harness, ministry=123)
    operational = page(harness, ministry=9)["rows"][0]
    scoped = page(harness, ministry=9, **leader)["rows"][0]
    assert operational["emails"] == ["valid@example.org"]
    assert scoped["emails"] == [] and scoped["email_visibility"] == "not_published"
    assert scoped["phones"] == {"home": "202-555-0123"}
    assert scoped["address_lines"][0] == "1 Example Street"
    assert scoped["age"] >= 60 and scoped["gender"] == "Unspecified"
    assert "1960-01-01" not in str(scoped) and "birthdate" not in scoped
    leaver = page(harness, ministry=4, action="leave")["rows"][0]
    assert leaver["current_role"] == "Chairperson"
    assert leaver["emails"] == [] and leaver["phones"] == {} and leaver["age"] is None
    assert page(harness, ministry=9, search="EXAMPLE")["total"] == 1
    assert page(harness, ministry=9, search="valid@example.org")["total"] == 0
    assert page(harness, ministry=9, page="2")["rows"] == []
    form = revisit(harness)
    respond(harness, form, answers_for(form))
    assert page(harness, ministry=9)["total"] == 1
    history = page(harness, ministry=9, history="all")
    assert history["total"] == 2 and {row["state"] for row in history["rows"]} == {
        "new",
        "superseded",
    }
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [], "leave": []}
    respond(harness, form, answers)
    assert page(harness, ministry=9)["total"] == 0
    assert page(harness, ministry=9, state="unresolved")["total"] == 0
    assert page(harness, ministry=9, history="all", state="cancelled")["total"] == 1
    assert all(row["unresolved"] == 0 for row in page(harness)["summaries"])


def test_native_leader_scope_private_post_audit_and_source_changes(
    response_service, google, monkeypatch
):
    """Real Google policy and source promotion change visibility without copied PII."""
    harness = setup(response_service)
    store = harness.service.store
    rule = address("leader@example.org", roles=("ministry_leader",))
    assigned = assignment(ministry=9)
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "login_rules", **record}
                for record in (rule, assigned)
            ],
        ).state
        == "applied"
    )
    google[0]["email"] = "leader@example.org"
    browser, _ = signed_in()
    root = f"/admin/reports/{harness.campaign.pk}/ministries/"
    route = root + "join/"
    from parishkit.stewardship.reports import ministry_views

    real_principal = ministry_views._principal

    def previously_staff(request, store, *, read_only=False):
        """Model an outer Staff decision superseded by the guarded leader check."""
        actor = real_principal(request, store, read_only=read_only)
        return actor if read_only else Principal(actor.identity, frozenset({"staff"}))

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(ministry_views, "_principal", previously_staff)
        response, body = search(browser, route, {"ministry": "9"})
        assert response.status_code == 200 and b"valid@example.org" not in body
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, root)
        assert (
            response.status_code == 200
            and b"Food pantry" in body
            and b"Choir" not in body
        )
        assert b"Ministry reports" in body and b"Family codes" not in body
        response, body = search(browser, route, {"ministry": "9"})
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert b"Not published" in body and b"valid@example.org" not in body
        assert b"202-555-0123" in body and b"1960-01-01" not in body
        assert search(browser, root + "leave/", {"ministry": "4"})[0].status_code == 403
        assert browser.post(route, {"search": "Private"}).status_code == 403
        response, body = search(browser, route, {"ministry": "9", "search": "Example"})
        assert response.status_code == 200 and b"Member Middle Example" in body
        assert b"?search=" not in body
        response, body = read(browser, route + "?search=Private")
        assert response.status_code == 400 and b"Private" not in body
        assert read(browser, route)[0].status_code == 400
        assert b"/ministries/9/" not in body
        for value in ("0", "09", "2147483648", "private@example.org"):
            assert search(browser, route, {"ministry": value})[0].status_code == 400
    contexts = list(
        AuditContext.objects.filter(
            event__event_type="ministry_report_viewed"
        ).values_list("context", flat=True)
    )
    assert any(context.get("ministry_duid") == 9 for context in contexts)
    assert "Example" not in json.dumps(contexts) and "valid@" not in json.dumps(
        contexts
    )
    with connection.cursor() as cursor:
        for value, valid in (
            (9, True),
            (True, False),
            (0, False),
            (2**31, False),
            ("private@example.org", False),
        ):
            cursor.execute(
                "SELECT stewardship_safe_context_v1('action',%s::jsonb)",
                [json.dumps({"ministry_duid": value})],
            )
            assert cursor.fetchone()[0] is valid
    data = ministry_source()
    data.members[3]["family_PublishEMail"] = True
    data.members[3].pop("family_PublishPhone")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    result = page(harness, roles=("ministry_leader",), scope=(9,), ministry=9)["rows"][
        0
    ]
    assert result["emails"] == ["valid@example.org"] and result["phones"] == {}
    assert result["phone_visibility"] == "not_published"
    data.members[3]["familyDUID"] = 2
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    moved = page(harness, ministry=9)["rows"][0]
    assert moved["member_name"] == "Unavailable Member" and not moved["emails"]
    assert not moved["phones"] and not moved["address_lines"] and moved["age"] is None
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "remove", "section": "login_rules", "id": assigned["id"]},
            ],
        ).state
        == "applied"
    )
    assert search(browser, route, {"ministry": "9"})[0].status_code == 403


def test_testing_hidden_proposed_and_resolved_intent(response_service):
    """Live reporting retains hidden intent and local identities, not rehearsals."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    assert page(harness)["summaries"][1]["joining"] == 0
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    identifier = str(uuid4())
    answers["proposed_members"][identifier] = member(
        first_name="New", last_name="Person", email="proposed@example.org"
    )
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    leader = dict(roles=("ministry_leader",), scope=(9,), ministry=9)
    proposed = next(
        row for row in page(harness, **leader)["rows"] if row["proposed_id"]
    )
    assert proposed["proposed_id"] == identifier and proposed["member_duid"] is None
    assert proposed["member_name"] == "New Person" and not proposed["emails"]
    assert proposed["email_visibility"] == "not_published"
    staff_proposed = next(
        row for row in page(harness, ministry=9)["rows"] if row["proposed_id"]
    )
    assert staff_proposed["emails"] == ["proposed@example.org"]
    store = harness.service.store
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
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
        ).state
        == "applied"
    )
    form = revisit(harness)
    respond(harness, form, answers_for(form))
    hidden = page(harness, activity="inactive")["summaries"]
    assert len(hidden) == 1 and hidden[0]["duid"] == 9 and hidden[0]["joining"] == 2
    assert page(harness, ministry=9, activity="active")["rows"] == []
    assert page(harness, **leader)["total"] == 2
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
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    summary = page(harness, **leader)["summaries"][0]
    assert summary["completed"] == summary["unresolved"] == 1
    assert summary["joining"] == 2 and "50" in summary["progress"]
    resolved = page(harness, **leader, state="resolved")["rows"]
    assert len(resolved) == 1 and resolved[0]["outcome_label"] == "Joined ministry"


def test_detail_pagination_is_complete_stable_and_source_scoped(response_service):
    """More than one page is selected before contact projection, without N+1 reads."""
    harness = response_service
    data = ministry_source()
    for duid in range(100, 151):
        data.members[duid] = data.members[3] | {
            "memberDUID": duid,
            "firstName": "Repeated",
            "memberType": "Other",
        }
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    configure(harness, census=False)
    harness = activate_response_service(harness)
    form = load_form(harness)
    answers = answers_for(form)
    for choices in answers["ministries"]["members"].values():
        choices["join"] = [9]
    respond(harness, form, answers)
    first = page(harness, ministry=9)
    second = page(harness, ministry=9, page="2")
    assert first["total"] == second["total"] == 52
    assert len(first["rows"]) == 50 and len(second["rows"]) == 2
    assert {row["member_duid"] for row in first["rows"] + second["rows"]} == {
        3,
        *range(100, 151),
    }
    assert [row["id"] for row in first["rows"]] == [
        row["id"] for row in page(harness, ministry=9)["rows"]
    ]
    assert first["metadata"]["source_id"] == second["metadata"]["source_id"]
    assert page(harness, ministry=9, page="3")["rows"] == []


def test_campaign_choices_intersect_enabled_modules_and_assignments(
    response_service, google
):
    """A newer unrelated campaign never hides an older authorized Ministry report."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.runtime import return_to_testing
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .campaign_builders import admit_test_work, campaign_clock, command
    from .test_taskrun_postgresql import act

    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    store = harness.service.store
    actor = uuid4()
    close_campaign(harness.campaign, actor)
    for task in TaskRun.objects.filter(state="running"):
        act(_status(task), "permanent_failure")
    with campaign_clock(harness.campaign.active_configuration.ends_at):
        command(harness.campaign, actor, Action.ARCHIVE)
        return_to_testing(
            campaign_id=harness.campaign.pk,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    result, successor, _ = add_draft(
        store,
        store.active(),
        uuid4(),
        campaign_record(
            name="Unassigned campaign",
            modules=["ministry"],
            ministry_duids=[4],
        ),
    )
    assert result.state == "applied"
    rule = address("leader@example.org", roles=("ministry_leader",))
    assigned = assignment(ministry=9)
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "login_rules", **record}
                for record in (rule, assigned)
            ],
        ).state
        == "applied"
    )
    google[0]["email"] = "leader@example.org"
    browser, _ = signed_in()
    owned = f"/admin/reports/{harness.campaign.pk}/ministries/"
    unowned = f"/admin/reports/{successor['id']}/ministries/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get("/admin/ministry-reports/")
        assert response.status_code == 302 and response["Location"] == owned
        response, body = read(browser, "/admin/ministry-reports/campaigns/")
        assert response.status_code == 200 and owned.encode() in body
        assert unowned.encode() not in body and b"Unassigned campaign" not in body
        response, body = read(browser, unowned)
        assert response.status_code == 403 and b"Unassigned campaign" not in body
        assert read(browser, owned)[0].status_code == 200
    google[0]["email"] = "admin@example.org"
    google[0]["sub"] = "synthetic-admin-subject"
    admin, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(admin, "/admin/ministry-reports/campaigns/")
        assert (
            response.status_code == 200
            and owned.encode() in body
            and unowned.encode() in body
        )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": successor["id"],
                    "values": {"modules": ["census"], "ministry_duids": []},
                }
            ],
        ).state
        == "applied"
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert admin.get("/admin/ministry-reports/")["Location"] == owned
        response, body = read(admin, unowned)
        assert response.status_code == 403 and "Retry-After" not in response
        assert b"Unassigned campaign" not in body
