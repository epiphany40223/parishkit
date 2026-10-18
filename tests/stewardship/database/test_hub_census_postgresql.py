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
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.hub_census import read_hub_census
from parishkit.stewardship.responses.models import ProposedChange

from ..census_factory import address, member
from ..policy_factory import address as rule_address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import prepared, resolve
from .test_census_review_postgresql import review
from .test_member_requests_postgresql import revisit, send
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


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
    # Another Family, with no response, has no subjects; only the response
    # is looked for.
    other = FamilyCampaign.objects.exclude(pk=family_id).values_list("pk", flat=True)
    census, statements = hub(harness, other[0])
    assert census.family is None and census.members == () and statements == 1


def test_a_second_response_keeps_each_change_once(live_response_service):
    """A Family answering again: the newest rows, an added Member last."""
    harness = live_response_service
    added = answer_twice(harness)
    assert ProposedChange.objects.filter(execution="superseded").count() == 2
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
    return response, body.decode(), len(queries)


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
    # panel costs what the Family or Timeline panel does. (The session's
    # first load above also records its sign-in activity, so it is not
    # compared.)
    response, _, again = as_web(admin, page)
    assert response.status_code == 200
    assert again == family_statements == member_statements, timeline_statements
    # Each load is one audited view, as before the hub; nothing else.
    assert (
        AuditEvent.objects.filter(event_type="family_timeline_viewed").count()
        == viewed + 4
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
