"""The export lifecycle commands of ``pk-stewardship admin`` (ADM-11 PR 8b).

Process admission is replaced by the test's own Django assembly (the runner
of test_admin_task_retry_cli_postgresql.py); everything after it is the
real command line on the restricted web login, requesting, following,
cancelling, retrying, regenerating and downloading real exports through the
pages' own functions, with the real worker rendering the files. An export
made on a page is followed and downloaded from the command line and the
other way round; both record the same events with the same contexts, and
both downloads are the same bytes.
"""

import hashlib
import io
import json
import socket
from uuid import UUID, uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.export_models import ExportRequest

from .test_admin_status_cli_postgresql import without_web_runtimes
from .test_admin_task_retry_cli_postgresql import approved_by_browser, runner
from .test_background_grants_postgresql import task_login
from .test_export_cleanup_postgresql import expire_publication
from .test_export_jobs_postgresql import run_export, scenario  # noqa: F401
from .test_export_views_postgresql import (  # noqa: F401
    create,
    http_scenario,
    post,
    restricted_download_pool,
)
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def cli(http_scenario, settings, monkeypatch):  # noqa: F811
    """The command line over the export scenario, with a full-scope session.

    Returns (run, secret): ``run(*argv, secret=...)`` gives (exit code,
    document); the session was approved from the scenario's signed-in
    browser, so the command line acts as the page's own Administrator.
    """
    admin = runner(settings.STEWARDSHIP_AUTH_RUNTIME, monkeypatch)

    def run(*argv, secret):
        """One command that prints exactly one document."""
        code, document, _ = admin(*argv, secret=secret)
        return code, document

    run.service = admin.service
    return run, approved_by_browser(admin.service)


def stream(*argv, secret):
    """``export download --stream``: (exit code, standard output bytes, document).

    The document is the one line on standard error; standard output holds
    the file alone.
    """
    raw, err = io.BytesIO(), io.StringIO()
    stdout = io.TextIOWrapper(raw, encoding="utf-8")
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        without_web_runtimes(),
    ):
        code = admin_cli.main(
            [*argv, "--config", "web.yaml", "--session-stdin"],
            stdin=io.BytesIO(f"pk-admin-session/1 {secret} {'a' * 64}\n".encode()),
            stdout=stdout,
            stderr=err,
        )
    stdout.flush()
    [document] = [
        json.loads(line) for line in err.getvalue().splitlines() if '"schema"' in line
    ]
    return code, raw.getvalue(), document


def download(export_id, secret):
    """``export download EXPORT_ID --stream`` for this session."""
    return stream("export", "download", str(export_id), "--stream", secret=secret)


def page_download(http_scenario, settings, request):  # noqa: F811
    """The export page's download of ``request``: its streamed bytes.

    The page's Download action issues and consumes its one-use grant in one
    post, as ``export download`` does.
    """
    _, browser = http_scenario
    download = reverse("admin:report_export_download", args=[request.pk])
    server, peer = socket.socketpair()
    try:
        with restricted_download_pool(settings):
            response = post(browser, download, **{"gunicorn.socket": server})
            assert response.status_code == 200
            body = b"".join(response.streaming_content)
            response.close()
        return body
    finally:
        server.close()
        peer.close()


def read_only_session(service):
    """A read-only session approved from the scenario's signed-in browser.

    As ``approved_by_browser``, whose approval this is with the lower scope.
    """
    from django.db import transaction

    from parishkit.stewardship.accounts.models import PortalSession

    from .automation_builders import pairing, start

    secret, code = start(service, scope="read-only")
    portal = PortalSession.objects.get(revoked_at__isnull=True)
    principal = current_principal(service.store, portal.principal_id)
    with transaction.atomic():
        automation.approve(
            principal,
            portal,
            pairing(service).request(code),
            scope="read_only",
            days=30,
        )
    return secret


def contexts(event_type):
    """The stored context of each event of this type, oldest first."""
    return [
        (event.actor_id, event.subject_id, event.campaign_reference, context)
        for event, context in (
            (row.event, row.context)
            for row in AuditContext.objects.filter(event__event_type=event_type)
            .select_related("event")
            .order_by("event__created_at", "event__id")
        )
    ]


def commands(verb):
    """The ``admin_cmd_export_<verb>`` events recorded so far."""
    return list(AuditEvent.objects.filter(event_type=f"admin_cmd_export_{verb}"))


@pytest.mark.parametrize("format", ["csv", "png", "pdf"])
def test_the_command_downloads_the_pages_bytes_with_the_pages_events(
    cli,
    http_scenario,  # noqa: F811
    settings,
    format,
):
    """An export made on the page, downloaded both ways: same bytes, same events."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario, format=format)
    run_export(setup, request)
    body = page_download(http_scenario, settings, request)
    code, document = run("export", "status", str(request.pk), secret=secret)
    assert code == 0, document
    status = document["result"]
    assert status["state"] == "ready" and not status["can_cancel"]
    assert status["file_name"] == f"participation.{format}"
    assert status["size"] == len(body)
    assert status["sha256"] == hashlib.sha256(body).hexdigest()
    code, streamed, document = download(request.pk, secret)
    assert code == 0, document
    assert streamed == body
    result = document["result"]
    assert result["size"] == len(body) and result["sha256"] == status["sha256"]
    assert result["file_name"] == status["file_name"] and result["id"] == str(
        request.pk
    )
    assert result["count"] == status["count"]
    # The page's opening and closing events, twice: the page's, then the
    # command's, attributed to the same Administrator with the same context.
    events = contexts("export_downloaded")
    assert [context["outcome"] for *_, context in events] == [
        "started",
        "succeeded",
    ] * 2
    assert events[:2] == events[2:]
    # A download is an audited read: no admin_cmd_* event.
    assert not AuditEvent.objects.filter(event_type__startswith="admin_cmd_").exists()


def test_create_follows_the_pages_form_and_repeats_safely(cli, http_scenario):  # noqa: F811
    """The page's request and its event; the same key returns the export."""
    run, secret = cli
    setup, _ = http_scenario
    facts = setup[2]
    page_request = create(http_scenario)
    key = uuid4()
    argv = (
        "export",
        "create",
        "--fact-set",
        str(facts.pk),
        "--format",
        "csv",
        "--timezone",
        "UTC",
        "--request-key",
        str(key),
    )
    code, document = run(*argv, secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    export = ExportRequest.objects.get(pk=result["export"]["id"])
    assert export.request_key == key and export.fact_set_id == facts.pk
    assert result["export"]["state"] == "queued" and result["export"]["can_cancel"]
    # The page's request and the command's: same event, same context.
    requested = contexts("export_requested")
    assert [entry[3] for entry in requested] == [{"outcome": "started"}] * 2
    assert requested[0][0] == requested[1][0]  # the same Administrator
    assert requested[1][1] == export.pk
    [event] = commands("create")
    assert event.subject_id == UUID(document["session"]["id"])
    code, again = run(*argv, secret=secret)
    assert code == 0 and not again["result"]["created"]
    assert again["result"]["export"]["id"] == str(export.pk)
    assert len(commands("create")) == 1 and len(contexts("export_requested")) == 2
    assert ExportRequest.objects.exclude(pk=page_request.pk).count() == 1
    # The same key for another format is the page's bound key: invalid.
    bound = list(argv)
    bound[bound.index("csv")] = "pdf"
    code, document = run(*bound, secret=secret)
    assert code == 1 and document["error"]["code"] == "invalid"
    # The worker renders it; the command downloads it.
    run_export(setup, export)
    code, body, document = download(export.pk, secret)
    assert code == 0 and body.startswith(b"date,scope,")
    assert hashlib.sha256(body).hexdigest() == document["result"]["sha256"]


def test_cancel_is_the_pages_and_a_published_export_is_stale(cli, http_scenario):  # noqa: F811
    """One cancellation and one event; repeating it changes nothing."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario)
    code, document = run("export", "cancel", str(request.pk), secret=secret)
    assert code == 0, document
    assert document["result"]["created"] and document["result"]["request_key"] is None
    assert document["result"]["export"]["state"] == "cancelled"
    code, document = run("export", "cancel", str(request.pk), secret=secret)
    assert code == 0 and not document["result"]["created"]
    assert [entry[3] for entry in contexts("export_cancelled")] == [
        {"outcome": "cancelled"}
    ]
    assert len(commands("cancel")) == 1
    published = create(http_scenario)
    run_export(setup, published)
    code, document = run("export", "cancel", str(published.pk), secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version"
    assert len(commands("cancel")) == 1


def test_retry_reruns_a_failed_export(cli, http_scenario):  # noqa: F811
    """The page's retry of the latest failed run, keyed; a stale run is refused."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario)
    with work_transaction():
        act(act(_status(request.task), "claim"), "permanent_failure")
    code, document = run("export", "status", str(request.pk), secret=secret)
    assert code == 0 and document["result"]["state"] == "failed"
    key = uuid4()
    argv = ("export", "retry", str(request.pk), "--request-key", str(key))
    code, document = run(*argv, secret=secret)
    assert code == 0, document
    assert document["result"]["created"]
    assert document["result"]["export"]["state"] == "queued"
    assert request.task.chain_runs.filter(retry_command_id=key).count() == 1
    code, again = run(*argv, secret=secret)
    assert code == 0 and not again["result"]["created"]
    assert len(commands("retry")) == 1
    # The queued retry is not a failed run: the page's conflict.
    code, document = run("export", "retry", str(request.pk), secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version"
    assert len(commands("retry")) == 1


def test_regenerate_requests_an_expired_export_again(cli, http_scenario):  # noqa: F811
    """A new export from the retained inputs; only an expired one may be."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario)
    run_export(setup, request)
    code, document = run("export", "regenerate", str(request.pk), secret=secret)
    assert code == 1 and document["error"]["code"] == "stale_version"
    expire_publication(request)
    code, body, document = download(request.pk, secret)
    assert code == 1 and document["error"]["code"] == "not_available" and body == b""
    key = uuid4()
    argv = ("export", "regenerate", str(request.pk), "--request-key", str(key))
    code, document = run(*argv, secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["export"]["id"] != str(request.pk)
    regenerated = ExportRequest.objects.get(pk=result["export"]["id"])
    assert (regenerated.fact_set_id, regenerated.format) == (
        request.fact_set_id,
        request.format,
    )
    code, again = run(*argv, secret=secret)
    assert code == 0 and again["result"]["export"]["id"] == result["export"]["id"]
    assert not again["result"]["created"] and len(commands("regenerate")) == 1
    # A participation file carries no codes or financial detail: it asked
    # nothing (no --yes) and no session stood in for a fresh sign-in.
    assert not AuditEvent.objects.filter(event_type="automation_fresh_gate").exists()


def test_a_fresh_gated_regeneration_stands_in_for_the_sign_in(
    cli,  # noqa: F811
    http_scenario,  # noqa: F811
    monkeypatch,
):
    """A code or financial file (here the scenario's export, made one of
    ``FRESH_REPORTS``) asks first, refuses a read-only session, and records
    one ``automation_fresh_gate`` with the new export; a repeat records none."""
    from parishkit.stewardship.reports import export_ui

    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario)
    run_export(setup, request)
    expire_publication(request)
    monkeypatch.setattr(
        export_ui, "FRESH_REPORTS", export_ui.FRESH_REPORTS | {request.report}
    )
    gates = AuditEvent.objects.filter(event_type="automation_fresh_gate")
    key = uuid4()
    argv = ("export", "regenerate", str(request.pk), "--request-key", str(key))
    # Unanswered: nothing is made, no gate is recorded.
    code, document = run(*argv, secret=secret)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert not commands("regenerate") and not gates.exists()
    # A read-only session cannot stand in for the sign-in.
    reader = read_only_session(run.service)
    code, document = run(*argv, "--yes", secret=reader)
    assert code == 1 and document["error"]["code"] == "denied"
    assert not commands("regenerate") and not gates.exists()
    code, document = run(*argv, "--yes", secret=secret)
    assert code == 0 and document["result"]["created"], document
    [gate] = gates
    [event] = commands("regenerate")
    assert gate.subject_id == event.subject_id
    # The same key again makes nothing, so it records no new gate.
    code, again = run(*argv, "--yes", secret=secret)
    assert code == 0 and not again["result"]["created"]
    assert gates.count() == 1 and len(commands("regenerate")) == 1


def test_watching_a_ready_export_stops_at_once(cli, http_scenario):  # noqa: F811
    """``export status --watch`` ends with one final document once ready."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario)
    code, document = run("export", "status", str(request.pk), secret=secret)
    assert code == 0 and document["result"]["state"] == "queued"
    run_export(setup, request)
    code, document = run(
        "export", "status", str(request.pk), "--watch", "2", secret=secret
    )
    assert code == 0 and document["final"] and document["result"]["state"] == "ready"


def test_refusals_change_nothing(cli, http_scenario):  # noqa: F811
    """Read-only scope, unknown records, a restore review and an ended session."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario)
    run_export(setup, request)
    # A read-only session may read the status, never change or download.
    reader = read_only_session(run.service)
    code, document = run("export", "status", str(request.pk), secret=reader)
    assert code == 0 and document["result"]["state"] == "ready"
    for argv in (
        ("export", "cancel", str(request.pk)),
        ("export", "retry", str(request.pk)),
        ("export", "regenerate", str(request.pk)),
    ):
        code, document = run(*argv, secret=reader)
        assert code == 1 and document["error"]["code"] == "denied", argv
    code, body, document = download(request.pk, reader)
    assert code == 1 and document["error"]["code"] == "denied" and body == b""
    # Unknown records: not available, nothing written.
    unknown = str(uuid4())
    for argv in (
        ("export", "status", unknown),
        ("export", "cancel", unknown),
        ("export", "retry", unknown),
        ("export", "regenerate", unknown),
        (
            "export",
            "create",
            "--fact-set",
            unknown,
            "--format",
            "csv",
            "--timezone",
            "UTC",
        ),
    ):
        code, document = run(*argv, secret=secret)
        assert code == 1 and document["error"]["code"] == "not_available", argv
    code, body, document = download(unknown, secret)
    assert code == 1 and document["error"]["code"] == "not_available" and body == b""
    code, document = run(
        "export",
        "create",
        "--fact-set",
        str(setup[2].pk),
        "--format",
        "csv",
        "--timezone",
        "Mars/Olympus",
        secret=secret,
    )
    assert code == 1 and document["error"]["code"] == "invalid"
    assert not AuditEvent.objects.filter(event_type__startswith="admin_cmd_").exists()
    assert not AuditEvent.objects.filter(event_type="export_downloaded").exists()
    # An ended session: exit 5 for every command, nothing recorded.
    row = AutomationSession.objects.get(secret_digest=automation.secret_digest(secret))
    actor = current_principal(run.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document = run("export", "status", str(request.pk), secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    code, body, document = download(request.pk, secret)
    assert code == 5 and body == b""
    assert not AuditEvent.objects.filter(event_type="export_downloaded").exists()


def test_a_download_that_breaks_off_records_its_failure(
    cli,
    http_scenario,  # noqa: F811
    monkeypatch,
):
    """A write that fails mid-stream: the page's closing event says failed."""
    run, secret = cli
    setup, _ = http_scenario
    request = create(http_scenario, format="png")
    run_export(setup, request)
    written = []

    def broken(stdout, chunk):
        """Accept nothing: the operator's disk is full."""
        written.append(chunk)
        raise OSError("No space left on device")

    monkeypatch.setattr(admin_cli, "write_stream", broken)
    code, _, document = download(request.pk, secret)
    assert code == 3 and document["error"]["code"] == "internal"
    assert written
    assert [context["outcome"] for *_, context in contexts("export_downloaded")] == [
        "started",
        "failed",
    ]
