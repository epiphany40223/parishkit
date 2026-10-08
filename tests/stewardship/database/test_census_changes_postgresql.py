"""Census changes worklist under the real web role (#528)."""

import io
from uuid import uuid4

import pytest
from django.db import transaction
from openpyxl import load_workbook

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.census_changes import CensusQuery, census_changes

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_responses_postgresql import respond, start
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
ADMIN = Principal(uuid4(), frozenset({"administrator"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4}))


def submit_live(harness):
    """One live response that renames Member 3, an automatic change."""
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        return respond(harness, form, answers)


def worklist(harness, principal=STAFF, **values):
    """Read through the restricted web grants, not the migration owner."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return census_changes(harness.campaign.pk, CensusQuery.parse(values), principal)


def test_worklist_reads_proposals_with_their_status(response_service):
    """A live response's change is listed, worded, and To review only for an
    Administrator; Staff find it with the status filter; leaders never."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    assert worklist(harness)["rows"] == []
    rows = worklist(harness, ADMIN)["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "First name" and row["family_answer"] == "Requested"
    assert row["status"] == "to_review" and row["route_label"] == "Automatic"
    assert row["who"] and row["family_duid"]
    assert len(worklist(harness, status="to_review")["rows"]) == 1
    assert worklist(harness, status="all", route="by_hand")["rows"] == []
    assert worklist(harness, status="all", search="no such name")["rows"] == []
    with pytest.raises(PermissionError):
        worklist(harness, LEADER)


def test_native_page_and_downloads(response_service, google, settings):
    """The page filters by CSRF POST; CSV and XLSX carry the same rows, and
    the view and download are audited."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = None  # restricted_download_pool sets it
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    route = f"/admin/reports/{harness.campaign.pk}/census/"
    browser, login = signed_in()
    assert login.status_code == 302
    with restricted_download_pool(settings):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert b"Requested" in body and b"To review" in body
        assert get(browser, route + "?search=x")[0].status_code == 400
        response, body = search(browser, route, {"status": "conflict"})
        assert response.status_code == 200 and b"No matching census changes." in body
        for invalid in ({"status": "bad"}, {"sort": "id"}, {"size": "7"}):
            assert search(browser, route, invalid)[0].status_code == 400
        response, body = search(browser, route, {"sort": "-submitted"})
        assert response.status_code == 200 and b'aria-sort="descending"' in body
        export = route + "export"
        response, body = search(
            browser, export, {"format": "csv", "timezone": "UTC", "status": "all"}
        )
        assert response.status_code == 200 and response["Content-Type"] == "text/csv"
        assert "stewardship-census-changes-" in response["Content-Disposition"]
        assert b"Requested" in body and b"How it reaches ParishSoft" in body
        response, body = search(
            browser, export, {"format": "xlsx", "timezone": "UTC", "status": "all"}
        )
        assert response.status_code == 200
        book = load_workbook(io.BytesIO(body))
        assert book.sheetnames == ["Census changes"]
        assert book["Census changes"]["F2"].value == "Requested"
        assert search(browser, export, {"format": "pdf"})[0].status_code == 400
    assert AuditEvent.objects.filter(event_type="census_changes_viewed").exists()
    assert AuditEvent.objects.filter(event_type="census_changes_exported").exists()
