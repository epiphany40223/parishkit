"""Actual-role complete directory capture and the shared private export lifecycle."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditEvent
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
        # Mailing columns do not narrow the capture (#202); reach does.
        assert postal.row_count == 52
        assert (
            create_directory_export(
                harness.service.store,
                actor,
                **(
                    values
                    | {
                        "postal": True,
                        "query": DirectoryQuery(sort="duid", reach="mail"),
                        "request_key": uuid4(),
                    }
                ),
            ).directory_snapshot.row_count
            == 51
        )
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
            # The same one-time key reused for another format (a page back
            # from the browser cache) is refused clearly, and queues nothing.
            other = "pdf" if format != "pdf" else "csv"
            before = ExportRequest.objects.count()
            reused = post(browser, route + "export", fields | {"format": other})
            assert reused.status_code == 409
            assert b"already used for a different export" in reused.content
            assert ExportRequest.objects.count() == before
            job_route = response["Location"]
            with CaptureQueriesContext(connection) as queries:
                response, body = read(browser, job_route)
            assert all(
                '"stewardship_directory_export_snapshot"."document"' not in item["sql"]
                for item in queries.captured_queries
            )
            assert response.status_code == 200 and b"Family-directory export" in body
            # A code-list export with no reach filter returns to the plain page.
            assert f'href="{route}">'.encode() in body
            assert b"mail-merge export" not in body
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
                {
                    "csv": b"Family,ParishSoft DUID,Family code\r\n",
                    "xlsx": b"PK",
                    "pdf": b"%PDF",
                }[format]
            )
            assert "family_directory." + format in response["Content-Disposition"]
            if format == "csv":
                # The code list names Families and codes, not addresses.
                assert harness.code.encode() in body
                assert b"1 Example Street" not in body
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
            == "Example"
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
        # The mail merge covers the filtered rows, here the one Family that
        # email reaches (#202).
        assert (
            request.report == "postal_outreach"
            and request.directory_snapshot.row_count == 1
        )
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT id,row_count FROM stewardship_directory_export_snapshot")
        assert cursor.fetchone() == (request.directory_snapshot_id, 1)
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
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with pytest.raises(PermissionError):
            create_directory_export(store, actor, **(values | {"request_key": uuid4()}))
        response, body = read(browser, route + "?mailing=yes")
        assert response.status_code == 200 and b"<fieldset disabled>" in body
        assert b"Other campaign work" in body
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
        "mailing": "yes",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, route + "export", fields).status_code == 403
        # Mailing columns (addresses) stay with the roles that could open the
        # old postal page; the old postal routes keep their checks too.
        assert read(browser, route + "?mailing=yes")[0].status_code == 403
        denied = post(browser, route, {"mailing": "yes", "reach": "email"})
        assert denied.status_code == 403 and b"Example Street" not in denied.content
        legacy = f"/admin/reports/{harness.campaign.pk}/postal/"
        # A bookmarked old postal URL redirects without reading anything,
        # then the merged page denies a Ministry leader or a signed-out
        # visitor exactly as the old page did.
        target = route + "?reach=mail&mailing=yes"
        for visitor in (browser, Client()):
            redirected = visitor.get(legacy)
            assert redirected.status_code == 302
            assert redirected["Location"] == target
            response, body = read(visitor, target)
            assert response.status_code == 403 and harness.code.encode() not in body
            assert b"Example Street" not in body
        assert post(browser, legacy, {}).status_code == 403
        assert post(browser, legacy + "export", fields).status_code == 403
        assert (
            read(browser, f"/admin/reports/exports/{request.pk}/")[0].status_code == 403
        )


def test_postal_mail_merge_leaves_out_families_without_a_mailing_address(
    live_response_service, google, tmp_path, settings
):
    """The postal file has mailable Families only; the publication keeps the count."""
    harness = live_response_service
    data = response_source()
    data.members[3]["emailAddress"] = ""
    for duid in (10, 11):
        data.families[duid] = data.families[1] | {
            "familyDUID": duid,
            "familyID": duid + 100,
            "lastName": "Unmailable",
            "primaryCity": "",
        }
        data.members[1000 + duid] = data.members[3] | {
            "memberDUID": 1000 + duid,
            "familyDUID": duid,
            "emailAddress": "",
        }
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    browser, _ = signed_in()
    root = tmp_path / "postal-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    fields = DirectoryQuery().form_values() | dict(
        mailing="yes", format="csv", browser_timezone="UTC", request_key=str(uuid4())
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, route + "?mailing=yes")
        # A campaign-wide fact, not a claim about this file's contents.
        assert (
            b"Across all active Families, 2 Families have neither a deliverable" in body
        )
        # The "neither" list itself needs no notice about those Families.
        response, neither = search(
            browser, route, {"mailing": "yes", "reach": "neither"}
        )
        assert response.status_code == 200 and b"Unmailable" in neither
        assert b"data-unreachable-notice" not in neither
        # The mailing columns show what the mail-merge file will hold, and
        # say which Families it leaves out.
        for text in (
            b"Addressee",
            b"Mailing address",
            b"No usable mailing address; left out of the mail-merge file",
            b'name="mailing" value="yes" checked',
            b'<input type="hidden" name="mailing" value="yes">',
            b"ParishSoft DUID, Family, Addressee, Family heads,",
        ):
            assert text in body
        response = post(browser, route + "export", fields)
        assert response.status_code == 302
    request = ExportRequest.objects.get(request_key=fields["request_key"])
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
    with restricted_download_pool(settings):
        response, body = search(browser, response["Location"] + "download", {})
    assert response.status_code == 200
    lines = body.decode().splitlines()
    assert lines[0].startswith("ParishSoft DUID,Family,Addressee,Family heads,")
    assert len(lines) == 2 and lines[1].startswith("1,Example,")
    assert "40000" in lines[1] and harness.code in lines[1]
    assert request.directory_snapshot.row_count == 3
    assert request.report == "postal_outreach"
    # A form rendered before the merge has no mailing field and posts to the
    # old postal export route; it still queues the postal mail merge.
    legacy = DirectoryQuery().form_values() | dict(
        format="csv", browser_timezone="UTC", request_key=str(uuid4())
    )
    old_route = f"/admin/reports/{harness.campaign.pk}/postal/export"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, old_route, legacy).status_code == 302
    old = ExportRequest.objects.get(request_key=legacy["request_key"])
    assert old.report == "postal_outreach" and old.parameters["postal"] is True
    assert old.directory_snapshot.row_count == 3


def test_mail_merge_covers_exactly_the_filtered_email_reachable_rows(
    live_response_service, google, tmp_path, settings
):
    """Mailing columns do not narrow the rows: the file holds the filtered rows.

    The one Family has deliverable email and a mailing address. Filtered to
    "By email" with mailing columns on (#202), the page and the mail merge
    both hold it, with its addressee and address.
    """
    harness = live_response_service
    browser, _ = signed_in()
    root = tmp_path / "mailing-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    filters = DirectoryQuery(reach="email").form_values() | {"mailing": "yes"}
    fields = filters | dict(
        format="csv", browser_timezone="UTC", request_key=str(uuid4())
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, route, filters)
        assert response.status_code == 200 and b"Matching Families: 1." in body
        assert b"<td>1 Example Street<br>" in body
        response = post(browser, route + "export", fields)
        assert response.status_code == 302
    request = ExportRequest.objects.get(request_key=fields["request_key"])
    assert request.report == "postal_outreach"
    assert request.parameters["postal"] is True
    assert request.parameters["filters"]["reach"] == "email"
    assert request.directory_snapshot.row_count == 1
    # The status page names the mail merge and returns to the same filter
    # with mailing columns on (closed presets only).
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        status, body = read(browser, response["Location"])
    assert status.status_code == 200
    assert b"Family-directory mail-merge export" in body
    assert f'href="{route}?reach=email&amp;mailing=yes"'.encode() in body
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
    with restricted_download_pool(settings):
        response, body = search(browser, response["Location"] + "download", {})
    assert response.status_code == 200
    lines = body.decode().splitlines()
    assert lines[0].startswith("ParishSoft DUID,Family,Addressee,Family heads,")
    assert len(lines) == 2 and lines[1].startswith("1,Example,Member Example,")
    assert "1 Example Street" in lines[1] and harness.code in lines[1]
    events = AuditEvent.objects.filter(event_type="postal_outreach_viewed")
    assert events.exists()
