"""The Family portal maintenance commands of ``pk-stewardship admin`` (PR 7a).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The switch is
read and changed through the page's own ``family_maintenance`` functions, so
the page and the command line see the same state and audit trail; changing
it is fresh-gated, and a full-scope session stands in for the sign-in.
"""

import pytest

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts import family_maintenance
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.audit.models import AuditEvent

from .automation_builders import paired
from .test_admin_test_sample_cli_postgresql import runner
from .test_family_maintenance_postgresql import change, events, family, maintenance

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "admin_cmd_portal_maintenance_set"


@pytest.fixture(autouse=True)
def fresh_state():
    """No test sees another test's cached switch state."""
    family_maintenance._cache.update(at=None)
    yield
    family_maintenance._cache.update(at=None)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def show(admin, secret):
    """``portal maintenance show``."""
    return admin("portal", "maintenance", "show", secret=secret)


def switch(admin, secret, *options, answer=b""):
    """``portal maintenance set`` with ``options``, ``answer`` typed at the prompt."""
    return admin("portal", "maintenance", "set", *options, secret=secret, answer=answer)


def trail():
    """The command events, fresh-gate events and fresh-gated notices so far."""
    return (
        AuditEvent.objects.filter(event_type=EVENT).count(),
        AuditEvent.objects.filter(event_type="automation_fresh_gate").count(),
        AutomationNotice.objects.filter(kind="fresh_gated").count(),
    )


def test_the_portal_is_closed_and_reopened_from_the_command_line(admin):
    """The prompt; the page's events; the fresh gate's trail; the Families' view."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, _ = show(admin, reader)
    assert code == 0, document
    assert document["result"] == {
        "closed": False,
        "since": None,
        "message": "",
        "closed_by_id": None,
    }
    # A read records nothing.
    assert events() == [] and trail() == (0, 0, 0)

    close = ("--state", "closed", "--message", "  Back by 3 PM.  ")
    # Refused prompts change nothing, and the prompt shows the page's notices.
    for answer in (b"", b"no\n"):
        code, refused, errors = switch(admin, secret, *close, answer=answer)
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "wait about 10 seconds" in errors
        assert "does not stop email" in errors
        assert events() == [] and trail() == (0, 0, 0)
    assert not maintenance(family("get", "/"))

    code, document, _ = switch(admin, secret, *close, answer=b"yes\n")
    assert code == 0, document
    result = document["result"]
    assert result["closed"] is True and result["message"] == "Back by 3 PM."
    assert result["closed_by_id"] == str(row.principal_id) and result["since"]
    assert events() == ["family_maintenance_started"]
    # The page's event is the Administrator's; the command's names the session.
    started = AuditEvent.objects.get(event_type="family_maintenance_started")
    assert started.actor_id == row.principal_id
    assert started.auditcontext.context == {"review_reason": "Back by 3 PM."}
    [event] = AuditEvent.objects.filter(event_type=EVENT)
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert event.auditcontext.context == {"outcome": "succeeded"}
    assert trail() == (1, 1, 1)
    closed = family("get", "/")
    assert closed.status_code == 503 and maintenance(closed)
    assert b"Back by 3 PM." in closed.content
    code, document, _ = show(admin, reader)
    assert code == 0 and document["result"] == result

    # Closing a closed portal is the page's refusal: stale, nothing recorded.
    code, stale, _ = switch(admin, secret, "--state", "closed", "--yes")
    assert code == 1 and stale["error"]["code"] == "stale_version", stale
    assert events() == ["family_maintenance_started"] and trail() == (1, 1, 1)

    # The page reopens what the command line closed, and the other way round.
    assert change(paired(admin.service)[0], "open").status_code == 302
    code, document, _ = switch(admin, secret, "--state", "closed", "--yes")
    assert code == 0 and document["result"]["message"] == "", document
    code, document, errors = switch(admin, secret, "--state", "open", answer=b"yes\n")
    assert code == 0, document
    assert "Reopen the Family portal" in errors
    assert document["result"] == {
        "closed": False,
        "since": None,
        "message": "",
        "closed_by_id": None,
    }
    assert events() == [
        "family_maintenance_started",
        "family_maintenance_ended",
        "family_maintenance_started",
        "family_maintenance_ended",
    ]
    # One command event, fresh-gate event and notice per command.
    assert trail() == (3, 3, 3)
    assert not maintenance(family("get", "/"))


def test_a_note_from_standard_input(admin):
    """``--message -`` reads the note after the preamble, and needs ``--yes``."""
    _, secret, _ = paired(admin.service)
    code, document, _ = switch(
        admin, secret, "--state", "closed", "--message", "-", answer=b"Soon.\n"
    )
    assert code == 2 and document["error"]["code"] == "usage"
    code, document, _ = switch(
        admin,
        secret,
        "--state",
        "closed",
        "--message",
        "-",
        "--yes",
        answer=b"Back soon.\n",
    )
    assert code == 0 and document["result"]["message"] == "Back soon.", document


def test_refusals_change_nothing(admin):
    """Bad notes, a read-only session and an ended session."""
    _, secret, row = paired(admin.service)
    _, reader, _ = paired(admin.service, scope="read-only")
    # The page's rules for the note, checked before any prompt.
    for note in ("Write to office@example.org", "x" * 501):
        code, document, errors = switch(
            admin, secret, "--state", "closed", "--message", note
        )
        assert code == 1 and document["error"]["code"] == "invalid", document
        assert "Type yes" not in errors
    code, document, _ = switch(admin, secret, "--state", "open", "--message", "x")
    assert code == 2 and document["error"]["code"] == "usage"
    # Reopening an open portal is stale, as the page refuses it.
    code, document, _ = switch(admin, secret, "--state", "open", "--yes")
    assert code == 1 and document["error"]["code"] == "stale_version", document
    code, document, _ = switch(admin, reader, "--state", "closed", "--yes")
    assert code == 1 and document["error"]["code"] == "denied"
    actor = current_principal(admin.service.store, row.principal_id)
    automation.revoke(row.pk, actor, reason="revoked_by_owner")
    code, document, _ = switch(admin, secret, "--state", "closed", "--yes")
    assert code == 5 and document["error"]["code"] == "session_ended"
    code, document, _ = show(admin, secret)
    assert code == 5 and document["error"]["code"] == "session_ended"
    assert events() == [] and trail() == (0, 0, 0)
    assert not maintenance(family("get", "/"))
