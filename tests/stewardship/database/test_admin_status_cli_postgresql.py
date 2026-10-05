"""The read-only status commands of ``pk-stewardship admin`` (ADM-11 PR 3).

Process admission is replaced by the test's own Django assembly, as in
test_admin_cli_postgresql.py; everything after it is the real command line
on the restricted web login: the session's admission, each read through the
function its page uses, the recheck, the page's own view event, ``--watch``
with its heartbeat, timeout and session ending, and parity with the pages'
own answers.
"""

import contextlib
import io
import json
import secrets
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from parishkit.logging import STRUCTURED_EXTRA_FIELD
from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationSession,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole

from .automation_builders import paired
from .campaign_builders import add_draft
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_send_progress_postgresql import prepared
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


def runner(service, monkeypatch):
    """Run admin commands in this process over ``service``'s store and Valkey.

    Process admission is replaced by the test's own assembly; the returned
    ``run(*argv, secret=...)`` gives ``(exit code, documents)``.
    """
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )

    @contextlib.contextmanager
    def admitted(configuration):
        """The test's Django and Valkey stand in for the web's own assembly."""
        yield admin_cli.AdminRuntime(
            store=service.store,
            pairing=automation.PairingStore(
                service.limiter.client, service.limiter.namespace
            ),
            public_origin="https://campaign.example.org",
            setup_complete=service.setup_complete,
        )

    monkeypatch.setattr(admin_cli, "ADMISSION", admitted)

    def run(*argv, secret):
        """One command as the wrapper runs it, on the restricted web login.

        The command process assembles no web runtimes, so none is visible
        while it runs: a read that reached for one would fail here as it
        would in the container.
        """
        out = io.StringIO()
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            without_web_runtimes(),
        ):
            code = admin_cli.main(
                [*argv, "--config", "web.yaml", "--session-stdin"],
                stdin=io.BytesIO(f"pk-admin-session/1 {secret} {'a' * 64}\n".encode()),
                stdout=out,
                stderr=io.StringIO(),
            )
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    run.service = service
    return run


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Run admin commands in this process; returns (code, documents)."""
    return runner(auth_service, monkeypatch)


@contextlib.contextmanager
def without_web_runtimes():
    """Hide the web's request runtimes, which the command process never has."""
    from django.test import override_settings

    with override_settings(
        STEWARDSHIP_AUTH_RUNTIME=None,
        STEWARDSHIP_FAMILY_RUNTIME=None,
        STEWARDSHIP_HEALTH_RUNTIME=None,
    ):
        yield


def session(admin, **options):
    """An approved automation session; returns its secret."""
    _, secret, _ = paired(admin.service, **options)
    return secret


def views(event_type):
    """How many view events of this type have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def one(admin, *argv, secret):
    """A command that prints exactly one document; returns (code, document)."""
    code, documents = admin(*argv, secret=secret)
    assert len(documents) == 1, documents
    return code, documents[0]


def test_status_reads_the_home_summary_and_audits_it_once(admin, google):
    """The home page's read, counts only, audited as the page is."""
    store = admin.service.store
    _, row, _ = add_draft(store, store.active(), uuid4())
    secret = session(admin, scope="read-only")
    before = views("dashboard_viewed")
    code, document = one(admin, "status", secret=secret)
    assert code == 0 and document["ok"] and document["final"], document
    result = document["result"]
    assert result["mode"] == "testing"
    assert result["campaign"]["id"] == row["id"]
    assert result["campaign"]["state"] == "draft"
    assert result["tasks"]["delivery_unknown"] == 0
    assert result["presence"] == {"count": 0}
    assert result["offsite_backup"] == {"state": "unset"}
    # The approval itself is an unacknowledged automation notice: a count.
    assert result["automation_notices"] >= 1
    assert set(result) == set(admin_cli.BY_NAME["status"].result_fields)
    # The page's own view event, attributed to the approving Administrator.
    assert views("dashboard_viewed") == before + 1
    assert AuditEvent.objects.filter(
        event_type="dashboard_viewed",
        actor_id=AutomationSession.objects.get().principal_id,
    ).exists()
    assert "admin@example.org" not in json.dumps(document)


def test_a_read_never_records_activity_on_its_command_session(admin, google):
    """Passive admission: a read-only session reads, and nothing is renewed."""
    secret = session(admin, scope="read-only")
    code, _ = one(admin, "status", secret=secret)
    assert code == 0
    command = PortalSession.objects.get(
        pk__in=AutomationLogin.objects.values("portal_session_id")
    )
    # Inserted at version 1 and closed at exit (version 2); a renewal of
    # its activity would have made it 3.
    assert command.revoked_at is not None and command.version == 2


def test_task_list_matches_the_background_work_page(admin, google):
    """The page's polling API and the command list the same tasks and counts."""
    for _ in range(3):
        new()
    browser, secret, _ = paired(admin.service)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = browser.get("/admin/background/tasks?size=20&sort=task").json()
    code, document = one(
        admin, "task", "list", "--size", "20", "--sort", "task", secret=secret
    )
    assert code == 0, document
    result = document["result"]
    assert [row["id"] for row in result["tasks"]] == [
        row["id"] for row in page["tasks"]
    ]
    assert result["counts"] == page["counts"]
    assert result["matching"] == page["matching"] == 3


def test_task_list_and_show_match_background_work(admin, google):
    """The same rows, metadata and filters as the page, and its view event."""
    task = new()
    secret = session(admin)
    before = views("background_viewed")
    code, document = one(admin, "task", "list", secret=secret)
    assert code == 0, document
    listed = document["result"]
    assert listed["state"] == "nonterminal" and listed["counts"]["queued"] == 1
    [row] = listed["tasks"]
    assert row["id"] == str(task.run_id) and row["state"] == "queued"
    assert row["created_at"].endswith("+00:00")
    code, document = one(
        admin, "task", "list", "--state", "succeeded", "--size", "20", secret=secret
    )
    assert code == 0 and document["result"]["tasks"] == []
    code, document = one(admin, "task", "show", str(task.run_id), secret=secret)
    assert code == 0, document
    shown = document["result"]
    assert shown["task"]["id"] == str(task.run_id)
    assert shown["latest_run_id"] == str(task.run_id)
    assert [event["action"] for event in shown["events"]] == ["created"]
    assert views("background_viewed") == before + 3
    # task show's view names the task, as the task page's does.
    assert (
        AuditEvent.objects.filter(
            event_type="background_viewed", subject_id=task.run_id
        ).count()
        == 1
    )


def test_a_missing_task_is_not_available_and_bad_input_is_refused(admin, google):
    """An unknown task is exit 1; a filter the page refuses is invalid."""
    secret = session(admin)
    code, document = one(admin, "task", "show", str(uuid4()), secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available"
    code, document = one(admin, "task", "list", "--sort", "nonsense", secret=secret)
    assert code == 1 and document["error"]["code"] == "invalid"
    code, document = one(admin, "send", "history", "--size", "7", secret=secret)
    assert code == 1 and document["error"]["code"] == "invalid"


def test_a_terminal_task_watch_prints_one_final_document(admin, google):
    """A finished task stops the watch at once, with exit 0."""
    task = act(new(), "safe_cancel", actor_id=uuid4())
    assert task.state == "cancelled"
    secret = session(admin)
    code, documents = admin(
        "task", "show", str(task.run_id), "--watch", "2", secret=secret
    )
    assert code == 0 and len(documents) == 1 and documents[0]["final"] is True


def test_a_watch_times_out_with_the_last_state_and_audits_once(
    admin, google, monkeypatch, caplog
):
    """A task that stays queued: non-final documents, then exit 7."""
    task = new()
    secret = session(admin)
    # The command line's own clock advances five seconds per reading and
    # never sleeps; nothing else in the process sees this clock.
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(
        admin_cli,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda seconds: None),
    )
    beats = []
    real = automation.heartbeat
    monkeypatch.setattr(
        automation, "heartbeat", lambda row: beats.append(real(row)) or beats[-1]
    )
    monkeypatch.setattr(admin_cli, "HEARTBEAT_SECONDS", 5)
    before = views("background_viewed")
    code, documents = admin(
        "task",
        "show",
        str(task.run_id),
        "--watch",
        "2",
        "--timeout",
        "12",
        secret=secret,
    )
    assert code == 7, documents
    *polls, last = documents
    assert polls and all(not item["final"] and item["ok"] for item in polls)
    assert last["final"] and not last["ok"]
    assert last["error"]["code"] == "watch_timeout"
    assert last["result"]["task"]["state"] == "queued"
    # Only the first read is the page view; the polls are passive.
    assert views("background_viewed") == before + 1
    # The command session was kept alive while the watch ran.
    assert beats and all(beats)
    # The timeout logged what was waited for, the limit and the time taken.
    [logged] = [
        getattr(record, STRUCTURED_EXTRA_FIELD)
        for record in caplog.records
        if getattr(record, STRUCTURED_EXTRA_FIELD, {}).get("timeout")
        == "automation_watch"
    ]
    assert logged["limit_seconds"] == 12 and logged["elapsed_seconds"] >= 12


def test_a_session_revoked_during_a_watch_stops_it_with_exit_5(
    admin, google, monkeypatch
):
    """Each poll admits again: a revocation between polls ends the watch."""
    task = new()
    secret = session(admin)
    row = AutomationSession.objects.get()

    def revoke(seconds):
        """The Administrator revokes the session in the portal meanwhile."""
        if AutomationSession.objects.get(pk=row.pk).revoked_at is None:
            actor = current_principal(admin.service.store, row.principal_id)
            automation.revoke(row.pk, actor, reason="revoked_by_owner")

    monkeypatch.setattr(
        admin_cli, "time", SimpleNamespace(monotonic=time.monotonic, sleep=revoke)
    )
    code, documents = admin(
        "task", "show", str(task.run_id), "--watch", "2", secret=secret
    )
    assert code == 5, documents
    assert documents[0]["final"] is False and documents[0]["ok"]
    assert documents[-1]["error"]["code"] == "session_ended"


def test_heartbeat_never_revives_an_ended_command_session(admin, google):
    """A command session already revoked is not renewed."""
    secret = session(admin)
    code, _ = one(admin, "status", secret=secret)
    assert code == 0
    command = PortalSession.objects.get(
        pk__in=AutomationLogin.objects.values("portal_session_id")
    )
    assert command.revoked_at is not None
    assert automation.heartbeat(command) is False


def test_send_progress_and_history_without_a_send(admin, google):
    """No send yet: the watch stops at once; the history is empty."""
    store = admin.service.store
    _, row, _ = add_draft(store, store.active(), uuid4())
    secret = session(admin, scope="read-only")
    before = views("delivery_viewed")
    code, documents = admin("send", "progress", "--watch", "2", secret=secret)
    assert code == 0 and len(documents) == 1, documents
    result = documents[0]["result"]
    assert result == {
        "campaign_id": row["id"],
        "mode": "testing",
        "paused": False,
        "upcoming": False,
        "send": None,
    }
    code, document = one(admin, "send", "history", secret=secret)
    assert code == 0 and document["result"]["sends"] == []
    assert document["result"]["campaign_id"] == row["id"]
    assert views("delivery_viewed") == before + 2


def test_schedule_show_reads_the_applied_schedules(admin, google):
    """The campaign's window, editability, version and resolved schedules."""
    store = admin.service.store
    _, row, _ = add_draft(store, store.active(), uuid4())
    secret = session(admin, scope="read-only")
    code, document = one(admin, "schedule", "show", secret=secret)
    assert code == 0, document
    result = document["result"]
    assert result["campaign_id"] == row["id"]
    assert result["version"] == store.active().digest
    assert result["editable"] is True
    assert result["window"]["timezone"]
    definition = ScheduleDefinition.objects.get()
    [schedule] = result["schedules"]
    assert schedule["id"] == str(definition.pk)
    assert schedule["kind"] == "initial"
    assert schedule["resolved"][0]["due_at"].endswith("+00:00")
    # The explicit campaign gives the same answer; an unknown one does not.
    code, again = one(admin, "schedule", "show", "--campaign", row["id"], secret=secret)
    assert code == 0 and again["result"] == result
    code, document = one(
        admin, "schedule", "show", "--campaign", str(uuid4()), secret=secret
    )
    assert code == 1 and document["error"]["code"] == "not_available"


def test_an_ended_session_is_exit_5_for_every_read(admin, google):
    """Revoked between commands: each read refuses before reading."""
    secret = session(admin)
    row = AutomationSession.objects.get()
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    for argv in (("status",), ("task", "list"), ("send", "progress")):
        code, document = one(admin, *argv, secret=secret)
        assert code == 5 and document["error"]["code"] == "session_ended", argv


def test_an_unknown_secret_reads_nothing(admin, google):
    """No session, no read and no view event."""
    before = views("dashboard_viewed")
    code, document = one(admin, "status", secret=secrets.token_urlsafe(32))
    assert code == 5 and document["error"]["code"] == "session_missing"
    assert views("dashboard_viewed") == before
    assert UUID(document["correlation_id"])


def test_a_real_send_in_progress_is_followed_and_listed(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
):
    """A prepared invitation: in progress for the watch, live in the history."""
    from parishkit.stewardship.accounts.authentication import runtime

    message = prepared(family_mail)
    command = runner(runtime(), monkeypatch)
    _, secret, _ = paired(command.service)
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(
        admin_cli,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda seconds: None),
    )

    def run(*argv):
        """One command under the paired session."""
        return command(*argv, secret=secret)

    code, documents = run("send", "progress", "--watch", "2", "--timeout", "6")
    assert code == 7, documents
    send = documents[0]["result"]["send"]
    assert send["active"] and send["kind"] == "initial"
    assert send["remaining"] == 1 and send["total"] == 1 and send["done"] == 0
    assert documents[-1]["error"]["code"] == "watch_timeout"
    code, documents = run("send", "history")
    assert code == 0, documents
    [listed] = documents[0]["result"]["sends"]
    assert listed["live"] and listed["current"] and listed["remaining"] == 1
    text = json.dumps(documents)
    assert family_mail.code not in text and message.sealed_substitutions not in text


def test_schedule_show_during_a_restore_review_is_unavailable(admin, google):
    """A restore under review withholds the read: exit 3, retry later."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .auth_builders import unguarded

    store = admin.service.store
    add_draft(store, store.active(), uuid4())
    secret = session(admin)
    with unguarded():
        SystemConfiguration.objects.update(restore_review_required=True)
    for argv in (("schedule", "show"), ("status",), ("task", "list")):
        code, document = one(admin, *argv, secret=secret)
        assert code == 3 and document["error"]["code"] == "unavailable", argv


def test_schedule_show_without_a_campaign_is_not_available(admin, google):
    """No current campaign: nothing to show, exit 1."""
    secret = session(admin)
    code, document = one(admin, "schedule", "show", secret=secret)
    assert code == 1 and document["error"]["code"] == "not_available"


def test_a_missing_task_is_reported_only_after_the_recheck(admin, google, monkeypatch):
    """A session ended while the read ran is exit 5, not "no such task"."""
    from parishkit.stewardship.jobs import task_reads

    secret = session(admin)
    row = AutomationSession.objects.get()

    def ended(*args):
        """The session is revoked while the task is looked up."""
        actor = current_principal(admin.service.store, row.principal_id)
        automation.revoke(row.pk, actor, reason="revoked_by_owner")
        return None, 0

    monkeypatch.setattr(task_reads, "detail", ended)
    code, document = one(admin, "task", "show", str(uuid4()), secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"


def test_ctrl_c_ends_a_watch_with_the_last_state(admin, google, monkeypatch):
    """No traceback: a final document, exit 7, and the command session closed."""
    task = new()
    secret = session(admin)

    def interrupted(seconds):
        """The operator presses Ctrl-C while the watch waits."""
        raise KeyboardInterrupt

    monkeypatch.setattr(
        admin_cli, "time", SimpleNamespace(monotonic=time.monotonic, sleep=interrupted)
    )
    code, documents = admin(
        "task", "show", str(task.run_id), "--watch", "2", secret=secret
    )
    assert code == 7, documents
    last = documents[-1]
    assert last["final"] and last["error"]["code"] == "watch_interrupted"
    assert last["result"]["task"]["state"] == "queued"
    command = PortalSession.objects.get(
        pk__in=AutomationLogin.objects.values("portal_session_id")
    )
    assert command.revoked_at is not None
