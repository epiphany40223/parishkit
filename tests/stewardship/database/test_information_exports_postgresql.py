"""Real-role complete information captures, immutable inputs and export ownership."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.export_models import (
    ExportRequest,
    InformationExportSnapshot,
)
from parishkit.stewardship.reports.export_services import TASK_TYPE, export_status
from parishkit.stewardship.reports.export_tasks import export_handler
from parishkit.stewardship.reports.information import InformationQuery, information_page
from parishkit.stewardship.reports.information_exports import create_information_export
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import AdditionalInformationItem

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post, restricted_download_pool
from .test_information_followup_postgresql import search
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def test_complete_capture_is_not_the_interactive_page(live_response_service):
    """Fifty-one real response versions prove the export does not inherit LIMIT 50."""
    harness = live_response_service
    respond(harness, "Version 0")
    for version in range(1, 51):
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = f"Version {version}"
        submit(harness, form, answers)
    actor = user("admin@example.org").pk
    query = InformationQuery(disposition="all", sort="oldest")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = information_page(harness.campaign.pk, query)
        assert page["total"] == 51 and len(page["rows"]) == 50
        snapshot = create_information_export(
            harness.service.store,
            actor,
            campaign_id=harness.campaign.pk,
            query=query,
            history=False,
            format="csv",
            browser_timezone="UTC",
            request_key=uuid4(),
        ).information_snapshot
        assert snapshot.row_count == len(snapshot.document["rows"]) == 51
        assert {row["text"] for row in snapshot.document["rows"]} == {
            f"Version {version}" for version in range(51)
        }
        assert [row["id"] for row in snapshot.document["rows"][:50]] == [
            row["id"] for row in page["rows"]
        ]
        assert (
            sum(row["disposition"] == "superseded" for row in snapshot.document["rows"])
            == 50
        )


def test_information_capture_freezes_full_text_and_history(live_response_service):
    """WEB captures shared-query results once; later edits cannot rewrite them."""
    harness = live_response_service
    submission = respond(harness, "=Private request\n" + "Long text. " * 250)
    item = AdditionalInformationItem.objects.get(submission=submission)
    actor = user("admin@example.org").pk
    values = dict(
        campaign_id=harness.campaign.pk,
        query=InformationQuery(search="private"),
        history=True,
        format="csv",
        browser_timezone="America/Detroit",
        request_key=uuid4(),
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        revision = update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=item.version,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=False,
            confirm_clear=False,
            notes="First complete note",
        )
        report = information_page(harness.campaign.pk, values["query"])
        request = create_information_export(harness.service.store, actor, **values)
        snapshot = request.information_snapshot
        assert snapshot.row_count == report["total"] == 1
        assert str(snapshot.source_id) == report["metadata"]["source_id"]
        row = snapshot.document["rows"][0]
        assert row["text"] == item.text
        assert row["notes"] == "First complete note"
        assert row["history"][0]["version"] == revision.expected_version + 1
        assert row["history"][0]["notes"] == "First complete note"
        assert row["family_name"] == report["rows"][0]["family_name"]
        assert (
            create_information_export(harness.service.store, actor, **values).pk
            == request.pk
        )
        with pytest.raises(ValueError, match="already bound"):
            create_information_export(
                harness.service.store, actor, **(values | {"history": False})
            )
        update_information(
            harness.service.store,
            actor,
            item.pk,
            expected_version=revision.expected_version + 1,
            request_key=uuid4(),
            follow_up_needed=True,
            followed_up=True,
            confirm_clear=False,
            notes="New note after capture",
        )
        snapshot.refresh_from_db()
        assert snapshot.document["rows"][0] == row
        assert InformationExportSnapshot.objects.count() == 1
        for statement in (
            "UPDATE stewardship_information_export_snapshot SET document='{}'",
            "DELETE FROM stewardship_information_export_snapshot",
        ):
            with (
                pytest.raises(DatabaseError),
                work_transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        without = create_information_export(
            harness.service.store,
            actor,
            **(values | {"history": False, "request_key": uuid4()}),
        ).information_snapshot
        assert without.document["rows"][0]["history"] == []
        assert without.document["rows"][0]["notes"] == "New note after capture"
        for invalid in (
            {"format": "png"},
            {"history": "yes"},
            {"browser_timezone": "private-invalid-timezone"},
            {"request_key": "not-a-uuid"},
        ):
            with pytest.raises(ValueError):
                create_information_export(
                    harness.service.store, actor, **(values | invalid)
                )
        # Direct clients cannot insert arbitrary captured values or commit orphan
        # inputs: the DB owns the payload and requires its atomic request link.
        with pytest.raises(DatabaseError), work_transaction():
            forged = InformationExportSnapshot.objects.create(
                campaign_id=harness.campaign.pk,
                configuration_id=request.configuration_id,
                actor_id=actor,
                correlation_id=uuid4(),
                parameters=request.parameters,
                document={"rows": [{"text": "forged"}]},
            )
            forged.refresh_from_db()
            assert forged.document["rows"][0]["text"] == item.text
        assert InformationExportSnapshot.objects.count() == 2


def test_native_information_exports_use_real_worker_and_guarded_downloads(
    live_response_service,
    google,
    tmp_path,
    settings,
    monkeypatch,
):
    """One fixture proves three formats, native ownership and retained regeneration."""
    harness = live_response_service
    respond(harness, "Complete exported Family request")
    browser, login = signed_in()
    assert login.status_code == 302
    actor = user("admin@example.org").pk
    root = tmp_path / "information-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    route = f"/admin/reports/{harness.campaign.pk}/information/"
    query = InformationQuery()
    first = None
    for format in ("csv", "xlsx", "pdf"):
        fields = query.form_values() | dict(
            history="yes",
            format=format,
            browser_timezone="UTC",
            request_key=str(uuid4()),
        )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(browser, route)
            assert response.status_code == 200 and b"Queue complete export" in body
            assert browser.post(route + "export", fields).status_code == 403
            if format == "csv":
                assert browser.get(route + "export").status_code == 405
                for invalid in (
                    {"page": "2"},
                    {"history": "no"},
                    {"format": ["csv", "pdf"]},
                ):
                    rejected = post(browser, route + "export", fields | invalid)
                    assert rejected.status_code == 400
                    assert b"Complete exported Family request" not in rejected.content
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
            response, body = read(browser, job_route)
            assert (
                response.status_code == 200 and b"Additional-information export" in body
            )
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert execute_hint(
                request.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: export_handler(store=harness.service.store, root=root)
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
            assert "additional_information." + format in response["Content-Disposition"]
            if format == "csv":
                assert b"Complete exported Family request" in body
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = (
        "A later Family request must not replace the export"
    )
    submit(harness, form, answers)
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
        assert regenerated.information_snapshot_id == first.information_snapshot_id
        assert (
            regenerated.information_snapshot.document["rows"][0]["text"]
            == "Complete exported Family request"
        )
