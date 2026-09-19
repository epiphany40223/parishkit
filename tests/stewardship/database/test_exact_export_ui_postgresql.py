"""Native exact exports reuse real policy, workers, retained facts and grants."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.exact_models import (
    ExactExportRequest,
    ExactExportResolution,
)
from parishkit.stewardship.reports.exact_tasks import exact_handler
from parishkit.stewardship.reports.export_models import ExportPublication, ExportRequest
from parishkit.stewardship.reports.export_tasks import export_handler

from .test_background_grants_postgresql import task_login
from .test_export_cleanup_postgresql import expire_publication
from .test_export_views_postgresql import post
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_workspace_postgresql import http_scenario, read  # noqa: F401
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def test_native_exact_retry_handoff_and_expired_regeneration(
    http_scenario,
    monkeypatch,
    settings,
    tmp_path,  # noqa: F811
):
    """One prepared corpus covers the complete immutable-input requester flow."""
    setup, browser = http_scenario
    store, _, facts, _ = setup
    path = f"/admin/reports/{facts.campaign_id}/participation/exact-export"
    values = {
        "population_scope": "current",
        "format": "xlsx",
        "browser_timezone": "America/New_York",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(path).status_code == 405
        assert browser.post(path, values).status_code == 403
        for invalid in (
            values | {"source_id": str(uuid4())},
            values | {"population_scope": "all"},
            values | {"browser_timezone": "bad-zone"},
            values | {"request_key": "bad-key"},
        ):
            assert post(browser, path, invalid).status_code == 400
        assert not ExactExportRequest.objects.exists()
        response = post(browser, path, values)
        assert response.status_code == 302
        status_path = response["Location"]
        assert response["Cache-Control"] == "no-store"
        original_activity = list(
            PortalSession.objects.values_list("id", "last_activity_at", "version")
        )
        response, body = read(browser, status_path)
        assert (
            response.status_code == 200 and b"Waiting for the exact calculation" in body
        )
        assert b"America/New_York" in body
        assert b"Cancel export" in body
        assert (
            list(PortalSession.objects.values_list("id", "last_activity_at", "version"))
            == original_activity
        )
        assert read(browser, status_path + "?private=value")[0].status_code == 400
    job = ExactExportRequest.objects.get(request_key=UUID(values["request_key"]))
    frozen = (job.source_id, job.submission_watermark, job.through_date)
    from parishkit.stewardship.reports import exact_services

    def no_recapture(*args):
        """Replay must not read new input values even if they could have changed."""
        raise AssertionError("Input recapture on replay")

    with monkeypatch.context() as patch:
        patch.setattr(exact_services, "current_inputs", no_recapture)
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert post(browser, path, values)["Location"] == status_path
    failed = act(_status(job.task), "claim")
    act(failed, "permanent_failure")
    retry_key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert b"Retry export" in read(browser, status_path)[1]
        for _ in range(2):
            assert (
                post(browser, status_path + "retry", {"request_key": str(retry_key)})[
                    "Location"
                ]
                == status_path
            )
    retry = job.task.chain_runs.get(retry_command_id=retry_key)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            retry.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_exact_export": exact_handler(store=store)},
        )
    resolution = ExactExportResolution.objects.select_related("export").get(request=job)
    assert (job.source_id, job.submission_watermark, job.through_date) == frozen
    rendered_path = f"/admin/reports/exports/{resolution.export_id}/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, status_path)
        assert response.status_code == 200 and rendered_path.encode() in body
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            resolution.export.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_export": export_handler(store=store, root=root)},
        )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert b"ready" in read(browser, status_path)[1]
        assert (
            post(
                browser, rendered_path + "regenerate", {"request_key": str(uuid4())}
            ).status_code
            == 409
        )
        assert post(browser, status_path + "cancel").status_code == 409
    expire_publication(resolution.export)
    publication = ExportPublication.objects.get(request=resolution.export)
    regeneration_key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, expired = read(browser, rendered_path)
        assert b"Regenerate expired file" in expired
        regenerated = post(
            browser,
            rendered_path + "regenerate",
            {"request_key": str(regeneration_key)},
        )
        assert regenerated.status_code == 302
        assert (
            post(
                browser,
                rendered_path + "regenerate",
                {"request_key": str(regeneration_key)},
            )["Location"]
            == regenerated["Location"]
        )
    replacement = ExportRequest.objects.get(request_key=regeneration_key)
    assert replacement.pk != resolution.export_id
    assert replacement.fact_set_id == resolution.fact_set_id
    assert replacement.format == job.format
    assert replacement.browser_timezone == job.browser_timezone
    assert replacement.created_at > resolution.export.created_at
    publication.refresh_from_db()
    assert publication.request_id == resolution.export_id
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            replacement.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_export": export_handler(store=store, root=root)},
        )
    assert ExportPublication.objects.filter(request=replacement).exists()
    # A later request can reuse ready facts but still owns a distinct renderer.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        waiting = post(browser, path, values | {"request_key": str(uuid4())})[
            "Location"
        ]
    pending = ExactExportRequest.objects.exclude(pk=job.pk).get()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            pending.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_exact_export": exact_handler(store=store)},
        )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, waiting + "cancel").status_code == 302
        assert b"cancelled" in read(browser, waiting)[1]

    from django.db import connection, transaction

    from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate

    # Install the later purge owner's sentinel only as the disposable schema
    # owner, then exercise ordinary WEB commands with every SQL guard enabled.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign_id=facts.campaign_id,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert b"Campaign work is gated" in read(browser, status_path)[1]
        for endpoint, payload in (
            (path, values | {"request_key": str(uuid4())}),
            (status_path + "cancel", {}),
            (status_path + "retry", {"request_key": str(uuid4())}),
            (rendered_path + "regenerate", {"request_key": str(uuid4())}),
        ):
            assert post(browser, endpoint, payload).status_code == 403


def test_native_exact_cancel_and_other_requester_denial(
    http_scenario,
    google,  # noqa: F811
):
    """Independent requests retain ownership before and after worker handoff."""
    from ..policy_factory import address
    from .auth_builders import signed_in
    from .campaign_builders import change

    setup, admin = http_scenario
    store, _, facts, _ = setup
    rule = address("staff@example.org", roles=("staff", "ministry_leader"))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "staff@example.org"
    google[0]["sub"] = "synthetic-staff-subject"
    browser, _ = signed_in()
    path = f"/admin/reports/{facts.campaign_id}/participation/exact-export"
    values = {
        "population_scope": "historical",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        owned = post(browser, path, values)["Location"]
        other = post(admin, path, values | {"request_key": str(uuid4())})["Location"]
        assert read(browser, other)[0].status_code == 403
        assert post(browser, other + "cancel").status_code == 403
        assert (
            post(browser, other + "retry", {"request_key": str(uuid4())}).status_code
            == 403
        )
        assert post(browser, owned + "cancel").status_code == 302
        assert b"cancelled" in read(browser, owned)[1]
        assert (
            post(browser, owned + "retry", {"request_key": str(uuid4())}).status_code
            == 409
        )
        assert read(admin, owned)[0].status_code == 200
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
        assert read(browser, owned)[0].status_code == 403
        assert post(browser, path, values).status_code == 403
        assert post(browser, owned + "cancel").status_code == 403
        assert (
            post(browser, owned + "retry", {"request_key": str(uuid4())}).status_code
            == 403
        )
