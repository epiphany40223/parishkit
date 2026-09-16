"""Admin cleanup recovery and uniform handling of private storage faults."""

import re
import socket
from uuid import uuid4

import pytest

from .test_export_jobs_postgresql import run_export, scenario  # noqa: F401
from .test_export_views_postgresql import create, http_scenario, post  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "endpoint", ["create", "status", "cancel", "grant", "download", "cleanup"]
)
def test_configuration_fault_is_uniform_denial_not_invalid_input(
    http_scenario,  # noqa: F811
    monkeypatch,
    endpoint,
):
    """ConfigError is a ValueError subtype, but never a caller parse failure."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.reports import export_views

    setup, browser = http_scenario

    def unavailable(*args, **kwargs):
        """A private configuration detail must not escape the shared denial."""
        raise ConfigError("synthetic-private-configuration-value")

    monkeypatch.setattr(export_views, "_principal", unavailable)
    identifier = uuid4()
    paths = {
        "create": f"/admin/campaign/{setup[2].campaign_id}/exports/participation",
        "status": f"/admin/exports/{identifier}",
        "cancel": f"/admin/exports/{identifier}/cancel",
        "grant": f"/admin/exports/{identifier}/download-grant",
        "download": "/admin/exports/download",
        "cleanup": f"/admin/background/tasks/{identifier}/retry-export-cleanup",
    }
    response = (
        browser.get(paths[endpoint])
        if endpoint == "status"
        else post(
            browser,
            paths[endpoint],
            {"request_key": str(uuid4())} if endpoint == "cleanup" else {},
        )
    )
    assert response.status_code == 403
    assert b"synthetic-private" not in response.content


def test_missing_artifact_root_is_denied_after_real_grant(http_scenario, settings):  # noqa: F811
    """An authentic grant does not turn a storage fault into a caller error."""
    setup, browser = http_scenario
    request = create(http_scenario)
    run_export(setup, request)
    grant = post(browser, f"/admin/exports/{request.pk}/download-grant").json()["grant"]
    settings.STEWARDSHIP_REPORTS_ROOT = None
    server, peer = socket.socketpair()
    try:
        response = post(
            browser,
            "/admin/exports/download",
            {"grant": grant},
            **{"gunicorn.socket": server},
        )
        assert response.status_code == 403
    finally:
        server.close()
        peer.close()


def failed_cleanup(setup_and_browser):
    """Create a real expired artifact and a journaled failed cleanup task."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.reports.export_cleanup import produce_cleanup

    from .test_export_cleanup_postgresql import expire_publication
    from .test_taskrun_postgresql import act

    setup, _ = setup_and_browser
    request = create(setup_and_browser)
    run_export(setup, request)
    publication = expire_publication(request)
    with scheduler_session() as guard:
        task = produce_cleanup(guard)[0]
    with work_transaction():
        act(act(task, "claim"), "permanent_failure")
    return task, publication


def test_admin_cleanup_retry_form_csrf_replay_and_restricted_sql(http_scenario):  # noqa: F811
    """The operational detail page provides a usable, narrow recovery action."""
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.reports.export_cleanup import TASK_TYPE, cleanup_handler
    from parishkit.stewardship.reports.export_models import ExportArtifactCleanup

    setup, browser = http_scenario
    task, publication = failed_cleanup(http_scenario)
    path = f"/admin/background/tasks/{task.run_id}/retry-export-cleanup"
    with web_login():
        page = browser.get(f"/admin/background/task/{task.run_id}")
        assert page.status_code == 200
        key = re.search(
            r'name="request_key" value="([a-f0-9-]+)"', page.content.decode()
        )[1]
        assert browser.get(path).status_code == 405
        assert browser.post(path, {"request_key": key}).status_code == 403
        invalid = post(browser, path, {"request_key": "not-a-uuid"})
        assert invalid.status_code == 400
        assert invalid["Content-Type"].startswith("text/html")
        assert invalid["Cache-Control"] == "no-store"
        assert b"Return to task details" in invalid.content
        response = post(browser, path, {"request_key": key})
        assert response.status_code == 302
        assert (
            post(browser, path, {"request_key": key})["Location"]
            == response["Location"]
        )
        conflict = post(browser, path, {"request_key": str(uuid4())})
        assert conflict.status_code == 409
        assert conflict["Content-Type"].startswith("text/html")
        assert b"Return to task details" in conflict.content
        stale = browser.get(f"/admin/background/task/{task.run_id}")
        assert b'name="request_key"' not in stale.content
        assert b"View the latest retry" in stale.content
    child = TaskRun.objects.get(parent_id=task.run_id)
    assert response["Location"] == f"/admin/background/task/{child.pk}"
    assert execute_hint(
        child.pk,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: cleanup_handler(setup[3])},
    )
    assert ExportArtifactCleanup.objects.get().attempt_id == publication.attempt_id


def test_staff_cannot_view_or_issue_operational_cleanup_retry(http_scenario, google):  # noqa: F811
    """Ordinary report access never grants the Admin-only destructive owner port."""
    from ..policy_factory import address
    from .auth_builders import signed_in
    from .test_export_authorization_postgresql import add_policy

    setup, _ = http_scenario
    task, _ = failed_cleanup(http_scenario)
    add_policy(setup, address("staff@example.org", ("staff",)))
    google[0].update(email="staff@example.org", sub="synthetic-export-staff")
    browser, response = signed_in()
    assert response.status_code == 302
    identifier = task.run_id
    assert browser.get(f"/admin/background/task/{identifier}").status_code == 403
    response = post(
        browser,
        f"/admin/background/tasks/{identifier}/retry-export-cleanup",
        {"request_key": str(uuid4())},
    )
    assert response.status_code == 403
    # Authorization also precedes malformed-field diagnostics.
    assert (
        post(
            browser, f"/admin/background/tasks/{identifier}/retry-export-cleanup"
        ).status_code
        == 403
    )


def test_cleanup_retry_cannot_select_a_different_attempt_root(http_scenario):  # noqa: F811
    """An explicit run ID cannot silently redirect a canonical retry command."""
    from parishkit.stewardship.reports.export_cleanup import retry_cleanup

    setup, _ = http_scenario
    _, first = failed_cleanup(http_scenario)
    second, _ = failed_cleanup(http_scenario)
    with web_login(), pytest.raises(PermissionError, match="cleanup root"):
        retry_cleanup(
            setup[0],
            setup[1].pk,
            first.attempt_id,
            request_key=uuid4(),
            run_id=second.run_id,
        )
