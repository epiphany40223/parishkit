"""The System logs commands of ``pk-stewardship admin`` (ADM-11 PR 8a).

Process admission is replaced by the test's own Django assembly (the runner
of test_admin_task_retry_cli_postgresql.py); everything after it is the
real command line on the restricted web login, reading the log through the
page's own functions (``audit.log_reads``) with the page's own filters. The
page and the command record the same view and export events with the same
counts, and ``logs export`` writes the same bytes the page downloads.
"""

import io
import json
from uuid import uuid4

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import unguarded
from .automation_builders import paired
from .test_admin_status_cli_postgresql import without_web_runtimes
from .test_admin_task_retry_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_log_views_postgresql import diagnostics

pytestmark = pytest.mark.django_db(transaction=True)

# Only the entries the tests write: operational ERROR and CRITICAL lines.
# Every command and page view adds audit records and INFO lines of its own,
# so these filters keep the page and the command reading the same entries.
SERIOUS = ("--show", "error", "--show", "critical")
SERIOUS_FORM = {"applied": "yes", "error": "yes", "critical": "yes"}


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Commands over the shared authentication service."""
    return runner(auth_service, monkeypatch)


def counted(event_type):
    """The ``count`` of each recorded event of this type, oldest first."""
    return [
        context["count"]
        for context in AuditContext.objects.filter(event__event_type=event_type)
        .order_by("event__created_at", "event__id")
        .values_list("context", flat=True)
    ]


def stream(admin, *argv, secret):
    """A streaming command: (exit code, standard output bytes, its document).

    The document is the one line on standard error (logging is off in
    these tests); standard output holds the file alone.
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
    [document] = [json.loads(line) for line in err.getvalue().splitlines()]
    return code, raw.getvalue(), document


def page(browser, path, values):
    """Post the System logs page (or its download) as the browser does."""
    token = browser.cookies["pk_admin_csrf"].value
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(path, {"csrfmiddlewaretoken": token} | values)


def test_logs_list_reads_the_pages_entries_and_records_its_view(admin, google):
    """The page's rows, newest first, with the page's view event and count."""
    diagnostics(("ERROR", "CRITICAL", "INFO"))
    browser, secret, row = paired(admin.service, scope="read-only")
    response = page(browser, "/admin/system/logs/", SERIOUS_FORM)
    assert response.status_code == 200
    code, document, _ = admin("logs", "list", *SERIOUS, secret=secret)
    assert code == 0, document
    result = document["result"]
    entries = result["entries"]
    assert [entry["level"] for entry in entries] == ["CRITICAL", "ERROR"]
    assert {entry["source"] for entry in entries} == {"operational"}
    assert entries[0]["type"] == "task_failed"
    assert entries[0]["details"]["failure"] == "alert_mail"
    assert result["page"] == 1 and result["matching"] == 2 and not result["has_next"]
    # The page and the command each recorded one view, with the same count;
    # the command's is attributed to the approving Administrator.
    assert counted("system_logs_viewed") == [2, 2]
    event = AuditEvent.objects.filter(event_type="system_logs_viewed").latest(
        "created_at"
    )
    assert event.actor_id == row.principal_id
    assert "@" not in json.dumps(document)


def test_logs_list_pages_through_one_snapshot(admin, google):
    """``through`` anchors the pages: later entries never shift them."""
    diagnostics(("ERROR",) * 30)
    secret = paired(admin.service, scope="read-only")[1]
    code, first, _ = admin(
        "logs", "list", *SERIOUS, "--size", "25", "--sort", "oldest", secret=secret
    )
    assert code == 0, first
    through = first["result"]["through"]
    assert first["result"]["has_next"] and len(first["result"]["entries"]) == 25
    diagnostics(("ERROR",) * 3)
    code, second, _ = admin(
        "logs",
        "list",
        *SERIOUS,
        "--size",
        "25",
        "--sort",
        "oldest",
        "--through",
        through,
        "--page",
        "2",
        secret=secret,
    )
    assert code == 0, second
    assert len(second["result"]["entries"]) == 5 and not second["result"]["has_next"]
    seen = {entry["id"] for entry in first["result"]["entries"]}
    assert not seen & {entry["id"] for entry in second["result"]["entries"]}


def test_logs_export_writes_the_pages_download(admin, google):
    """The same bytes as the page's download, and the same export event."""
    diagnostics(("ERROR", "CRITICAL", "WARNING"))
    browser, secret, row = paired(admin.service)
    for fmt, zone in (("csv", "UTC"), ("jsonl", "America/New_York")):
        response = page(
            browser,
            "/admin/system/logs/export/",
            SERIOUS_FORM | {"format": fmt, "timezone": zone},
        )
        assert response.status_code == 200
        code, body, document = stream(
            admin,
            "logs",
            "export",
            *SERIOUS,
            "--format",
            fmt,
            "--timezone",
            zone,
            secret=secret,
        )
        assert code == 0, document
        assert body == response.content
        result = document["result"]
        assert result["count"] == 2 and result["size"] == len(body)
        assert result["format"] == fmt and result["timezone"] == zone
        assert result["file_name"].startswith("stewardship-logs-")
    assert counted("system_logs_exported") == [2, 2, 2, 2]
    event = AuditEvent.objects.filter(event_type="system_logs_exported").latest(
        "created_at"
    )
    assert event.actor_id == row.principal_id
    # A read records no admin_cmd_* event.
    assert not AuditEvent.objects.filter(event_type__startswith="admin_cmd_").exists()


def test_logs_export_refusals_write_no_file(admin, google):
    """Read-only scope, invalid choices and an ended session; no bytes, no event."""
    diagnostics(("ERROR",))
    _, reader, _ = paired(admin.service, scope="read-only")
    code, body, document = stream(admin, "logs", "export", secret=reader)
    assert code == 1 and document["error"]["code"] == "denied" and body == b""
    _, secret, row = paired(admin.service)
    for argv in (
        ("--timezone", "Mars/Olympus"),
        ("--event", "Not An Event"),
        ("--start", "2054-10-05"),
    ):
        code, body, document = stream(admin, "logs", "export", *argv, secret=secret)
        assert code == 1 and document["error"]["code"] == "invalid", argv
        assert body == b""
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, body, document = stream(admin, "logs", "export", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert body == b"" and counted("system_logs_exported") == []


def test_logs_list_refusals(admin, google):
    """Invalid filters, a restore review and an ended session; no view event."""
    browser, secret, row = paired(admin.service, scope="read-only")
    for argv in (
        ("--size", "7"),
        ("--through", "2054-10-05T14:00:00+00:00"),
        ("--start", "2054-10-05"),
        ("--start", "2054-10-05", "--end", "2054-10-01", "--zone", "UTC"),
    ):
        code, document, _ = admin("logs", "list", *argv, secret=secret)
        assert code == 1 and document["error"]["code"] == "invalid", argv
    code, document, _ = admin("logs", "list", "--actor", "x", secret=secret)
    assert code == 2 and document["error"]["code"] == "usage"
    with unguarded():
        SystemConfiguration.objects.update(restore_review_required=True)
    code, document, _ = admin("logs", "list", secret=secret)
    assert code == 3 and document["error"]["code"] == "unavailable"
    with unguarded():
        SystemConfiguration.objects.update(restore_review_required=False)
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document, _ = admin("logs", "list", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert counted("system_logs_viewed") == []


def test_the_actor_filter_names_one_administrator(admin, google):
    """``--actor`` filters as the page's Actor does: only that user's entries.

    Approving the session recorded audit entries under the Administrator.
    """
    _, secret, row = paired(admin.service, scope="read-only")
    code, document, _ = admin(
        "logs",
        "list",
        "--show",
        "audit",
        "--actor",
        str(row.principal_id),
        secret=secret,
    )
    assert code == 0, document
    entries = document["result"]["entries"]
    assert entries and {entry["actor_id"] for entry in entries} == {
        str(row.principal_id)
    }
    code, document, _ = admin(
        "logs", "list", "--show", "audit", "--actor", str(uuid4()), secret=secret
    )
    assert code == 0 and document["result"]["entries"] == []


def test_logs_list_lists_the_pages_rows_in_its_order(admin, google):
    """Row for row and in order, both ways, as the page lists them."""
    from parishkit.stewardship.observability import correlation

    from .test_log_views_postgresql import identifiers

    # Each entry with its own correlation id, which the page shows per row.
    for _ in range(6):
        with correlation():
            diagnostics(("ERROR",))
    browser, secret, _ = paired(admin.service, scope="read-only")
    for sort in ("newest", "oldest"):
        response = page(
            browser, "/admin/system/logs/", SERIOUS_FORM | {"sort": sort, "size": "25"}
        )
        listed = [str(value) for value in identifiers(response)]
        code, document, _ = admin(
            "logs", "list", *SERIOUS, "--sort", sort, "--size", "25", secret=secret
        )
        assert code == 0, document
        entries = document["result"]["entries"]
        assert len(listed) == 6
        assert [entry["correlation_id"] for entry in entries] == listed, sort


def test_logs_export_matches_the_download_with_actor_emails(admin, google):
    """The same bytes as the page's file when its rows name actors by email."""
    browser, secret, row = paired(admin.service)
    # Approving the session recorded this event under the Administrator.
    form = {
        "applied": "yes",
        "audit": "yes",
        "event": "automation_session_approved",
        "actor": str(row.principal_id),
    }
    response = page(browser, "/admin/system/logs/export/", form | {"format": "csv"})
    assert response.status_code == 200
    code, body, document = stream(
        admin,
        "logs",
        "export",
        "--show",
        "audit",
        "--event",
        "automation_session_approved",
        "--actor",
        str(row.principal_id),
        secret=secret,
    )
    assert code == 0, document
    assert body == response.content and b"@" in body
    assert document["result"]["count"] >= 1


def test_a_command_read_past_the_pages_limit_is_stopped_and_recorded(
    admin, google, monkeypatch
):
    """``logs list`` and ``logs export`` read under the page's statement limit
    (#287): a read that outlives it is stopped, recorded as a timeout with what
    stopped it, the limit and the time taken, and is exit 3 with no view or
    export event."""
    from django.db import connection

    from parishkit.stewardship.audit import log_reads
    from parishkit.stewardship.audit.models import OperationalLog

    secret = paired(admin.service, scope="full")[1]
    monkeypatch.setattr(log_reads, "READ_SECONDS", 0.2)
    real = log_reads.sources

    def slow(query, through):
        """A read that takes longer than the limit."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(2)")
        return real(query, through)

    monkeypatch.setattr(log_reads, "sources", slow)
    viewed = counted("system_logs_viewed")
    exported = counted("system_logs_exported")
    code, document, _ = admin("logs", "list", "--text", "anything", secret=secret)
    assert code == 3 and document["error"]["code"] == "unavailable", document
    code, body, streamed = stream(
        admin, "logs", "export", "--text", "anything", secret=secret
    )
    assert code == 3 and body == b"", streamed
    assert counted("system_logs_viewed") == viewed
    assert counted("system_logs_exported") == exported
    stops = list(
        OperationalLog.objects.filter(event="task_timed_out").values_list(
            "context", flat=True
        )
    )
    assert len(stops) == 2
    for stop in stops:
        assert stop["what"] == "statement_timeout" and "limit_seconds" in stop
