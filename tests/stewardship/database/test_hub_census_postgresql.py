"""The Family hub's census tabs under the real web login (#1678 slice 2).

A live Family answers twice through the real HTTP owner: first it renames
Member 3 (an Automatic change) and gives itself a new home address (By
hand); then it answers again, keeping both and adding a Member. The hub's
read (``hub_census.read_hub_census``) is checked as the restricted web
login, through the same stage mapping as the review worklist, and the page
itself for its Family and Member panels, its two extra statements and its
single audited view.
"""

from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.hub_census import read_hub_census
from parishkit.stewardship.responses.effective import proposal_index
from parishkit.stewardship.responses.models import ProposedChange, Submission

from ..census_factory import address, member
from ..policy_factory import address as rule_address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import prepared, resolve
from .test_census_review_postgresql import review
from .test_member_requests_postgresql import revisit, send
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

# Text only the hub's census statements hold (hub_census.SUBMISSION,
# PROPOSALS and ROSTER), to count them among a page load's statements.
CENSUS_SQL = (
    "'proposed_members', s.answers->'proposed_members'",
    "FROM stewardship_proposed_change p\nJOIN stewardship_submission s",
    "FROM stewardship_source_current c\nJOIN stewardship_family_campaign f",
)


def answer_twice(harness):
    """Rename Member 3 and move house, then answer again adding a Member."""
    added = str(uuid4())
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["family"]["home_address"] = address(line1="9 Census Way")
        send(harness, form, answers)
        form = revisit(harness)
        answers = answers_for(form)
        answers["proposed_members"][added] = member(first_name="Zed")
        send(harness, form, answers)
    return added


def hub(harness, family_id):
    """Read the hub's census tabs as the restricted web login."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        CaptureQueriesContext(connection) as queries,
    ):
        census = read_hub_census(harness.campaign.pk, family_id, census=True)
    return census, len(queries)


def test_fields_and_stages_follow_the_change_rows_in_force(response_service):
    """The response's rows, staged as the worklist stages them, as they move."""
    harness, admin, staff, home, name = prepared(response_service)
    family_id = home.submission.family_id
    census, statements = hub(harness, family_id)
    assert statements == 2
    (row,) = census.family.changed
    assert row.label == "Home address" and "9 Census Way" in row.answer
    assert row.stage_label == "To review" and row.baseline_read is False
    assert {item.label for item in census.family.unchanged} >= {
        "Opt out of all parish emails"
    }
    renamed = next(item for item in census.members if item.changed)
    (first,) = renamed.changed
    assert (first.label, first.answer, first.stage_label) == (
        "First name",
        "Requested",
        "To review",
    )
    assert first.baseline and first.baseline != "Requested"
    assert renamed.label.endswith(", Requested")
    # Accepting the rename and entering the address by hand move their
    # stages, through the real review and resolution services.
    review(harness, admin, name, "approved")
    resolve(harness, staff, home, "entered")
    census, _ = hub(harness, family_id)
    assert census.family.changed[0].stage_label == "Entered by hand"
    renamed = next(item for item in census.members if item.changed)
    assert renamed.changed[0].stage_label == "Ready to update"
    assert renamed.changed[0].placement.decision.label == "Accepted"
    # Another Family, with no response and no ParishSoft Member, has no
    # subjects; the response and its current Members are looked for.
    other = FamilyCampaign.objects.exclude(pk=family_id).values_list("pk", flat=True)
    census, statements = hub(harness, other[0])
    assert census.family is None and census.members == () and statements == 2


def test_before_a_response_each_member_has_a_marked_tab(
    live_response_service, auth_service, google
):
    """R39 finding 8: before the Family answers, each of its active, living
    Members in the current ParishSoft data has a tab marked No submission
    yet, named from ParishSoft, read as the restricted web login; an
    inactive or deceased Member has none. The page reads two census
    statements (the response, then the Members) on every tab."""
    harness = live_response_service
    data = response_source()
    base = data.members[3]
    data.members[4] = {**base, "memberDUID": 4, "firstName": "Second"}
    data.members[5] = {**base, "memberDUID": 5, "memberStatus": "Inactive"}
    data.members[6] = {**base, "memberDUID": 6, "memberStatus": "Deceased"}
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    family_id = FamilyCampaign.objects.get(family_duid=1).pk
    census, statements = hub(harness, family_id)
    assert census.family is None and statements == 2
    assert [
        (item.key, item.label, item.mark, item.duid, item.rows)
        for item in census.members
    ] == [
        ("member-1", "Example, Member", "unsubmitted", 3, ()),
        ("member-2", "Example, Second", "unsubmitted", 4, ()),
    ]
    page = reverse("admin:timeline", args=[family_id])
    admin, login = signed_in()
    assert login.status_code == 302
    response, body, timeline_statements = as_web(admin, page)
    assert response.status_code == 200
    assert 'data-tab="member-2"' in body and 'data-tab="member-3"' not in body
    response, body, member_statements = as_web(admin, page + "?tab=member-1")
    assert response.status_code == 200
    panel = body.split('id="family-tabs-panel-member-1"')[1]
    assert '<span class="status-badge">No submission yet</span>' in panel
    assert "Open Census changes" not in panel
    assert timeline_statements == member_statements == 2


def test_a_second_response_keeps_each_change_once(live_response_service):
    """A Family answering again: the changes in force, an added Member last.

    The second answer left the first one's changes as its reviewed form
    showed them (#1705), so they stay in force on the first response and no
    row is replaced; the hub still shows each once."""
    harness = live_response_service
    added = answer_twice(harness)
    assert ProposedChange.objects.filter(execution="superseded").count() == 0
    family_id = ProposedChange.objects.values_list(
        "submission__family_id", flat=True
    ).first()
    census, _ = hub(harness, family_id)
    assert len(census.family.changed) == 1
    assert sum(len(item.changed) for item in census.members if item.mark != "new") == 1
    (new,) = [item for item in census.members if item.mark == "new"]
    assert new.key == f"member-{len(census.members)}"
    assert new.addition.stage_label == "To review"
    assert ("First name", "Zed") in [(row.label, row.answer) for row in new.rows]
    assert ProposedChange.objects.get(field="new_member").entity_key == added


def as_web(client, path):
    """GET ``path`` as the restricted web login, through the real read guard."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as queries,
    ):
        response, body = get(client, path)
    # The hub's census statements, told apart by their own SQL text: the
    # rest of the page (sessions, guards, the timeline) is slice 1's.
    census = [
        query["sql"]
        for query in queries.captured_queries
        if any(text in query["sql"] for text in CENSUS_SQL)
    ]
    return response, body.decode(), len(census)


def test_the_hub_draws_each_census_panel_with_no_new_audit(
    live_response_service, auth_service, google
):
    """Family and Member panels; the same statements whichever tab is chosen."""
    harness = live_response_service
    answer_twice(harness)
    family_id = ProposedChange.objects.values_list(
        "submission__family_id", flat=True
    ).first()
    page = reverse("admin:timeline", args=[family_id])
    admin, login = signed_in()
    assert login.status_code == 302
    viewed = AuditEvent.objects.filter(event_type="family_timeline_viewed").count()
    response, body, timeline_statements = as_web(admin, page)
    assert response.status_code == 200
    assert 'data-tab="family"' in body and 'data-tab="member-1"' in body
    response, body, family_statements = as_web(admin, page + "?tab=family")
    assert response.status_code == 200
    panel = body.split('id="family-tabs-panel-family"')[1]
    assert "9 Census Way" in panel and "To review" in panel
    assert "Show unchanged" in panel
    response, body, member_statements = as_web(admin, page + "?tab=member-1")
    assert response.status_code == 200 and 'id="hub-member-1-changed-caption"' in body
    # The census read is the same two statements on every tab, so a Member
    # panel costs what the Family or Timeline panel does.
    assert timeline_statements == family_statements == member_statements == 2
    # Each load is one audited view, as before the hub; nothing else.
    assert (
        AuditEvent.objects.filter(event_type="family_timeline_viewed").count()
        == viewed + 3
    )
    # Staff see the same tabs.
    store = auth_service.store
    rule = rule_address("staff@example.org", roles=("staff",))
    applied = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    assert applied.state == "applied"
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    staff, _ = signed_in()
    response, body, _ = as_web(staff, page + "?tab=family")
    assert response.status_code == 200 and "9 Census Way" in body


def test_a_kept_request_names_a_member_parishsoft_no_longer_lists(
    live_response_service,
):
    """Member 3's Deceased request survives ParishSoft moving the Member out
    and a later response; the hub's rows equal ``proposal_index``'s."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"] = {
            "deceased_status": True,
            "confirmed": True,
            "death_date": "2026-01-01",
        }
        send(harness, form, answers)
    data = response_source()
    # Another active Member keeps the household able to sign in.
    data.members[4] = {**data.members[3], "memberDUID": 4, "firstName": "Survivor"}
    data.members[3]["familyDUID"] = 2
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    with web_login():
        form = revisit(harness)
        send(harness, form, answers_for(form))
    latest = Submission.objects.order_by("-family_version").first()
    census, _ = hub(harness, latest.family_id)
    kept = [item for item in census.members if item.mark == "missing"]
    assert [item.label for item in census.members] == [
        census.members[0].label,
        kept[0].label,
    ]
    assert census.members[0].mark == "" and "Survivor" in census.members[0].label
    # Named from the ParishSoft record the Family first answered from.
    assert kept[0].label != "Name not recorded in ParishSoft"
    assert [row.label for row in kept[0].rows] == ["Deceased", "Death date"]
    # The SQL restatement selects exactly the rows proposal_index does.
    with work_transaction():
        expected = {
            (kind, "" if kind == "family" else key, field): row.execution
            for (kind, key, field), row in proposal_index(latest).items()
            if row.execution not in {"superseded", "cancelled"}
        }
    shown = {
        ("member", "3", "deceased_status"),
        ("member", "3", "death_date"),
    }
    assert shown <= set(expected)
    assert {key for key in expected if key[0] == "member"} == shown
