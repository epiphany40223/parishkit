"""An ended report export answers the fixed "no longer available" page (#1398).

Exports are point-in-time (operations spec, "Point-in-time exports"; UX
conventions, "Point-in-time downloads"): once a report export's window is
over, or it was cancelled, its status page, Download, Cancel and Retry all
answer one fixed page, 410 Gone, linking back to the report. The owner and
capability check comes first, so another reader's ended export is still the
plain denial and reveals nothing.
"""

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import ExportRequest

from ..export_urls import export_action
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_workspace_postgresql import http_scenario, read  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

GONE = b"This export is no longer available."


def _create(browser, facts):
    """Queue one Participation chart export from the page's form; its page."""
    values = {
        "fact_set_id": str(facts.pk),
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = post(browser, reverse("admin:report_export_create"), values)
    assert response.status_code == 302, response
    return response["Location"], ExportRequest.objects.get(
        request_key=values["request_key"]
    )


def _back(facts):
    """The page's link back: Participation, with the export's own scope."""
    from parishkit.stewardship.reports.workspace import ReportQuery

    url = ReportQuery(scope=facts.population_scope, timezone="UTC").url()
    return url.replace("&", "&amp;")


def _commands(browser, page):
    """Each command's answer on the export at ``page``: {action: response}."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return {
            "cancel": post(browser, export_action(page, "cancel")),
            "retry": post(
                browser, export_action(page, "retry"), {"request_key": str(uuid4())}
            ),
            "download": post(browser, export_action(page, "download")),
        }


def _assert_gone(browser, page, back):
    """The status page and every command answer the 410 page linking to
    ``back``, and none of them changed anything."""
    before = ExportRequest.objects.count()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, page)
    assert response.status_code == 410 and GONE in body
    assert f'href="{back}"'.encode() in body
    assert b"Download export" not in body and b"Regenerate" not in body
    for action, answer in _commands(browser, page).items():
        assert answer.status_code == 410 and GONE in answer.content, action
    assert ExportRequest.objects.count() == before


def test_an_ended_report_export_is_gone_everywhere(
    http_scenario,  # noqa: F811
    google,
    settings,
    tmp_path,
):
    """Expired and cancelled exports answer 410 on every route; another
    reader's ended export stays a plain 403 on every route."""
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.reports.export_tasks import export_handler

    from ..policy_factory import address
    from .auth_builders import signed_in
    from .campaign_builders import change
    from .test_export_cleanup_postgresql import expire_publication

    setup, admin = http_scenario
    store, _, facts, _ = setup
    root = tmp_path / "gone-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    back = _back(facts)

    # An expired file: its window is over.
    expired_page, expired = _create(admin, facts)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            expired.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_export": export_handler(store=store, root=root)},
        )
    expire_publication(expired)
    _assert_gone(admin, expired_page, back)

    # A cancelled one, before any file was made.
    cancelled_page, _ = _create(admin, facts)
    assert _commands(admin, cancelled_page)["cancel"].status_code == 302
    _assert_gone(admin, cancelled_page, back)

    # Another reader (Staff, who sees only their own exports) gets the
    # denial, never the 410 page or its report.
    rule = address("staff@example.org", roles=("staff",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff-subject")
    staff, _ = signed_in()
    for page in (expired_page, cancelled_page):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(staff, page)
        assert response.status_code == 403 and GONE not in body
        for action, answer in _commands(staff, page).items():
            assert answer.status_code == 403, action
            assert GONE not in answer.content, action


def test_the_window_ends_a_file_and_a_failed_exports_retry(
    http_scenario,  # noqa: F811
    settings,
    tmp_path,
    monkeypatch,
):
    """A file is published for one window (the deployment's setting) and its
    status page says until when; a shorter setting ends it at once. A failed
    export offers Retry for one window after its failure, then is the 410
    page, and the command line's retry is ``not_available`` (#1398)."""
    from datetime import timedelta

    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports import export_services
    from parishkit.stewardship.reports.export_models import ExportPublication
    from parishkit.stewardship.reports.export_tasks import export_handler

    from .test_taskrun_postgresql import act

    setup, admin = http_scenario
    store, _, facts, _ = setup
    root = tmp_path / "window-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    page, job = _create(admin, facts)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            job.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_export": export_handler(store=store, root=root)},
        )
    publication = ExportPublication.objects.get(request=job)
    # Published for the default 60 minutes, not seven days.
    lifetime = publication.expires_at - publication.created_at
    assert timedelta(minutes=59) < lifetime <= timedelta(minutes=61)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(admin, page)
    end = publication.expires_at.isoformat()
    assert response.status_code == 200 and b"Ready to download until" in body
    assert f'datetime="{end}"'.encode() in body
    # A grant issued inside the window, then twenty minutes on a 15-minute
    # setting has ended the file though its record says 60: the page, a new
    # grant and the issued grant's use are all refused.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        grant = export_services.issue_download(store, job.requester_id, job.pk)
    later = publication.created_at + timedelta(minutes=20)
    monkeypatch.setattr(export_services, "database_now", lambda: later)
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 15
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(admin, page)
        assert response.status_code == 410 and GONE in body
        with pytest.raises(export_services.ExportExpired):
            export_services.issue_download(store, job.requester_id, job.pk)
        with pytest.raises(export_services.ExportExpired):
            export_services.consume_download(store, job.requester_id, grant.pk)
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 60
    monkeypatch.undo()

    # A failed export: Retry is offered within the window, gone after it.
    failed_page, failed = _create(admin, facts)
    act(act(_status(failed.task), "claim"), "permanent_failure")
    latest = failed.task.chain_runs.order_by("-retry_sequence").first()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(admin, failed_page)
    assert response.status_code == 200 and b"Retry export" in body
    # It says until when, the end of its Retry window.
    retry_until = (latest.updated_at + timedelta(minutes=60)).isoformat()
    assert b"Retry is available until" in body
    assert f'datetime="{retry_until}"'.encode() in body
    past = latest.updated_at + timedelta(minutes=61)
    monkeypatch.setattr(export_services, "database_now", lambda: past)
    _assert_gone(admin, failed_page, _back(facts))
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(export_services.ExportExpired),
    ):
        export_services.retry_export(
            store, failed.requester_id, failed.pk, request_key=uuid4()
        )


def test_a_latest_data_exports_failed_calculation_ends_too(
    http_scenario,  # noqa: F811
    monkeypatch,
):
    """A latest-data export whose calculation failed offers Retry for one
    window; then it is the 410 page and a new Retry is refused, while a
    recorded key still replays (#1398)."""
    from datetime import timedelta

    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports import exact_services, export_services
    from parishkit.stewardship.reports.exact_models import ExactExportRequest

    from .test_taskrun_postgresql import act

    setup, admin = http_scenario
    store = setup[0]
    values = {
        "population_scope": "current",
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = post(admin, reverse("admin:report_exact_create"), values)["Location"]
    job = ExactExportRequest.objects.get(request_key=values["request_key"])
    act(act(_status(job.task), "claim"), "permanent_failure")
    key = uuid4()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(admin, page)
        assert response.status_code == 200 and b"Retry is available until" in body
        exact_services.retry_exact_export(
            store, job.requester_id, job.pk, request_key=key
        )
    retried = job.task.chain_runs.get(retry_command_id=key)
    act(act(_status(retried), "claim"), "permanent_failure")
    retried.refresh_from_db()
    later = retried.updated_at + timedelta(minutes=61)
    monkeypatch.setattr(export_services, "database_now", lambda: later)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(admin, page)
        assert response.status_code == 410 and GONE in body
        with pytest.raises(export_services.ExportExpired):
            exact_services.retry_exact_export(
                store, job.requester_id, job.pk, request_key=uuid4()
            )
        # The recorded key replays its own retry rather than a refusal.
        replay = exact_services.retry_exact_export(
            store, job.requester_id, job.pk, request_key=key
        )
    assert replay.run_id == retried.pk
