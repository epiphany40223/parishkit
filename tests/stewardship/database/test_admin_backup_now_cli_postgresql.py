"""``pk-admin system backup-now``: Take a backup now from the command line (#530).

The command shows the page's preview at the prompt and records the request
through the page's own ``request_backup`` on the restricted web login. The
request's guard admits the full-scope automation session as the fresh
sign-in (a session whose sign-in is older than five minutes here), and the
command records ``backup_requested`` with its own
``admin_cmd_system_backup_now`` and the fresh gate's trail.
"""

from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.backup_models import BackupRequest

from .auth_builders import unguarded
from .automation_builders import paired
from .test_admin_test_sample_cli_postgresql import runner

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def backup_now(admin, secret, *options, answer=b""):
    """``system backup-now`` with ``options``, ``answer`` typed at the prompt."""
    return admin("system", "backup-now", *options, secret=secret, answer=answer)


def count(event):
    """How many audit events of this type were recorded."""
    return AuditEvent.objects.filter(event_type=event).count()


def test_a_backup_is_requested_from_the_command_line(admin):
    """The prompt shows the preview; the request, its events and a safe repeat."""
    _, secret, row = paired(admin.service)
    # The session's sign-in is ten minutes old: only the automation session
    # stands in for a fresh one.
    older = row.authenticated_at - timedelta(minutes=10)
    with unguarded():
        AutomationSession.objects.filter(pk=row.pk).update(authenticated_at=older)
        PortalSession.objects.filter(principal_id=row.principal_id).update(
            authenticated_at=older
        )
    for answer in (b"", b"no\n"):
        code, refused, errors = backup_now(admin, secret, answer=answer)
        assert code == 4 and refused["error"]["code"] == "confirmation_required"
        assert "Off-site copies are not set up" in errors
        assert "No backup has been recorded yet." in errors
    assert not BackupRequest.objects.exists()

    key = str(uuid4())
    code, document, _ = backup_now(admin, secret, "--request-key", key, answer=b"yes\n")
    assert code == 0, document
    result = document["result"]
    assert result["request_id"] == key and result["created"] is True
    assert result["status"] == "waiting" and result["created_at"]
    request = BackupRequest.objects.get()
    assert request.actor_id == row.principal_id and request.state == "waiting"
    assert count("backup_requested") == count("admin_cmd_system_backup_now") == 1
    [event] = AuditEvent.objects.filter(event_type="admin_cmd_system_backup_now")
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert count("automation_fresh_gate") == 1

    # The same key again returns the first request and records nothing new.
    code, document, _ = backup_now(admin, secret, "--request-key", key, "--yes")
    assert code == 0 and document["result"]["created"] is False
    assert BackupRequest.objects.count() == 1
    assert count("backup_requested") == 1

    # Another request while this one waits is refused before the prompt.
    code, refused, _ = backup_now(admin, secret, "--yes")
    assert code == 1 and refused["error"]["code"] == "stale_version"
    assert BackupRequest.objects.count() == 1


def test_a_read_only_session_cannot_request_a_backup(admin):
    """Take a backup now needs a full-scope session, as every change does."""
    _, reader, _ = paired(admin.service, scope="read-only")
    code, refused, _ = backup_now(admin, reader, "--yes")
    assert code != 0 and refused["error"]["code"] in {"denied", "scope_required"}
    assert not BackupRequest.objects.exists()
