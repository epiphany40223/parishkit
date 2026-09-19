"""Complete Ministry capture and actual-role requester/worker/download boundaries."""

import io
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.export_models import (
    ExportRequest,
    MinistryExportSnapshot,
)
from parishkit.stewardship.reports.export_services import (
    TASK_TYPE,
    authorize,
    cancel_export,
    consume_download,
    export_status,
    issue_download,
)
from parishkit.stewardship.reports.export_tasks import export_handler, load_document
from parishkit.stewardship.reports.information_rendering import render_information
from parishkit.stewardship.reports.ministries import MinistryQuery
from parishkit.stewardship.reports.ministry_exports import create_ministry_export

from ..policy_factory import address, assignment
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post, restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_reports_postgresql import page, setup
from .test_ministry_responses_postgresql import configure, ministry_source, respond
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def leader(harness, google, *, roles=("ministry_leader",)):
    """Activate a real exact-address policy and one manually assigned Ministry."""
    rule, assigned = (
        address("leader@example.org", roles=roles),
        assignment(ministry=9),
    )
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in (rule, assigned)
        ],
    )
    google[0]["email"] = "leader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(google_subject=google[0]["sub"]).pk
    return browser, actor, rule, assigned


def create(harness, actor, **overrides):
    """Capture under the real web database role, without migration-owner grants."""
    values = dict(
        campaign_id=harness.campaign.pk,
        query=MinistryQuery(),
        ministry_id=9,
        action="join",
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return create_ministry_export(
            harness.service.store, actor, **(values | overrides)
        )


def test_capture_scope_and_revocation(response_service, google):
    """SQL owns privacy capture; another owner or removed assignment cannot reuse it."""
    harness = setup(response_service)
    _, actor, _, assigned = leader(harness, google)
    request = create(harness, actor)
    snapshot = MinistryExportSnapshot.objects.get(pk=request.ministry_snapshot_id)
    assert snapshot.row_count == 1
    assert snapshot.authorization_scope == {
        "capability": "ministry_report",
        "operational": False,
        "ministries": [9],
    }
    assert "valid@example.org" not in json.dumps(snapshot.document)
    assert "202-555-0123" in json.dumps(snapshot.document)
    assert "1960-01-01" not in json.dumps(snapshot.document)
    with pytest.raises(PermissionError):
        create(harness, actor, ministry_id=4, action="leave")
    other = user("someone@example.org").pk
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in (
                address("someone@example.org", roles=("ministry_leader",)),
                assignment("someone@example.org", ministry=9),
            )
        ],
    )
    admin = user("admin@example.org").pk
    leaving = create(harness, admin, ministry_id=4, action="leave")
    leaving.refresh_from_db()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        capture = leaving.ministry_snapshot.document
        assert capture["rows"][0]["current_role"] == "Chairperson"
        assert capture["rows"][0]["emails"] is None
        document = load_document(leaving)
        assert document.rows[0][-1] == "Chairperson"
        assert len(document.headings) == 9
        assert "Email" not in document.headings and "Phones" not in document.headings
        for format in ("csv", "xlsx", "pdf"):
            output = io.BytesIO()
            render_information(document, output, format=format)
            assert output.getvalue()
            if format == "csv":
                assert b"Chairperson" in output.getvalue()
                assert b"valid@example.org" not in output.getvalue()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert (
            export_status(harness.service.store, actor, request.pk)["state"] == "queued"
        )
        with pytest.raises(PermissionError):
            authorize(harness.service.store, other, request=request)
        assert authorize(store, admin, request=request).identity == admin
        for statement in (
            "UPDATE stewardship_ministry_export_snapshot SET document='{}'",
            "DELETE FROM stewardship_ministry_export_snapshot",
        ):
            with (
                pytest.raises(DatabaseError),
                work_transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        with pytest.raises(DatabaseError), work_transaction():
            forged = MinistryExportSnapshot.objects.create(
                campaign_id=harness.campaign.pk,
                configuration_id=request.configuration_id,
                actor_id=actor,
                correlation_id=uuid4(),
                parameters=request.parameters,
                document={"forged": "secret"},
                authorization_scope={"operational": True},
            )
            forged.refresh_from_db()
            assert forged.authorization_scope["operational"] is False
            assert "secret" not in json.dumps(forged.document)
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "remove", "section": "login_rules", "id": assigned["id"]}],
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with pytest.raises(PermissionError):
            export_status(store, actor, request.pk)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_export_request_authorized_v1(%s,r) "
                "FROM stewardship_export_request r WHERE id=%s",
                [actor, request.pk],
            )
            assert cursor.fetchone() == (False,)


def test_complete_capture_stays_stable_after_source_promotion(response_service):
    """One Family response supplies 52 matches; HTML and export share ordering."""
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
    actor = user("admin@example.org").pk
    result = create(harness, actor)
    retained = MinistryExportSnapshot.objects.get(pk=result.ministry_snapshot_id)
    assert retained.row_count == len(retained.document["rows"]) == 52
    assert [row["id"] for row in retained.document["rows"][:50]] == [
        row["id"] for row in page(harness, ministry=9)["rows"]
    ]
    empty = create(harness, actor, query=MinistryQuery(search="No such Member"))
    assert empty.ministry_snapshot.row_count == 0
    assert empty.authorization_scope["ministries"] == [9]
    summary = create(harness, actor, ministry_id=None, action="summary")
    assert summary.ministry_snapshot.row_count == 2
    assert (
        AuditEvent.objects.filter(
            event_type="export_requested", subject_id=summary.pk
        ).count()
        == 1
    )
    event = AuditEvent.objects.get(event_type="export_requested", subject_id=summary.pk)
    assert ExportRequest.objects.get(pk=event.subject_id).authorization_scope == {
        "capability": "ministry_report",
        "operational": True,
        "ministries": [4, 9],
    }
    assert (
        MinistryExportSnapshot.objects.get(pk=summary.ministry_snapshot_id).document[
            "rows"
        ]
        == []
    )
    before = retained.document
    data.members[3]["firstName"] = "Changed after capture"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    retained.refresh_from_db()
    assert retained.document == before
    assert page(harness, ministry=9)["metadata"]["source_id"] != str(retained.source_id)


def test_native_leader_exports_worker_download_and_regeneration(
    response_service,
    google,
    settings,
    tmp_path,
    monkeypatch,
):
    """Exercise real session, native POST, all formats, publication and scoped bytes."""
    harness = setup(response_service)
    browser, actor, _, assigned = leader(harness, google)
    root = tmp_path / "ministry-exports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    route = f"/admin/reports/{harness.campaign.pk}/ministries/"
    first = None
    for format in ("csv", "xlsx", "pdf"):
        fields = MinistryQuery().form_values() | dict(
            ministry="9",
            action="join",
            format=format,
            browser_timezone="UTC",
            request_key=str(uuid4()),
        )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = search(browser, route + "join/", {"ministry": "9"})
            assert response.status_code == 200 and b"Queue complete export" in body
            assert browser.post(route + "export/", fields).status_code == 403
            if format == "csv":
                for invalid in (
                    {"scope": "all"},
                    {"page": "2"},
                    {"ministry": "09"},
                    {"format": ["csv", "pdf"]},
                ):
                    assert (
                        post(browser, route + "export/", fields | invalid).status_code
                        == 400
                    )
            response = post(browser, route + "export/", fields)
            assert response.status_code == 302
            result = ExportRequest.objects.get(request_key=fields["request_key"])
            first = first or result
            assert (
                post(browser, route + "export/", fields)["Location"]
                == response["Location"]
            )
            job_route = response["Location"]
            response, body = read(browser, job_route)
            assert response.status_code == 200 and b"Ministry export" in body
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            document = load_document(result)
            assert "valid@example.org" not in str(document.rows)
            assert execute_hint(
                result.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: export_handler(store=harness.service.store, root=root)
                },
            )
        with restricted_download_pool(settings):
            response, body = search(browser, job_route + "download", {})
            assert response.status_code == 200
            assert body.startswith(
                {"csv": b"Member,", "xlsx": b"PK", "pdf": b"%PDF"}[format]
            )
            if format == "csv":
                assert b"Not published" in body and b"valid@example.org" not in body
                assert b"1960-01-01" not in body and b"202-555-0123" in body
    from parishkit.stewardship.reports import export_services

    with work_transaction():
        now = export_services.database_now()
    with monkeypatch.context() as patch:
        patch.setattr(export_services, "database_now", lambda: now + timedelta(days=8))
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            fields = {"request_key": str(uuid4())}
            response = post(
                browser, f"/admin/reports/exports/{first.pk}/regenerate", fields
            )
            assert response.status_code == 302
            regenerated = ExportRequest.objects.get(request_key=fields["request_key"])
            assert regenerated.ministry_snapshot_id == first.ministry_snapshot_id
    store = harness.service.store
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        grant = issue_download(store, actor, first.pk)
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "remove", "section": "login_rules", "id": assigned["id"]}],
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert read(browser, job_route)[0].status_code == 403
        assert post(browser, job_route + "download", {}).status_code == 403
        with pytest.raises(PermissionError):
            issue_download(store, actor, first.pk)
        with pytest.raises(PermissionError):
            consume_download(store, actor, grant.pk)
        with pytest.raises(DatabaseError), work_transaction():
            from parishkit.stewardship.reports.export_models import ExportDownloadUse

            ExportDownloadUse.objects.create(grant=grant, actor_id=actor)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(PermissionError),
    ):
        execute_hint(
            regenerated.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: export_handler(store=store, root=root)},
        )


def test_operational_capture_cannot_survive_staff_downgrade(
    response_service,
    google,
    tmp_path,
):
    """Retaining the same Ministry assignment does not retain operational columns."""
    harness = setup(response_service)
    _, actor, rule, _ = leader(harness, google, roles=("staff", "ministry_leader"))
    store = harness.service.store
    result = create(harness, actor)
    assert result.authorization_scope["operational"] is True
    cancelled = create(harness, actor)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        cancel_export(store, actor, cancelled.pk)
    root = tmp_path / "cancelled-exports"
    root.mkdir(mode=0o700)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            cancelled.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: export_handler(store=store, root=root)},
        )
    assert not (root / "exports").exists()
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
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with pytest.raises(PermissionError):
            export_status(store, actor, result.pk)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_export_request_authorized_v1(%s,r) "
                "FROM stewardship_export_request r WHERE id=%s",
                [actor, result.pk],
            )
            assert cursor.fetchone() == (False,)
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(PermissionError),
    ):
        execute_hint(
            result.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: export_handler(store=store, root=root)},
        )
    limited = create(harness, actor)
    assert limited.authorization_scope["operational"] is False
    # Only the future purge owner's sentinel is synthetic in this disposable DB.
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
    before = MinistryExportSnapshot.objects.count()
    with pytest.raises(PermissionError):
        create(harness, actor)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        MinistryExportSnapshot.objects.create(
            campaign_id=harness.campaign.pk,
            configuration_id=limited.configuration_id,
            actor_id=actor,
            correlation_id=uuid4(),
            parameters=limited.parameters,
        )
    assert MinistryExportSnapshot.objects.count() == before
