"""The session commands of ``pk-stewardship admin`` against PostgreSQL (ADM-11).

Process admission (web profile, mounts, signing keyring, receipt check) is
replaced by the test's own Django assembly; everything after it is the real
command line: the preamble, ``login start`` and ``login wait`` around a real
approval, ``whoami``, ``sessions`` and ``logout``, their documents,
exit codes and the 72-hour warning.
"""

import contextlib
import io
import json
import secrets
from datetime import timedelta

import pytest
from django.db.models import F

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.sessions import database_now

from .auth_builders import signed_in, unguarded
from .automation_builders import HOST, OTHER_HOST, approve_now

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admin(auth_service, monkeypatch):
    """Run admin commands in this process, admitted as the test assembly."""
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
            store=auth_service.store,
            pairing=automation.PairingStore(
                auth_service.limiter.client, auth_service.limiter.namespace
            ),
            public_origin="https://campaign.example.org",
        )

    monkeypatch.setattr(admin_cli, "ADMISSION", admitted)
    monkeypatch.setattr(admin_cli, "POLL_SECONDS", 0.05)

    def run(*argv, secret, host=HOST):
        """One command with the wrapper's preamble; returns (code, doc, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        code = admin_cli.main(
            [*argv, "--config", "web.yaml", "--session-stdin"],
            stdin=io.BytesIO(f"pk-admin-session/1 {secret} {host}\n".encode()),
            stdout=out,
            stderr=err,
        )
        lines = out.getvalue().splitlines()
        assert len(lines) == 1, lines
        return code, json.loads(lines[0]), err.getvalue()

    run.service = auth_service
    return run


def pair(admin, browser, **options):
    """``login start``, approval, ``login wait``; returns the secret."""
    secret = secrets.token_urlsafe(32)
    code, document, _ = admin(
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "launch assistant",
        "--expect-email",
        "Admin@Example.org",
        "--scope",
        options.get("scope", "full"),
        "--days",
        str(options.get("days", 30)),
        secret=secret,
    )
    assert code == 0 and document["final"] is False, document
    result = document["result"]
    assert result["approve_url"] == (
        "https://campaign.example.org/admin/users/automation/approval/"
    )
    user_code = result["user_code"]
    assert len(user_code) == 9 and user_code[4] == "-"
    scope = {"read-only": "read_only"}.get(options.get("scope"), "full")
    days = options.get("days", 30)
    approve_now(
        admin.service,
        automation.normalized_code(user_code),
        scope=scope,
        days=days,
    )
    code, document, _ = admin("login", "wait", "--name", "ops", secret=secret)
    assert code == 0, document
    assert document["result"]["name"] == "ops"
    assert document["result"]["principal_email"] == "admin@example.org"
    assert secret not in json.dumps(document)
    return secret


def test_login_whoami_sessions_and_logout(admin, google):
    """The whole session life through the command line's documents."""
    browser, _ = signed_in()
    secret = pair(admin, browser)
    code, document, stderr = admin("whoami", secret=secret)
    assert code == 0 and document["ok"] and document["final"]
    assert "administrator" in document["result"]["roles"]
    assert document["result"]["scope"] == "full"
    assert document["session"]["id"] == document["result"]["id"]
    assert "WARNING" not in stderr
    code, document, _ = admin("sessions", secret=secret)
    assert code == 0
    [listed] = document["result"]["sessions"]
    assert listed["live"] and listed["current"] and "digest" not in json.dumps(listed)
    code, document, _ = admin("logout", secret=secret)
    assert code == 0 and document["result"]["end_reason"] == "logout"
    code, document, _ = admin("whoami", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert document["ok"] is False


def test_sessions_lists_live_ones_unless_ended_are_included(admin, google):
    """The page's filter and sort (#621): live only by default, ended on
    request with when and why each ended, sorted by the page's tokens."""
    browser, _ = signed_in()
    first = pair(admin, browser)
    second = pair(admin, browser)
    assert admin("logout", secret=first)[0] == 0
    code, document, _ = admin("sessions", secret=second)
    assert code == 0
    [live] = document["result"]["sessions"]
    assert live["live"] and live["current"] and live["ended_at"] is None
    code, document, _ = admin("sessions", "--include-ended", secret=second)
    newest, ended = document["result"]["sessions"]
    assert newest["id"] == live["id"] and newest["live"]
    assert not ended["live"] and ended["end_reason"] == "logout"
    assert ended["ended_at"] is not None
    code, document, _ = admin(
        "sessions", "--include-ended", "--sort", "created", secret=second
    )
    assert [row["id"] for row in document["result"]["sessions"]] == [
        ended["id"],
        live["id"],
    ]
    # A descending token is written with "=", or argparse reads it as an option.
    code, document, _ = admin(
        "sessions", "--include-ended", "--sort=-created", secret=second
    )
    assert code == 0
    assert [row["id"] for row in document["result"]["sessions"]] == [
        live["id"],
        ended["id"],
    ]
    code, document, _ = admin(
        "sessions", "--include-ended", "--sort=-ended", secret=second
    )
    # The live session has no ending, so it sorts last either way.
    assert [row["id"] for row in document["result"]["sessions"]] == [
        ended["id"],
        live["id"],
    ]
    code, document, _ = admin("sessions", "--sort", "created_at", secret=second)
    assert code == 1 and document["error"]["code"] == "invalid"


def test_wait_is_repeatable_while_pending_and_expires_cleanly(admin, google):
    """pairing_pending while the request lives; pairing_expired after; no session."""
    secret = secrets.token_urlsafe(32)
    code, document, _ = admin(
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "slow",
        "--expect-email",
        "admin@example.org",
        "--scope",
        "full",
        secret=secret,
    )
    assert code == 0
    for _ in range(2):
        code, document, _ = admin(
            "login", "wait", "--name", "ops", "--timeout", "1", secret=secret
        )
        assert code == 5 and document["error"]["code"] == "pairing_pending"
    # Expire the request: both keys disappear, as Valkey's expiry would do.
    from parishkit.stewardship.accounts.authentication import runtime

    limiter = runtime().limiter
    for key in limiter.client.scan_iter(limiter.namespace + ":automation-pairing*"):
        limiter.client.delete(key)
    code, document, _ = admin("login", "wait", "--name", "ops", secret=secret)
    assert code == 5 and document["error"]["code"] == "pairing_expired"
    assert not AutomationSession.objects.exists()


def test_logout_reports_a_session_another_ending_beat(admin, google, monkeypatch):
    """A revocation committing first: no change, said plainly, exit 0."""
    browser, _ = signed_in()
    secret = pair(admin, browser)
    real = automation.end_session

    def raced(session, *, reason, actor_id=None):
        """The portal's revocation commits just before logout's ending."""
        real(session, reason="revoked_by_owner", actor_id=actor_id)
        return real(session, reason=reason, actor_id=actor_id)

    monkeypatch.setattr(automation, "end_session", raced)
    code, document, _ = admin("logout", secret=secret)
    assert code == 0 and document["ok"], document
    assert document["result"] == {
        "ended": False,
        "reason": "already_ended",
        "end_reason": "revoked_by_owner",
    }
    assert AutomationSession.objects.get().end_reason == "revoked_by_owner"


def test_a_failed_start_leaves_no_pairing(admin, google, monkeypatch):
    """An outage before the code is printed stores nothing to approve."""
    from django.db import OperationalError

    from parishkit.stewardship.accounts.authentication import runtime

    def outage():
        """The database clock is unreachable."""
        raise OperationalError("down")

    monkeypatch.setattr(automation, "database_now", outage)
    code, document, _ = admin(
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "outage",
        "--expect-email",
        "admin@example.org",
        "--scope",
        "full",
        secret=secrets.token_urlsafe(32),
    )
    assert code == 3 and document["error"]["code"] == "unavailable"
    limiter = runtime().limiter
    assert not list(
        limiter.client.scan_iter(limiter.namespace + ":automation-pairing*")
    )


def test_an_approved_but_uncollected_pairing_is_abandoned(admin, google):
    """Once the request has expired, its approved session is revoked."""
    browser, _ = signed_in()
    secret = secrets.token_urlsafe(32)
    _, document, _ = admin(
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "late",
        "--expect-email",
        "admin@example.org",
        "--scope",
        "full",
        secret=secret,
    )
    approve_now(
        admin.service, automation.normalized_code(document["result"]["user_code"])
    )
    from parishkit.stewardship.accounts.authentication import runtime

    limiter = runtime().limiter
    for key in limiter.client.scan_iter(limiter.namespace + ":automation-pairing*"):
        limiter.client.delete(key)
    code, document, _ = admin("login", "wait", "--name", "ops", secret=secret)
    assert code == 5 and document["error"]["code"] == "pairing_expired"
    assert AutomationSession.objects.get().end_reason == "pairing_abandoned"


def test_read_only_sessions_can_read(admin, google):
    """A read-only session runs the read commands."""
    browser, _ = signed_in()
    secret = pair(admin, browser, scope="read-only", days=2)
    code, document, stderr = admin("whoami", secret=secret)
    assert code == 0 and document["result"]["scope"] == "read_only"
    # Less than 72 hours left: a warning on standard error, not in the document.
    assert "WARNING: this automation session expires" in stderr


def test_unknown_secrets_and_other_hosts_exit_5(admin, google):
    """An unknown secret is session_missing; a host mismatch ends the session."""
    code, document, _ = admin("whoami", secret=secrets.token_urlsafe(32))
    assert code == 5 and document["error"]["code"] == "session_missing"
    browser, _ = signed_in()
    secret = pair(admin, browser)
    code, document, _ = admin("whoami", secret=secret, host=OTHER_HOST)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert AutomationSession.objects.get().end_reason == "host_mismatch"


def test_revocation_in_the_portal_stops_the_next_command(admin, google):
    """Revoked from Automation access: the next command exits 5."""
    browser, _ = signed_in()
    secret = pair(admin, browser)
    row = AutomationSession.objects.get()
    from parishkit.stewardship.accounts.policy import current_principal

    administrator = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, administrator, reason="revoked_by_owner")
    code, document, _ = admin("whoami", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended"


def test_a_session_survives_a_new_process(admin, google):
    """Each command is a new process admitting the same file again."""
    browser, _ = signed_in()
    secret = pair(admin, browser)
    for _ in range(3):
        code, _, _ = admin("whoami", secret=secret)
        assert code == 0
    with unguarded():
        AutomationSession.objects.update(
            expires_at=database_now() + timedelta(hours=1),
            authenticated_at=F("authenticated_at"),
        )
    code, _, stderr = admin("whoami", secret=secret)
    assert code == 0 and "expires in 0 hours" in stderr


def test_two_concurrent_waits_collect_the_session_once(admin, google):
    """Two waits racing on one approval: one collects, the other finds it collected.

    The row lock is taken before the pairing request is consumed, so the
    session is collected (and its use recorded) exactly once and neither
    wait revokes it as abandoned.
    """
    import threading

    from django.db import connections

    signed_in()
    secret = secrets.token_urlsafe(32)
    _, document, _ = admin(
        "login",
        "start",
        "--name",
        "ops",
        "--label",
        "race",
        "--expect-email",
        "admin@example.org",
        "--scope",
        "full",
        secret=secret,
    )
    approve_now(
        admin.service, automation.normalized_code(document["result"]["user_code"])
    )
    barrier, results = threading.Barrier(2), []

    def wait():
        """One ``login wait`` in its own thread and database connection."""
        try:
            barrier.wait()
            results.append(admin("login", "wait", "--name", "ops", secret=secret))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wait) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert [code for code, _, _ in results] == [0, 0]
    names = [document["result"].get("name") for _, document, _ in results]
    assert names.count("ops") == 1 and names.count(None) == 1
    row = AutomationSession.objects.get()
    assert row.revoked_at is None and row.last_used_at is not None
    # Inserted at version 1, then exactly one recorded first use.
    assert row.version == 2
