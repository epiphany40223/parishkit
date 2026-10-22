"""Native exact exports reuse real policy, workers, retained facts and grants.

Since #1537 no page offers the latest-data export: it is requested with
``export exact create``, through ``exact_services.create_exact_export``,
which these tests call as the command does. Its status page and buttons
are the shared export page's.
"""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from uuid import UUID, uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.exact_models import (
    ExactExportRequest,
    ExactExportResolution,
)
from parishkit.stewardship.reports.exact_tasks import exact_handler
from parishkit.stewardship.reports.export_tasks import export_handler

from ..export_urls import export_action
from .test_background_grants_postgresql import task_login
from .test_export_cleanup_postgresql import expire_publication
from .test_export_views_postgresql import post
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_workspace_postgresql import http_scenario, read  # noqa: F401
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def request_exact(store, campaign_id, values, email="admin@example.org"):
    """Request a latest-data export as ``email``, as ``export exact create``
    does (#1537); the address of its status page."""
    from parishkit.stewardship.reports.exact_services import create_exact_export

    job = create_exact_export(
        store,
        PortalUser.objects.get(email=email).pk,
        campaign_id=campaign_id,
        population_scope=values["population_scope"],
        format=values["format"],
        browser_timezone=values["browser_timezone"],
        request_key=UUID(values["request_key"]),
    )
    return reverse("admin:report_export", args=[job.pk])


def test_native_exact_retry_handoff_and_expiry(
    http_scenario,
    monkeypatch,
    settings,
    tmp_path,  # noqa: F811
):
    """One prepared corpus covers the complete immutable-input requester flow."""
    setup, browser = http_scenario
    store, _, facts, _ = setup
    campaign = facts.campaign_id
    values = {
        "population_scope": "current",
        "format": "xlsx",
        "browser_timezone": "America/New_York",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for invalid in (
            values | {"population_scope": "all"},
            values | {"format": "docx"},
        ):
            with pytest.raises(ValueError):
                request_exact(store, campaign, invalid)
        assert not ExactExportRequest.objects.exists()
        status_path = request_exact(store, campaign, values)
        original_activity = list(
            PortalSession.objects.values_list("id", "last_activity_at", "version")
        )
        response, body = read(browser, status_path)
        assert response.status_code == 200
        # Still queued, so the page says why it waits instead (#340); the
        # scenario's running source probe (a task with no name of its own)
        # holds the general process; the report email's send has finished.
        assert b"Waiting for other background work to finish" in body
        assert b"Calculating the figures" not in body
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
            assert request_exact(store, campaign, values) == status_path
    failed = act(_status(job.task), "claim")
    act(failed, "permanent_failure")
    retry_key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert b"Retry export" in read(browser, status_path)[1]
        for _ in range(2):
            assert (
                post(
                    browser,
                    export_action(status_path, "retry"),
                    {"request_key": str(retry_key)},
                )["Location"]
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
    rendered_path = reverse("admin:report_export", args=[resolution.export_id])
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
        assert post(browser, export_action(status_path, "cancel")).status_code == 409
    # Exports are point-in-time (#1398): an expired one offers no Regenerate.
    expire_publication(resolution.export)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, expired = read(browser, rendered_path)
    # It has ended: the fixed "no longer available" page, 410.
    assert (
        response.status_code == 410
        and b"This export is no longer available." in expired
    )
    assert b"Regenerate" not in expired
    # A later request can reuse ready facts but still owns a distinct renderer.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        waiting = request_exact(store, campaign, values | {"request_key": str(uuid4())})
    pending = ExactExportRequest.objects.exclude(pk=job.pk).get()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            pending.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_exact_export": exact_handler(store=store)},
        )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, export_action(waiting, "cancel")).status_code == 302
        # A cancelled export has ended (#1398).
        response, body = read(browser, waiting)
        assert (
            response.status_code == 410
            and b"This export is no longer available." in body
        )
        # One still queued, whose page and commands the gate below holds.
        held = request_exact(store, campaign, values | {"request_key": str(uuid4())})

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
        assert b"Other campaign work" in read(browser, held)[1]
        with pytest.raises(PermissionError):
            request_exact(store, campaign, values | {"request_key": str(uuid4())})
        for endpoint, payload in (
            (export_action(held, "cancel"), {}),
            (export_action(held, "retry"), {"request_key": str(uuid4())}),
        ):
            assert post(browser, endpoint, payload).status_code == 403


def test_native_exact_cancel_and_other_requester_denial(
    http_scenario,
    google,  # noqa: F811
):
    """Independent requests retain ownership before and after worker handoff."""
    from .auth_builders import signed_in
    from .report_builders import set_user

    setup, admin = http_scenario
    store, _, facts, _ = setup
    # (The fixture is in the users form once a report email exists, #1786.)
    set_user(store, "staff@example.org", "staff")
    google[0]["email"] = "staff@example.org"
    google[0]["sub"] = "synthetic-staff-subject"
    browser, _ = signed_in()
    campaign = facts.campaign_id
    values = {
        "population_scope": "historical",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        owned = request_exact(store, campaign, values, "staff@example.org")
        other = request_exact(store, campaign, values | {"request_key": str(uuid4())})
        assert read(browser, other)[0].status_code == 403
        assert post(browser, export_action(other, "cancel")).status_code == 403
        assert (
            post(
                browser, export_action(other, "retry"), {"request_key": str(uuid4())}
            ).status_code
            == 403
        )
        assert post(browser, export_action(owned, "cancel")).status_code == 302
        response, body = read(browser, owned)
        assert (
            response.status_code == 410
            and b"This export is no longer available." in body
        )
        # Its Retry answers the same page; an Administrator, who may see
        # every export, gets it too.
        assert (
            post(
                browser, export_action(owned, "retry"), {"request_key": str(uuid4())}
            ).status_code
            == 410
        )
        assert read(admin, owned)[0].status_code == 410
        # Another requester's ended export stays a plain denial: the owner
        # check runs first, so it reveals nothing.
        assert post(admin, export_action(other, "cancel")).status_code == 302
        assert read(browser, other)[0].status_code == 403
        assert post(browser, export_action(other, "cancel")).status_code == 403
    set_user(store, "staff@example.org", "ministry_leader")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert read(browser, owned)[0].status_code == 403
        with pytest.raises(PermissionError):
            request_exact(store, campaign, values, "staff@example.org")
        assert post(browser, export_action(owned, "cancel")).status_code == 403
        assert (
            post(
                browser, export_action(owned, "retry"), {"request_key": str(uuid4())}
            ).status_code
            == 403
        )
