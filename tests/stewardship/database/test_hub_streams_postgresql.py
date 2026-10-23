"""The Family hub's Ministry, Financial and last tabs under the real web login.

Slices 3 and 4 of #1678. Each stream's read (``hub_streams``) runs as the
restricted web login, and its stages are compared with the stream's own
mapping applied to the rows the real services store, as the review pages
read them. The page itself is fetched for each tab, so a tab's statements
are counted where they run, only while it is chosen, under the one audited
view the page always had.
"""

from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.accounts.leader_import import ministry_names
from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.reports.hub_census import read_hub_census
from parishkit.stewardship.reports.hub_streams import (
    read_hub_financial,
    read_hub_other,
    read_member_ministry,
)
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import AdditionalInformationItem

from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_ministry_responses_postgresql import ministry_source, start
from .test_pipeline_postgresql import ministry
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import load_form
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import form_and_answers, submit
from .test_roster_entries_postgresql import resolved, tick
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

# Text only each stream's own statements hold, to count them among a page
# load's statements.
STREAM_SQL = {
    "ministry": "FROM stewardship_ministry_request r",
    "financial": "stewardship_financial_report_v1",
    "other": "FROM stewardship_additional_information a",
}


def as_web(read, *args, **kwargs):
    """Run one hub read as the restricted web login; its result and count."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        CaptureQueriesContext(connection) as queries,
    ):
        result = read(*args, **kwargs)
    return result, len(queries)


def page(client, path):
    """GET ``path`` as the web login: the body and each stream's statements."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as queries,
    ):
        response, body = get(client, path)
    assert response.status_code == 200
    counts = {
        name: sum(text in query["sql"] for query in queries.captured_queries)
        for name, text in STREAM_SQL.items()
    }
    return body.decode(), counts


def viewed():
    """How many hub views the audit holds."""
    return AuditEvent.objects.filter(event_type="family_timeline_viewed").count()


def test_member_ministry_rows_follow_the_ministry_mapping(response_service, google):
    """Each request in force, staged as Ministry requests stages it."""
    harness, admin, join, leave = resolved(response_service)
    family_id = join.submission.family_id
    rows, statements = as_web(
        read_member_ministry, harness.campaign.pk, family_id, ("member", "3")
    )
    # The requests, then the current catalog's names (two statements).
    assert statements == 3
    names = ministry_names([9, 4])
    shown = {row.label: row for row in rows}
    assert set(shown) == {names[9], names[4]}
    assert shown[names[9]].placement == ministry(join)
    assert shown[names[9]].stage_label == "To enter by hand"
    assert shown[names[9]].note == "Joined the Ministry"
    assert (shown[names[4]].answer, shown[names[4]].baseline) == (
        "Leave",
        "In this Ministry",
    )
    assert shown[names[4]].placement == ministry(leave)
    # The tick moves the join to Entered by hand, as Roster changes to
    # enter reads it.
    tick(harness, admin, join, True)
    rows, _ = as_web(
        read_member_ministry, harness.campaign.pk, family_id, ("member", "3")
    )
    (entered,) = [row for row in rows if row.label == names[9]]
    assert entered.placement == ministry(join)
    assert entered.placement.stage is Stage.ENTERED_BY_HAND
    # A Member with no request: one statement, no row.
    rows, statements = as_web(
        read_member_ministry, harness.campaign.pk, family_id, ("member", "99")
    )
    assert rows == () and statements == 1

    # The page: only the chosen Member tab reads the requests.
    census, _ = as_web(read_hub_census, harness.campaign.pk, family_id, census=False)
    (member,) = [item for item in census.members if item.entity == ("member", "3")]
    path = reverse("admin:timeline", args=[family_id])
    client, login = signed_in()
    assert login.status_code == 302
    before = viewed()
    body, counts = page(client, path)
    assert counts == {"ministry": 0, "financial": 0, "other": 0}
    assert 'aria-disabled="true"' not in body.split('role="tablist"')[1]
    body, counts = page(client, f"{path}?tab={member.key}")
    # The tab's own read, then Ministry requests' own reads of the requests
    # (two statements), which give them their row actions (#2064).
    assert counts["ministry"] == 3
    drawn = body.split(f'id="family-tabs-panel-{member.key}"')[1]
    assert names[9] in drawn and "Entered by hand" in drawn
    assert f'id="hub-{member.key}-ministry-caption"' in drawn
    # Each request offers Ministry requests' own row actions, posting there.
    assert f'id="hub-{member.key}-ministry-decide-dialog"' in drawn
    assert f'action="{reverse("admin:ministry_report")}"' in drawn
    assert 'data-row-action="back_to_review"' in drawn
    assert viewed() == before + 2

    # ParishSoft moves Member 3 to another Family: Ministry requests leaves
    # the requests out by default (#1217), so the hub keeps each stage and
    # says where they are listed.
    data = ministry_source()
    data.members[3]["familyDUID"] = 2
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    rows, _ = as_web(
        read_member_ministry, harness.campaign.pk, family_id, ("member", "3")
    )
    assert {row.label: row.placement for row in rows} == {
        names[9]: ministry(join),
        names[4]: ministry(leave),
    }
    for row in rows:
        assert row.note.startswith("Not in ParishSoft: Ministry requests lists")
        assert "Members no longer in ParishSoft" in row.note


def test_a_member_of_a_family_with_no_response_reads_no_ministry(
    response_service, google
):
    """R39 finding 8, Ministry stewardship without census: before the Family
    answers, its Member's tab shows the No submission yet mark and nothing
    else, and choosing it reads no Ministry request."""
    start(response_service, census=False)
    activate_response_service(response_service)
    family_id = FamilyCampaign.objects.get(family_duid=1).pk
    path = reverse("admin:timeline", args=[family_id])
    client, login = signed_in()
    assert login.status_code == 302
    body, counts = page(client, f"{path}?tab=member-1")
    assert counts == {"ministry": 0, "financial": 0, "other": 0}
    drawn = body.split('id="family-tabs-panel-member-1"')[1]
    assert '<span class="status-badge">No submission yet</span>' in drawn
    assert "Ministry joins and leaves" not in drawn
    assert "Open Ministry requests" not in drawn


def test_the_financial_tab_reads_the_report_row(response_service, google):
    """The Family's own row of Financial stewardship detail, money proven."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        response = pledge(
            harness, load_form(harness), shares={CHECK: "", OTHER: "Stock gift"}
        )
    family = FamilyCampaign.objects.get(pk=response.family_id)
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )
    tab, statements = as_web(
        read_hub_financial, campaign, family.family_duid, parish_name="Sample Parish"
    )
    # The proof's two reads, the selection and the configurations answered.
    assert statements == 4
    assert tab.available and tab.pledge.addition.stage_label == "To review"
    rows = {row.label.split(" (")[0]: row for row in tab.pledge.rows}
    assert (rows["Annual pledge"].baseline, rows["Annual pledge"].answer) == (
        "$1,200.00",
        "$1,234.50",
    )
    assert rows["Frequency"].answer == "Monthly ($102.88 each)"
    assert "Other: Stock gift" in rows["Share methods"].answer
    assert not rows["Share methods"].baseline_read
    assert tab.contributions.display == "$100.00"
    # Another Family, with no response: no pledge, and no wording read.
    other = FamilyCampaign.objects.filter(campaign=campaign).exclude(pk=family.pk)
    tab, statements = as_web(
        read_hub_financial,
        campaign,
        other.first().family_duid,
        parish_name="Sample Parish",
    )
    assert tab.available and tab.pledge is None and statements == 3

    path = reverse("admin:timeline", args=[family.pk])
    client, login = signed_in()
    assert login.status_code == 302
    before = viewed()
    body, counts = page(client, path)
    assert counts["financial"] == 0
    body, counts = page(client, path + "?tab=financial")
    # The tab's own selection, then Pledges' own, which gives the pledge its
    # row actions (#2064).
    assert counts == {"ministry": 0, "financial": 2, "other": 0}
    drawn = body.split('id="family-tabs-panel-financial"')[1]
    assert "$1,234.50" in drawn and "To review" in drawn
    # The pledge's table offers Pledges' own row actions, posting there
    # (#2064).
    assert 'data-row-action="accept"' in drawn
    assert 'id="hub-financial-pledges-decide-dialog"' in drawn
    assert f'action="{reverse("admin:pledges_to_enter")}"' in drawn
    assert "ParishSoft contributions for" in drawn
    assert viewed() == before + 2


def answer(harness, form, answers, **values):
    """Submit one live response with these last-tab answers."""
    answers.update(values)
    return submit(harness, form, answers).submission


def test_the_last_tab_follows_the_answers_and_the_follow_up(
    live_response_service, google
):
    """Cannot attend and the current item, staged as their pages stage them."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    response = answer(
        harness,
        form,
        answers,
        cannot_attend=True,
        additional_information="Please call about the choir",
    )
    family_id = response.family_id
    information = bool(
        harness.campaign.active_configuration.values.get("additional_information")
    )
    assert information
    read = (read_hub_other, harness.campaign.pk, family_id, ())
    tab, statements = as_web(*read, ministry=False, information=True)
    assert statements == 1
    item = AdditionalInformationItem.objects.get()
    assert tab.item == item.pk
    assert [
        (row.label, row.answer, row.stage_label) for row in tab.subject.changed
    ] == [
        ("Cannot attend Mass or prayer services", "Yes", "To review"),
        ("Additional information", "Please call about the choir", "To review"),
    ]
    # Staff mark follow-up needed, then completed, through the real service.
    client, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(email="admin@example.org").pk
    for needed, done, stage in ((True, False, "To handle"), (True, True, "Handled")):
        item.refresh_from_db()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            update_information(
                harness.service.store,
                actor,
                item.pk,
                expected_version=item.version,
                follow_up_needed=needed,
                followed_up=done,
                confirm_clear=False,
                request_key=uuid4(),
                notes="",
            )
        tab, _ = as_web(*read, ministry=False, information=True)
        assert tab.subject.changed[-1].stage_label == stage
    # The Family answers again with no text: the item is withdrawn, so the
    # tab lists "None given" as unchanged.
    harness, form, answers, _ = revisit(harness)
    answer(harness, form, answers, cannot_attend=False, additional_information="")
    tab, _ = as_web(*read, ministry=False, information=True)
    assert tab.item is None and tab.subject.changed == ()
    assert [(row.label, row.answer) for row in tab.subject.unchanged] == [
        ("Cannot attend Mass or prayer services", "No"),
        ("Additional information", "None given"),
    ]
    # Another Family, with no response: no subject.
    other = FamilyCampaign.objects.filter(campaign=harness.campaign).exclude(
        pk=family_id
    )
    tab, statements = as_web(
        read_hub_other,
        harness.campaign.pk,
        other.first().pk,
        (),
        ministry=False,
        information=True,
    )
    assert tab.subject is None and statements == 1

    path = reverse("admin:timeline", args=[family_id])
    before = viewed()
    body, counts = page(client, path + "?tab=other")
    assert counts == {"ministry": 0, "financial": 0, "other": 1}
    drawn = body.split('id="family-tabs-panel-other"')[1]
    assert "Show unchanged (2 fields)" in drawn and "None given" in drawn
    assert viewed() == before + 1
