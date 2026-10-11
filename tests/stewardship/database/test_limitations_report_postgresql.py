"""Limitations report under the real web role (#247, #970).

The report was "Talents and limitations". Responses submitted before talents
were retired keep their stored talents; ``submit_live`` stores one such
response as a form opened before #970 would send it (the configured default
talents are still accepted), and the report must leave those talents out.
"""

import io
from uuid import uuid4

import pytest
from django.db import transaction
from django.urls import reverse
from openpyxl import load_workbook

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.limitations import (
    LimitationsQuery,
    limitations_report,
)
from parishkit.stewardship.responses.service import DEFAULT_TALENTS
from parishkit.stewardship.source.models import SourceCurrent

from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_followup_postgresql import read as followup
from .test_ministry_reports_postgresql import actor
from .test_ministry_responses_postgresql import respond, start
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4}))
PAINTER, OTHER = DEFAULT_TALENTS[0].id, DEFAULT_TALENTS[-1].id


def submit_live(harness, *, cannot_serve=False):
    """One live response with stored talents (as before #970) and limitations."""
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
    campaign = Campaign.objects.get(pk=harness.campaign.pk)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return limitations_report(
            campaign.pk, LimitationsQuery.parse(values), principal
        )


def directory_name(duid):
    """The Family's name as the directory shows it, from the current snapshot."""
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.snapshot_names import snapshot_family_names

    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).get()
    return snapshot_family_names(snapshot, [duid])[duid]


def test_report_lists_limitations_and_filters(response_service):
    """Rows come from the effective live response, filterable; no talents."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submission = submit_live(harness, cannot_serve=True)
    # The stored answer keeps its talents (#970); the report never shows them.
    assert submission.answers["service"]["members"]["3"]["talents"] == {
        PAINTER: "",
        OTHER: "Organ",
    }
    result = report(harness)
    assert len(result["members"]) == 1 and len(result["families"]) == 1
    row = result["members"][0]
    assert "talents" not in row
    assert row["cannot_serve"] is True and row["member_name"]
    # Both tables name the Family as every Admin table does (#932).
    family = result["families"][0]
    assert ", " in row["family_name"] and family["family_name"] == row["family_name"]
    assert row["family_name"] == directory_name(row["family_duid"])
    assert result["summary"] == {"cannot_serve": 1, "cannot_attend": 1}
    only_families = report(harness, show="cannot_attend")
    assert only_families["members"] == [] and len(only_families["families"]) == 1
    assert report(harness, search="no such name")["members"] == []
    with pytest.raises(PermissionError):
        report(harness, principal=LEADER)
    for invalid in ({"show": PAINTER}, {"talent": "any"}, {"extra": "x"}):
        with pytest.raises(ValueError):
            LimitationsQuery.parse(invalid)
    # The ministry follow-up queue explains why this Member is leaving.
    # The queue resolves scope from the actor in SQL (#389 L3), so it needs a
    # real portal user rather than the invented STAFF Principal.
    queue = followup(harness, actor(harness, ("staff",), ()))
    assert [(row["action"], row["cannot_serve"]) for row in queue["rows"]] == [
        ("leave", True)
    ]


def test_native_page_and_downloads(response_service, google, settings):
    """The page filters by CSRF POST; CSV, XLSX and PDF carry the same rows.

    Read with the real restricted web and download logins
    (``restricted_download_pool``): the download pool's login reads no
    campaign or source data, so a download must be read on the web
    connection, never there (#557).
    """
    settings.STEWARDSHIP_DOWNLOAD_POOL = None  # restricted_download_pool sets it
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness, cannot_serve=True)
    route = reverse("admin:limitations_report")
    assert route == "/admin/reports/limitations/"
    browser, login = signed_in()
    assert login.status_code == 302
    with restricted_download_pool(settings):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert b"<h1>Limitations</h1>" in body and b"Organ" not in body
        assert b"Painter" not in body and b"Members who cannot participate" in body
        assert get(browser, route + "?search=x")[0].status_code == 400
        response, body = search(browser, route, {"show": "cannot_attend"})
        assert response.status_code == 200 and b"No matching Members." in body
        assert search(browser, route, {"show": "bad"})[0].status_code == 400
        # The filter's old name and a talent's id are refused (#970).
        assert search(browser, route, {"talent": "any"})[0].status_code == 400
        assert search(browser, route, {"show": str(uuid4())})[0].status_code == 400
        # Shared tables (#203): every column sorts on the server, both tables
        # page independently, and the controls are POST forms, never links.
        response, body = search(
            browser,
            route,
            {"show": "any", "members_sort": "-latest", "families_size": "25"},
        )
        assert response.status_code == 200 and b"Page 1 of 1" in body
        assert b'aria-sort="descending"' in body and b'<a class="sort-link"' not in body
        assert b'name="families_size" value="25"' in body
        assert b'name="members_sort" value="latest"' in body
        # The Members table's Family DUID has its own sortable column (#932).
        response, body = search(browser, route, {"show": "any", "members_sort": "duid"})
        assert response.status_code == 200 and b"Organ" not in body
        assert b'name="members_sort" value="-duid"' in body
        for invalid in (
            {"members_sort": "submitted_at"},
            {"families_sort": "-talents"},
            {"members_sort": "talents"},
            {"members_size": "7"},
            {"families_page": "x"},
        ):
            assert search(browser, route, invalid)[0].status_code == 400
        export = reverse("admin:limitations_export")
        response, body = search(
            browser, export, {"format": "csv", "timezone": "UTC", "show": "any"}
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert response["Cache-Control"] == "no-store"
        assert "stewardship-limitations-" in response["Content-Disposition"]
        assert b"Organ" not in body and b"Cannot attend Mass" in body
        assert body.startswith(
            b"Member,Family,Family DUID,Cannot participate in ministries,"
            b"Latest response\r\n"
        )
        response, body = search(browser, export, {"format": "xlsx", "timezone": "UTC"})
        assert response.status_code == 200
        assert response["Content-Disposition"].endswith(".xlsx")
        book = load_workbook(io.BytesIO(body))
        assert book.sheetnames == ["Members", "Families"]
        assert [cell.value for cell in book["Members"][1]] == [
            "Member",
            "Family",
            "Family DUID",
            "Cannot participate in ministries",
            "Latest response",
        ]
        assert book["Members"]["D2"].value == "Yes"
        # The PDF is offered too (#966), named and typed as one.
        response, body = search(browser, export, {"format": "pdf", "timezone": "UTC"})
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].endswith(".pdf")
        assert body.startswith(b"%PDF-")
        assert search(browser, export, {"format": "png"})[0].status_code == 400
        # The re-posted forms carry the Show choice.
        response, body = search(browser, route, {"members_sort": "-latest"})
        assert response.status_code == 200 and b'name="show" value="any"' in body
        _, body = get(browser, reverse("admin:participation"))
        assert route.encode() in body
        # The campaign reports page links the response dashboard too (#477).
        assert route.replace("limitations", "responses").encode() in body
        # The old address is gone, with no redirect (no legacy Admin URLs).
        assert browser.get("/admin/reports/talents/").status_code == 404
        assert browser.get("/admin/campaign/talents/").status_code == 404
        # A search is audited as used, never as its text (#556).
        assert search(browser, route, {"search": "Organ"})[0].status_code == 200
    assert AuditEvent.objects.filter(event_type="talents_report_viewed").exists()
    assert AuditEvent.objects.filter(event_type="talents_report_exported").exists()
    # Each records its Show choice, whether the search box was used and the
    # ParishSoft snapshot read (#556). The report keeps the events it
    # recorded as Talents and limitations (#970).
    snapshot = str(SourceCurrent.objects.get().snapshot_id)
    contexts = [
        (event.event_type, event.auditcontext.context)
        for event in AuditEvent.objects.filter(
            event_type__startswith="talents_report_"
        ).select_related("auditcontext")
    ]
    assert all(context["snapshot_id"] == snapshot for _, context in contexts)
    assert all("talent_option_id" not in context for _, context in contexts)
    assert ("talents_report_viewed", "cannot_attend", False) in {
        (kind, context["report_filter"], context["search_used"])
        for kind, context in contexts
    }
    assert {
        context["report_filter"]
        for kind, context in contexts
        if kind == "talents_report_exported"
    } == {"any"}
    assert ("talents_report_viewed", "any", True) in {
        (kind, context["report_filter"], context["search_used"])
        for kind, context in contexts
    }
    assert "Organ" not in str(contexts)


def test_both_tables_sort_every_column_in_memory():
    """The complete in-memory report sorts before paging, per table."""
    from datetime import UTC, datetime

    from parishkit.stewardship.reports.limitation_views import tables

    def row(name, duid, day, cannot=False):
        """One member/family row as the selection shapes it."""
        return {
            "member_name": name,
            "family_name": name,
            "family_duid": duid,
            "cannot_serve": cannot,
            "submitted_at": datetime(2026, 9, day, tzinfo=UTC),
        }

    result = {
        "members": [row("b", 2, 1), row("A", 1, 3, True)],
        "families": [row("b", 2, 1), row("a", 1, 3)],
    }
    members, families = tables(
        result,
        LimitationsQuery(search="private"),
        {"members_sort": "-cannot_serve", "families_sort": "-duid"},
        "/r/",
    )
    assert [r["family_duid"] for r in members.rows] == [1, 2]
    assert [r["family_duid"] for r in families.rows] == [2, 1]
    assert members.method == families.method == "post" and members.action == "/r/"
    assert ("search", "private") in members.carried
    assert ("families_sort", "-duid") in members.carried
    members, _ = tables(result, LimitationsQuery(), {"members_sort": "-latest"}, "/r/")
    assert [r["family_duid"] for r in members.rows] == [1, 2]
