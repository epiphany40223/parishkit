"""The exact daily export commands of ``pk-stewardship admin`` (ADM-11 PR 8h).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login, through the
Participation page's own exact-export services, with the real workers
calculating and rendering. A request made by the command is the page's: the
page's form with the same key returns it, and its retry, cancel, handoff and
file are the page's.
"""

from uuid import UUID, uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent
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

from .test_admin_export_cli_postgresql import download, read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_workspace_postgresql import http_scenario  # noqa: F401
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def count(event_type):
    """How many ``event_type`` events have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def test_an_exact_export_from_request_to_file(
    http_scenario,  # noqa: F811
    cli,  # noqa: F811
    settings,
    tmp_path,
):
    """Create, repeat, retry, hand off, render and fetch, as the page does."""
    setup, browser = http_scenario
    store, _, facts, _ = setup
    run = cli()
    key = uuid4()
    create = (
        "export",
        "exact",
        "create",
        "--scope",
        "current",
        "--format",
        "csv",
        "--timezone",
        "UTC",
        "--request-key",
        str(key),
    )
    code, document = run(*create)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["exact"]["state"] == "queued"
    job = ExactExportRequest.objects.get(request_key=key)
    assert result["exact"]["id"] == str(job.pk)
    assert job.campaign_id == facts.campaign_id
    assert count("export_requested") == 1
    assert count("admin_cmd_export_exact_create") == 1
    # The same key from the command or the page's form is the same request.
    code, document = run(*create)
    assert code == 0 and document["result"]["created"] is False
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = post(
            browser,
            reverse("admin:report_exact_create"),
            {
                "population_scope": "current",
                "format": "csv",
                "browser_timezone": "UTC",
                "request_key": str(key),
            },
        )
    assert response.status_code == 302 and str(job.pk) in response["Location"]
    assert ExactExportRequest.objects.count() == 1
    assert (
        count("export_requested") == 1 and count("admin_cmd_export_exact_create") == 1
    )
    # Another selection with that key is the page's 409: invalid.
    code, document = run(
        *create[:-5], "pdf", "--timezone", "UTC", "--request-key", str(key)
    )
    assert code == 1 and document["error"]["code"] == "invalid"

    # The calculation fails; a retry by key, repeated, makes one retry.
    act(act(_status(job.task), "claim"), "permanent_failure")
    code, document = run("export", "exact", "status", str(job.pk))
    assert code == 0 and document["result"]["state"] == "failed"
    retry_key = uuid4()
    for created in (True, False):
        code, document = run(
            "export", "exact", "retry", str(job.pk), "--request-key", str(retry_key)
        )
        assert code == 0 and document["result"]["created"] is created, document
    retry = job.task.chain_runs.get(retry_command_id=retry_key)
    assert count("admin_cmd_export_exact_retry") == 1

    # The worker calculates and hands off; the status names the export.
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            retry.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_exact_export": exact_handler(store=store)},
        )
    resolution = ExactExportResolution.objects.select_related("export").get(request=job)
    code, document = run("export", "exact", "status", str(job.pk))
    assert code == 0 and document["result"]["export_id"] == str(resolution.export_id)
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
    # A read-only session follows it to the end and finds it ready.
    reader = read_only_session(run.service)
    code, document = run(
        "export", "exact", "status", str(job.pk), "--watch", "2", secret=reader
    )
    assert code == 0 and document["final"]
    assert document["result"]["state"] == "ready"
    export_id = UUID(document["result"]["export_id"])
    code, body, streamed = download(export_id, run.secret)
    assert code == 0 and len(body) == streamed["result"]["size"] > 0
    # A published export cannot be cancelled: the page's 409.
    code, document = run("export", "exact", "cancel", str(job.pk))
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # A read-only session cannot change one.
    code, document = run(*create[:-1], str(uuid4()), secret=reader)
    assert code == 1 and document["error"]["code"] == "denied"


def test_cancelling_an_exact_export_before_handoff(
    http_scenario,  # noqa: F811
    cli,  # noqa: F811
):
    """The page's Cancel once; a second cancel changes nothing."""
    run = cli()
    code, document = run(
        "export", "exact", "create", "--format", "png", "--timezone", "UTC"
    )
    assert code == 0, document
    exact_id = document["result"]["exact"]["id"]
    assert document["result"]["exact"]["population_scope"] == "historical"
    for created in (True, False):
        code, document = run("export", "exact", "cancel", exact_id)
        assert code == 0 and document["result"]["created"] is created, document
        assert document["result"]["exact"]["state"] == "cancelled"
    assert count("export_cancelled") == 1
    assert count("admin_cmd_export_exact_cancel") == 1
    # A cancelled request cannot be retried: stale_version.
    code, document = run("export", "exact", "retry", exact_id)
    assert code == 1 and document["error"]["code"] == "stale_version", document
    # An unknown request is not_available.
    code, document = run("export", "exact", "status", str(uuid4()))
    assert code == 1 and document["error"]["code"] == "not_available"


def test_retry_and_cancel_after_the_handoff(
    http_scenario,  # noqa: F811
    cli,  # noqa: F811
):
    """After the handoff a retry and a cancel are the downstream export's."""
    setup, _browser = http_scenario
    store = setup[0]
    run = cli()
    code, document = run(
        "export", "exact", "create", "--format", "csv", "--timezone", "UTC"
    )
    assert code == 0, document
    exact_id = document["result"]["exact"]["id"]
    job = ExactExportRequest.objects.get(pk=exact_id)
    # The calculation fails once and is retried with key K, then hands off.
    act(act(_status(job.task), "claim"), "permanent_failure")
    before = uuid4()
    retry = ("export", "exact", "retry", exact_id, "--request-key")
    code, document = run(*retry, str(before))
    assert code == 0 and document["result"]["created"], document
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            job.task.chain_runs.get(retry_command_id=before).pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_exact_export": exact_handler(store=store)},
        )
    resolution = ExactExportResolution.objects.select_related("export__task").get(
        request=job
    )
    downstream = resolution.export.task
    assert count("admin_cmd_export_exact_retry") == 1
    # K replayed across the handoff is a new downstream retry, which the
    # queued export refuses (the page's 409); nothing is recorded.
    code, document = run(*retry, str(before))
    assert code == 1 and document["error"]["code"] == "stale_version", document
    assert count("admin_cmd_export_exact_retry") == 1
    assert not downstream.chain_runs.filter(retry_command_id=before).exists()
    # The render fails. K is a new retry of the export now, not a repeat of
    # the calculation's: it retries the export once, recording the event.
    act(act(_status(downstream), "claim"), "permanent_failure")
    for created in (True, False):
        code, document = run(*retry, str(before))
        assert code == 0 and document["result"]["created"] is created, document
    assert downstream.chain_runs.filter(retry_command_id=before).count() == 1
    assert count("admin_cmd_export_exact_retry") == 2
    # Cancelled after the handoff, before publication: the export's Cancel.
    for created in (True, False):
        code, document = run("export", "exact", "cancel", exact_id)
        assert code == 0 and document["result"]["created"] is created, document
        assert document["result"]["exact"]["state"] == "cancelled"
    assert count("admin_cmd_export_exact_cancel") == 1
    # The exact id is not an export id.
    code, document = run("export", "status", exact_id)
    assert code == 1 and document["error"]["code"] == "not_available"


def test_unknown_requests_and_missing_inputs_are_not_available(
    http_scenario,  # noqa: F811
    cli,  # noqa: F811
    monkeypatch,
):
    """An unknown id, or no current inputs to freeze, is not_available."""
    run = cli()
    unknown = str(uuid4())
    for argv in (
        ("export", "exact", "status", unknown),
        ("export", "exact", "cancel", unknown),
        ("export", "exact", "retry", unknown),
    ):
        code, document = run(*argv)
        assert code == 1 and document["error"]["code"] == "not_available", argv
    monkeypatch.setattr(
        "parishkit.stewardship.reports.exact_services.current_inputs",
        lambda campaign_id, scope: (None, None),
    )
    code, document = run(
        "export", "exact", "create", "--format", "csv", "--timezone", "UTC"
    )
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert not ExactExportRequest.objects.exists()
    assert count("admin_cmd_export_exact_create") == 0
