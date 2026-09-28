"""Talents and limitations report under the real web role (#247)."""

import io
from uuid import uuid4

import pytest
from django.db import transaction
from openpyxl import load_workbook

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.talents import TalentQuery, talents_report
from parishkit.stewardship.responses.service import DEFAULT_TALENTS

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_ministry_followup_postgresql import read as followup
from .test_ministry_responses_postgresql import respond, start
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4}))
PAINTER, OTHER = DEFAULT_TALENTS[0].id, DEFAULT_TALENTS[-1].id


def submit_live(harness, *, cannot_serve=False):
    """One live response with a talent, "Other" text and the welcome answer."""
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["cannot_attend"] = True
        answers["service"] = {
            "members": {
                "3": {
                    "cannot_serve": cannot_serve,
                    "talents": {PAINTER: "", OTHER: "Organ"},
                }
            },
            "proposed_members": {},
        }
        if cannot_serve:
            answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
        return respond(harness, form, answers)


def report(harness, principal=STAFF, **values):
    """Read through the restricted web grants, not the migration owner."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return talents_report(
            campaign.pk,
            TalentQuery.parse(values),
            principal,
            configuration=campaign.active_configuration.values,
        )


def test_report_lists_talents_limitations_and_filters(response_service):
    """Rows come from the effective live response, worded and filterable."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness, cannot_serve=True)
    result = report(harness)
    assert len(result["members"]) == 1 and len(result["families"]) == 1
    row = result["members"][0]
    assert row["talents"] == ["Painter", "Other: Organ"]
    assert row["cannot_serve"] is True and row["member_name"]
    assert result["summary"]["cannot_attend"] == 1
    assert dict(result["summary"]["talents"])["Painter"] == 1
    assert len(report(harness, talent=PAINTER)["members"]) == 1
    assert report(harness, talent=DEFAULT_TALENTS[1].id)["members"] == []
    only_families = report(harness, talent="cannot_attend")
    assert only_families["members"] == [] and len(only_families["families"]) == 1
    assert report(harness, search="no such name")["members"] == []
    with pytest.raises(PermissionError):
        report(harness, principal=LEADER)
    for invalid in ({"talent": "painter"}, {"extra": "x"}):
        with pytest.raises(ValueError):
            TalentQuery.parse(invalid)
    # The ministry follow-up queue explains why this Member is leaving.
    queue = followup(harness, STAFF)
    assert [(row["action"], row["cannot_serve"]) for row in queue["rows"]] == [
        ("leave", True)
    ]


def test_native_page_and_downloads(response_service, google, settings):
    """The page filters by CSRF POST; CSV and XLSX carry the same rows."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    route = f"/admin/reports/{harness.campaign.pk}/talents/"
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert b"Other: Organ" in body and b"Painter" in body
        assert get(browser, route + "?search=x")[0].status_code == 400
        response, body = search(browser, route, {"talent": "cannot_serve"})
        assert response.status_code == 200 and b"No matching Members." in body
        assert search(browser, route, {"talent": "bad"})[0].status_code == 400
        export = route + "export"
        response, body = search(
            browser, export, {"format": "csv", "timezone": "UTC", "talent": "any"}
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert b"Other: Organ" in body and b"Cannot attend Mass" in body
        response, body = search(browser, export, {"format": "xlsx", "timezone": "UTC"})
        assert response.status_code == 200
        book = load_workbook(io.BytesIO(body))
        assert book.sheetnames == ["Members", "Families"]
        assert book["Members"]["D2"].value == "Painter; Other: Organ"
        assert search(browser, export, {"format": "pdf"})[0].status_code == 400
        _, body = get(browser, route.replace("talents", "participation"))
        assert route.encode() in body
    assert AuditEvent.objects.filter(event_type="talents_report_viewed").exists()
    assert AuditEvent.objects.filter(event_type="talents_report_exported").exists()
