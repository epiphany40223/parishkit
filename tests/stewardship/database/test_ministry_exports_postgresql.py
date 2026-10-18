"""Complete Ministry capture and actual-role requester/worker/download boundaries."""

import io
import json
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.audit.models import AuditContext
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

from ..export_urls import export_action
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post, restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_reports_postgresql import drop_leader, page, setup
from .test_ministry_responses_postgresql import configure, ministry_source, respond
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read
from .test_response_http_postgresql import answers_for, load_form
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def leader(harness, google, *, roles=("ministry_leader",)):
    """Sign in leader@example.org, whom setup() made a ParishSoft leader of
    Food pantry (9) (#922), through an exact-address rule with ``roles``.

    Returns the browser, the portal user, that rule and None, where the
    manually assigned Ministry was before Ministry leaders' Ministries came
    from ParishSoft.
    """
    rule = address("leader@example.org", roles=roles)
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "leader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    actor = PortalUser.objects.get(google_subject=google[0]["sub"]).pk
    return browser, actor, rule, None


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
    """SQL owns privacy capture; another owner or a lost role cannot reuse it."""
    harness = setup(response_service)
    _, actor, _, _ = leader(harness, google)
    request = create(harness, actor)
    snapshot = MinistryExportSnapshot.objects.get(pk=request.ministry_snapshot_id)
    assert snapshot.row_count == 1
    assert snapshot.authorization_scope == {
        "capability": "ministry_report",
        "operational": False,
        "ministries": [9],
        "result_ministries": [9],
    }
    recorded = AuditContext.objects.get(
        event__event_type="export_requested",
        event__campaign_reference=harness.campaign.pk,
    )
    assert recorded.event.subject_id == request.pk
    assert recorded.context["ministry_duids"] == [9]
    assert recorded.context["ministry_operational"] is False
    # The Member asked to join, so even the unpublished email is in the
    # leader's capture (#1591); the birth date never is.
    assert "valid@example.org" in json.dumps(snapshot.document)
    assert "202-555-0123" in json.dumps(snapshot.document)
    assert "1960-01-01" not in json.dumps(snapshot.document)
    with pytest.raises(PermissionError):
        create(harness, actor, ministry_id=4, action="leave")
    # Another leader of the same Ministry is still not the export's owner.
    other = user("leader-9@example.org").pk
    store = harness.service.store
    admin = user("admin@example.org").pk
    leaving = create(harness, admin, ministry_id=4, action="leave")
    # The campaign-wide summary export was removed (#1126): neither a
    # summary action nor a missing Ministry is accepted.
    for removed in (
        {"action": "summary", "ministry_id": None},
        {"action": "summary"},
        {"ministry_id": None},
    ):
        with pytest.raises(ValueError):
            create(harness, admin, **removed)
    # A list keeps a Member DUID sort (#960), accepted by the capture.
    listed = create(harness, admin, query=MinistryQuery(sort="duid"))
    listed = MinistryExportSnapshot.objects.get(pk=listed.ministry_snapshot_id)
    assert listed.row_count == 1 and listed.parameters["filters"]["sort"] == "duid"
    leaving.refresh_from_db()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        capture = leaving.ministry_snapshot.document
        assert capture["rows"][0]["current_role"] == "Chairperson"
        assert capture["rows"][0]["emails"] is None
        document = load_document(leaving)
        role = document.headings.index("Current Ministry role")
        assert document.rows[0][role] == "Chairperson"
        # The list's Ministry heads the file, not a column (#932), by name
        # alone (#1123).
        assert dict(document.metadata)["Ministry"] == capture["summaries"][0]["name"]
        assert "Ministry DUID" not in dict(document.metadata)
        # No Assignee column since follow-up assignment was removed (#552).
        assert len(document.headings) == 6 and "Assignee" not in document.headings
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
    drop_leader(harness)
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
    # Query audit-side evidence without a request handle or join to campaign
    # detail. The retained context itself proves attribution after detail purge.
    events = list(
        AuditContext.objects.filter(
            event__event_type="export_requested",
            event__campaign_reference=harness.campaign.pk,
        ).select_related("event")
    )
    assert {event.event.subject_id: event.context for event in events} == {
        request.pk: {
            "outcome": "started",
            "count": count,
            "ministry_duids": [9],
            "ministry_operational": True,
            "report": "ministry",
            "format": request.format,
        }
        for request, count in ((result, 52), (empty, 0))
    }
    before = retained.document
    data.members[3]["firstName"] = "Changed after capture"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    retained.refresh_from_db()
    assert retained.document == before
    assert page(harness, ministry=9)["metadata"]["source_id"] != str(retained.source_id)


def test_native_leader_exports_worker_and_download(
    response_service,
    google,
    settings,
    tmp_path,
    monkeypatch,
):
    """Exercise real session, all formats, publication and scoped bytes."""
    harness = setup(response_service)
    browser, actor, _, _ = leader(harness, google)
    root = tmp_path / "ministry-exports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    first = None
    # One Ministry's joining export is no longer offered on a page (#1157):
    # the host command still captures one, through the
    # same service, so it is created here as the command does.
    for format in ("csv", "xlsx", "pdf"):
        key = uuid4()
        # Read back: the capture's document is written by its SQL trigger.
        result = ExportRequest.objects.get(
            pk=create(harness, actor, format=format, request_key=key).pk
        )
        first = first or result
        # The same key is the same export, never a second one.
        assert create(harness, actor, format=format, request_key=key).pk == result.pk
        job_route = reverse("admin:report_export", args=[result.pk])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(browser, job_route)
            assert response.status_code == 200 and b"Ministry requests export" in body
            # The parent crumb is the way back; no "Return to" repeats it
            # (#1345).
            assert b"Return to " not in body
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            document = load_document(result)
            assert "valid@example.org" in str(document.rows)
            assert execute_hint(
                result.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: export_handler(store=harness.service.store, root=root)
                },
            )
        with restricted_download_pool(settings):
            response, body = search(browser, export_action(job_route, "download"), {})
            assert response.status_code == 200
            assert body.startswith(
                {"csv": b"Member,", "xlsx": b"PK", "pdf": b"%PDF"}[format]
            )
            if format == "csv":
                assert b"Not published" not in body and b"valid@example.org" in body
                assert b"1960-01-01" not in body and b"+1 (202) 555-0123" in body
    # Once its window is over, the leader's own export is the fixed 410 page,
    # linking back to Ministry requests (#1398).
    from .test_export_cleanup_postgresql import expire_publication

    expire_publication(result)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, job_route)
    assert response.status_code == 410
    assert b"This export is no longer available." in body
    assert f'href="{reverse("admin:ministry_report")}"'.encode() in body
    # One more export, still queued when the leader loses the Ministry.
    pending = create(harness, actor, format="csv", request_key=uuid4())
    store = harness.service.store
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        grant = issue_download(store, actor, first.pk)
    drop_leader(harness)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert read(browser, job_route)[0].status_code == 403
        assert (
            post(browser, export_action(job_route, "download"), {}).status_code == 403
        )
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
            pending.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: export_handler(store=store, root=root)},
        )


def test_operational_capture_cannot_survive_staff_downgrade(
    response_service,
    google,
    tmp_path,
):
    """Still leading the same Ministry does not retain operational columns."""
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
            # Staff goes; the rule's Ministry leader role stays, and the
            # leader keeps leading Food pantry through ParishSoft.
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
