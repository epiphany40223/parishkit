"""Actual-role complete directory capture and the shared private export lifecycle."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.directories import DirectoryQuery
from parishkit.stewardship.reports.directory_exports import create_directory_export
from parishkit.stewardship.reports.export_models import (
    DirectoryExportSnapshot,
    ExportRequest,
)
from parishkit.stewardship.reports.export_services import TASK_TYPE, export_status
from parishkit.stewardship.reports.export_tasks import export_handler

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_directories_postgresql import page
from .test_export_views_postgresql import post, restricted_download_pool
from .test_information_followup_postgresql import search
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def test_complete_directory_capture_is_private_immutable_and_source_pinned(
    live_response_service,
):
    """One promotion proves complete postal scope and stable code selection."""
    harness = live_response_service
    data = response_source()
    for duid in range(10, 61):
        data.families[duid] = data.families[1] | {
            "familyDUID": duid,
            "familyID": duid + 100,
            "firstName": "Repeated",
            "lastName": "Family",
        }
        data.members[1000 + duid] = data.members[3] | {
            "memberDUID": 1000 + duid,
            "familyDUID": duid,
            "memberType": "Other",
            "emailAddress": "",
        }
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    actor = user("admin@example.org").pk
    values = dict(
        campaign_id=harness.campaign.pk,
        query=DirectoryQuery(sort="duid"),
        postal=False,
        mac=harness.rings.mac,
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
    )
    interactive = page(harness, sort="duid")
    assert interactive["total"] == 52 and len(interactive["rows"]) == 50
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_directory_export(harness.service.store, actor, **values)
        retained = DirectoryExportSnapshot.objects.get(pk=request.directory_snapshot_id)
        assert retained.row_count == len(retained.document["rows"]) == 52
        assert [row["family_id"] for row in retained.document["rows"][:50]] == [
            row["family_id"] for row in interactive["rows"]
        ]
        assert harness.code not in json.dumps(retained.document)
        assert all("code" not in row for row in retained.document["rows"])
        assert (
            create_directory_export(harness.service.store, actor, **values).pk
            == request.pk
        )
        with pytest.raises(ValueError, match="already bound"):
            create_directory_export(
                harness.service.store, actor, **(values | {"postal": True})
            )
        postal = create_directory_export(
            harness.service.store,
            actor,
            **(values | {"postal": True, "request_key": uuid4()}),
        ).directory_snapshot
        assert postal.row_count == 51
        selected = create_directory_export(
            harness.service.store,
            actor,
            **(
                values
                | {
                    "query": DirectoryQuery(exact_code=harness.code.lower()),
                    "request_key": uuid4(),
                }
            ),
        )
        assert selected.directory_snapshot.row_count == 1
        assert selected.parameters["exact"] and selected.parameters["family_id"]
        assert harness.code.lower() not in json.dumps(selected.parameters).lower()
        assert "exact_code" not in selected.parameters["filters"]
        empty = create_directory_export(
            harness.service.store,
            actor,
            **(
                values
                | {
                    "query": DirectoryQuery(exact_code="ZZZZZZZZ"),
                    "request_key": uuid4(),
                }
            ),
        )
        assert empty.directory_snapshot.row_count == 0 and empty.parameters["exact"]
        for statement in (
            "UPDATE stewardship_directory_export_snapshot SET document='{}'",
            "DELETE FROM stewardship_directory_export_snapshot",
        ):
            with (
                pytest.raises(DatabaseError),
                work_transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        with pytest.raises(DatabaseError), work_transaction():
            orphan = DirectoryExportSnapshot.objects.create(
                campaign_id=harness.campaign.pk,
                configuration_id=request.configuration_id,
                actor_id=actor,
                correlation_id=uuid4(),
                parameters=request.parameters,
                document={"rows": [{"code": "FORGED"}]},
            )
            orphan.refresh_from_db()
            assert "FORGED" not in json.dumps(orphan.document)
    before = retained.document
    data.families[1]["lastName"] = "Later source name"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    retained.refresh_from_db()
    assert retained.document == before
    assert page(harness)["metadata"]["source_id"] != str(retained.source_id)


def test_native_directory_exports_render_download_and_regenerate_retained_inputs(
    live_response_service, google, tmp_path, settings, monkeypatch
):
    """One fixture covers all formats, guarded download and source-change replay."""
    harness = live_response_service
    browser, login = signed_in()
    assert login.status_code == 302
    actor = user("admin@example.org").pk
    root = tmp_path / "directory-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    first = None
    for format in ("csv", "xlsx", "pdf"):
        fields = DirectoryQuery(exact_code=harness.code).form_values() | dict(
            format=format, browser_timezone="UTC", request_key=str(uuid4())
        )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(browser, route)
            assert response.status_code == 200 and b"Queue complete export" in body
            assert browser.post(route + "export", fields).status_code == 403
            if format == "csv":
                assert browser.get(route + "export").status_code == 405
                for invalid in (
                    {"page": "2"},
                    {"family_id": str(uuid4())},
                    {"format": ["csv", "pdf"]},
                ):
                    rejected = post(browser, route + "export", fields | invalid)
                    assert (
                        rejected.status_code == 400
                        and harness.code.encode() not in rejected.content
                    )
                    assert rejected["Cache-Control"] == "no-store"
            response = post(browser, route + "export", fields)
            assert response.status_code == 302
            request = ExportRequest.objects.get(request_key=fields["request_key"])
            first = first or request
            assert (
                post(browser, route + "export", fields)["Location"]
                == response["Location"]
            )
            job_route = response["Location"]
            with CaptureQueriesContext(connection) as queries:
                response, body = read(browser, job_route)
            assert all(
                '"stewardship_directory_export_snapshot"."document"' not in item["sql"]
                for item in queries.captured_queries
            )
            assert response.status_code == 200 and b"Family-directory export" in body
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert execute_hint(
                request.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: export_handler(
                        store=harness.service.store,
                        root=root,
                        general=harness.rings.general,
                    )
                },
            )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert (
                export_status(harness.service.store, actor, request.pk)["state"]
                == "ready"
            )
        with restricted_download_pool(settings):
            response, body = search(browser, job_route + "download", {})
            assert response.status_code == 200
            assert body.startswith(
                {"csv": b"Record,", "xlsx": b"PK", "pdf": b"%PDF"}[format]
            )
            assert "family_directory." + format in response["Content-Disposition"]
            if format == "csv":
                assert harness.code.encode() in body and b"1 Example Street" in body
    data = response_source()
    data.families[1]["lastName"] = "Later changed name"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    from parishkit.stewardship.reports import export_services

    with work_transaction():
        now = export_services.database_now()
    monkeypatch.setattr(
        export_services, "database_now", lambda: now + timedelta(days=8)
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        fields = {"request_key": str(uuid4())}
        response = post(
            browser, f"/admin/reports/exports/{first.pk}/regenerate", fields
        )
        assert response.status_code == 302
        regenerated = ExportRequest.objects.get(request_key=fields["request_key"])
        assert regenerated.directory_snapshot_id == first.directory_snapshot_id
        assert (
            regenerated.directory_snapshot.document["rows"][0]["family_name"]
            == "Household Example"
        )


def test_directory_export_staff_gates_and_service_boundaries(
    live_response_service, google
):
    """Check Staff capture, preparation gates and metadata-only service roles."""
    harness = live_response_service
    store = harness.service.store
    rule = address("reader@example.org", roles=("staff", "ministry_leader"))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(google_subject=google[0]["sub"]).pk
    values = dict(
        campaign_id=harness.campaign.pk,
        query=DirectoryQuery(),
        postal=True,
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
        mac=harness.rings.mac,
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_directory_export(store, actor, **values)
        assert (
            request.report == "postal_outreach"
            and request.directory_snapshot.row_count == 0
        )
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT id,row_count FROM stewardship_directory_export_snapshot")
        assert cursor.fetchone() == (request.directory_snapshot_id, 0)
        with pytest.raises(DatabaseError):
            cursor.execute("SELECT document FROM stewardship_directory_export_snapshot")
    for role in (ServiceRole.WORKER, ServiceRole.SCHEDULER):
        with (
            task_login(role, exact=True, reconnect=True),
            pytest.raises(DatabaseError),
            work_transaction(),
        ):
            DirectoryExportSnapshot.objects.create(
                campaign_id=harness.campaign.pk,
                configuration_id=request.configuration_id,
                actor_id=actor,
                correlation_id=uuid4(),
                parameters=request.parameters,
            )
    # Simulate only the eventual purge owner's sentinel in this disposable DB.
    # No application purge executor or retained user data is involved.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=harness.campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    route = f"/admin/reports/{harness.campaign.pk}/postal/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with pytest.raises(PermissionError):
            create_directory_export(store, actor, **(values | {"request_key": uuid4()}))
        response, body = read(browser, route)
        assert response.status_code == 200 and b"<fieldset disabled>" in body
        assert b"Campaign work is gated" in body
        assert (
            read(browser, f"/admin/reports/exports/{request.pk}/")[0].status_code == 200
        )
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "login_rules",
                "id": rule["id"],
                "values": {
                    "roles": ["ministry_leader"],
                    "grants": {
                        "ministry_leader": rule["values"]["grants"]["ministry_leader"]
                    },
                },
            }
        ],
    )
    fields = DirectoryQuery().form_values() | {
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, route + "export", fields).status_code == 403
        assert (
            read(browser, f"/admin/reports/exports/{request.pk}/")[0].status_code == 403
        )
