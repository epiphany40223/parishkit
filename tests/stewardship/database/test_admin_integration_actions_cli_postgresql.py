"""``integration backup-probe`` and ``integration dismiss`` (ADM-11 PR 10).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
queue the Off-site backups page's Test access check and dismiss a key change
line through the pages' own ``queue_probe`` and ``dismiss``. Nothing here
contacts Google: a check is only queued, and the Workspace installer that
answers it does not run.
"""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from parishkit.stewardship import admin_integration_actions
from parishkit.stewardship.accounts import integration_credentials, integration_views
from parishkit.stewardship.accounts.integration_credentials import CredentialSummary
from parishkit.stewardship.accounts.secret_requests import (
    cancel_secret_request,
    clean_secret_request,
    stage_secret_request,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.backup_models import BackupDriveProbe

from . import campaign_builders
from .automation_builders import as_staff, paired
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner

pytestmark = pytest.mark.django_db(transaction=True)
PROBE = "admin_cmd_integration_backup_probe"
DISMISS = "admin_cmd_integration_dismiss"
PAGE_DISMISS = "credential_result_dismissed"
FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"
LINK = f"https://drive.google.com/drive/folders/{FOLDER}"
OTHER = "0OtherFolder1234567890"
MAILBOX = "mail@example.org"


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


def with_records(monkeypatch, request, *records):
    """The ``admin`` runner over a configuration with extra ``records``."""
    original = campaign_builders.configuration_document

    def document():
        """The synthetic configuration, plus ``records``."""
        value = original()
        value["sections"]["integrations"] += [
            {
                "id": str(uuid4()),
                "values": {
                    "kind": kind,
                    "settings": settings,
                    "credential_fingerprint": fingerprint,
                },
            }
            for kind, settings, fingerprint in records
        ]
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    return request.getfixturevalue("admin")


WORKSPACE = ("google_workspace", {"delegated_email": MAILBOX}, "b" * 64)


@pytest.fixture
def backup(monkeypatch, request):
    """Workspace mail and a saved off-site folder."""
    return with_records(
        monkeypatch, request, WORKSPACE, ("backup", {"target": LINK}, None)
    )


@pytest.fixture
def workspace(monkeypatch, request):
    """Workspace mail, but no off-site folder saved yet."""
    return with_records(monkeypatch, request, WORKSPACE)


def probe(admin, secret, folder=None):
    """Run ``integration backup-probe`` (``--folder`` when given)."""
    extra = () if folder is None else ("--folder", folder)
    return admin("integration", "backup-probe", *extra, secret=secret)


def dismiss(admin, secret, target, request_id):
    """Run ``integration dismiss``."""
    return admin(
        "integration", "dismiss", target, "--request-id", str(request_id), secret=secret
    )


def refused(document, code):
    """The error document's code is ``code``, with no result; its problems."""
    assert document["error"]["code"] == code, document
    assert "result" not in document
    return [
        (problem["field"], problem["code"])
        for problem in document["error"].get("fields", [])
    ]


def count(event):
    """How many ``event`` audit rows exist."""
    return AuditEvent.objects.filter(event_type=event).count()


def test_the_saved_folder_is_checked_as_the_page_does(backup):
    """The page's check, as the delegated mailbox user, with the command's
    event naming the session; the status line then shows it pending."""
    _, secret, row = paired(backup.service)
    code, document, _ = probe(backup, secret)
    assert code == 0, document
    result = document["result"]
    assert result["pending"] and result["probe"]["state"] == "pending"
    assert result["probe"]["folder_url"] == LINK
    queued = BackupDriveProbe.objects.get()
    assert (queued.requested_by_id, queued.folder_id, queued.subject) == (
        row.principal_id,
        FOLDER,
        MAILBOX,
    )
    assert queued.state == "pending"
    event = AuditEvent.objects.get(event_type=PROBE)
    assert event.subject_id == row.pk and event.actor_id == row.principal_id
    code, status, _ = backup("integration", "status", "backup", secret=secret)
    assert code == 0 and status["result"]["probe"] == result["probe"]


def test_a_given_folder_link_is_checked_before_it_is_saved(workspace):
    """``--folder`` is the page's link field; any folder link form is read."""
    _, secret, _ = paired(workspace.service)
    code, document, _ = probe(workspace, secret, f"{LINK}?usp=sharing")
    assert code == 0, document
    assert BackupDriveProbe.objects.get().folder_id == FOLDER
    assert count(PROBE) == 1


@pytest.mark.parametrize(
    ("folder", "problem"),
    [(None, "required"), ("https://example.org/not-drive", "invalid")],
)
def test_no_folder_or_a_bad_link_is_invalid(workspace, folder, problem):
    """No folder given and none saved, or a link that is not a Drive folder,
    at ``folder`` with the page's words."""
    _, secret, _ = paired(workspace.service)
    code, document, _ = probe(workspace, secret, folder)
    assert code == 1
    assert refused(document, "invalid") == [("folder", problem)]
    assert "Google Drive folder" in document["error"]["fields"][0]["message"]
    assert not BackupDriveProbe.objects.exists() and count(PROBE) == 0


def test_workspace_not_set_up_is_invalid(admin):
    """The check signs in as the Workspace mailbox user, so it needs one."""
    _, secret, _ = paired(admin.service)
    code, document, _ = probe(admin, secret, LINK)
    assert code == 1
    assert refused(document, "invalid") == [("google_workspace", "required")]
    assert "Google Workspace" in document["error"]["fields"][0]["message"]
    assert not BackupDriveProbe.objects.exists() and count(PROBE) == 0


def test_the_local_environment_never_checks(backup, monkeypatch):
    """Without a mail redirect the local laptop environment never reaches
    Google Drive (#1399)."""
    from parishkit.stewardship import deployment

    monkeypatch.setattr(
        deployment, "recorded_profile", lambda: deployment.DeploymentProfile.LOCAL
    )
    _, secret, _ = paired(backup.service)
    code, document, _ = probe(backup, secret)
    # Refused before the configuration is looked at: no field problem.
    assert code == 1 and refused(document, "invalid") == []
    assert not BackupDriveProbe.objects.exists() and count(PROBE) == 0


def test_a_changed_workspace_mailbox_is_stale(backup, monkeypatch):
    """The SQL guard refuses a subject that is not the applied mailbox user:
    another Administrator's Workspace change in between is stale_version."""
    original = integration_views._records

    def changed(configuration):
        """The records as a page read before the Workspace change saw them."""
        value = deepcopy(original(configuration))
        value["google_workspace"]["values"]["settings"]["delegated_email"] = (
            "other@example.org"
        )
        return value

    monkeypatch.setattr(integration_views, "_records", changed)
    _, secret, _ = paired(backup.service)
    code, document, _ = probe(backup, secret)
    assert code == 1
    refused(document, "stale_version")
    assert not BackupDriveProbe.objects.exists() and count(PROBE) == 0


def line(monkeypatch, kind, request_id):
    """Show ``kind`` as the latest key change line for ``request_id``."""
    shown = CredentialSummary(kind, timezone.now(), "", request_id)
    monkeypatch.setattr(integration_credentials, "summary", lambda *_: shown)


@pytest.mark.parametrize("kind", ["updated", "failed"])
def test_a_finished_line_is_dismissed_with_both_events(admin, monkeypatch, kind):
    """The page's event about the request plus the command's event."""
    _, secret, row = paired(admin.service)
    request_id = uuid4()
    line(monkeypatch, kind, request_id)
    code, document, _ = dismiss(admin, secret, "parishsoft", request_id)
    assert code == 0, document
    assert document["result"] == {
        "target": "parishsoft",
        "request_id": str(request_id),
        "dismissed": True,
    }
    page = AuditEvent.objects.get(event_type=PAGE_DISMISS)
    assert page.subject_id == request_id and page.actor_id == row.principal_id
    event = AuditEvent.objects.get(event_type=DISMISS)
    assert event.subject_id == row.pk


def test_a_pending_or_other_line_dismisses_nothing(admin, monkeypatch):
    """A staged change still running, or a request the page no longer shows,
    is the page's silent no-op: dismissed false, nothing recorded."""
    _, secret, row = paired(admin.service)
    # The settings page's own intake, with synthetic references: no key.
    request_id = staged(row)
    for target, wanted in (("parishsoft", request_id), ("parishsoft", uuid4())):
        code, document, _ = dismiss(admin, secret, target, wanted)
        assert code == 0, document
        assert document["result"]["dismissed"] is False
    # Slack is optional: not set up, it has no line to dismiss.
    code, document, _ = dismiss(admin, secret, "slack", request_id)
    assert code == 0 and document["result"]["dismissed"] is False
    assert count(PAGE_DISMISS) == 0 and count(DISMISS) == 0


def staged(row):
    """Stage a synthetic ParishSoft key change (no key); return its id."""
    request_id = uuid4()
    stage_secret_request(
        request_id=request_id,
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=row.principal_id,
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint="a" * 64,
        correlation_id=uuid4(),
    )
    return request_id


def finished(row):
    """A real finished (cancelled, so ``failed``) key change's id."""
    request_id = staged(row)
    cancel_secret_request(
        request_id=request_id, actor_id=row.principal_id, correlation_id=uuid4()
    )
    # Nothing was staged (synthetic reference), so cleanup removes nothing.
    clean_secret_request(
        request_id=request_id,
        target="parishsoft",
        correlation_id=uuid4(),
        remove_payload=lambda reference: None,
    )
    return request_id


def test_a_real_finished_line_is_dismissed_once(admin):
    """Against the real line: the first dismissal hides it with both events;
    a repeat finds nothing shown, dismisses nothing and records nothing."""
    _, secret, row = paired(admin.service)
    request_id = finished(row)
    code, shown, _ = admin("integration", "show", "parishsoft", secret=secret)
    assert code == 0 and shown["result"]["key"]["state"] == "failed"
    assert shown["result"]["key"]["request_id"] == str(request_id)
    results = [dismiss(admin, secret, "parishsoft", request_id) for _ in range(2)]
    assert [code for code, _, _ in results] == [0, 0]
    assert [document["result"]["dismissed"] for _, document, _ in results] == [
        True,
        False,
    ]
    assert count(PAGE_DISMISS) == 1 and count(DISMISS) == 1
    code, shown, _ = admin("integration", "show", "parishsoft", secret=secret)
    assert code == 0 and shown["result"]["key"] is None


def test_a_failed_command_event_rolls_the_dismissal_back(admin, monkeypatch):
    """The page's event and the command's share one transaction: when the
    command's cannot be written, neither is, and the line stays shown."""
    from django.db import DatabaseError

    _, secret, row = paired(admin.service)
    request_id = finished(row)

    def failing(*_args):
        """Write nothing; fail as the database would."""
        raise DatabaseError("synthetic failure")

    original = admin_integration_actions._command_event
    monkeypatch.setattr(admin_integration_actions, "_command_event", failing)
    code, document, _ = dismiss(admin, secret, "parishsoft", request_id)
    assert code != 0 and "result" not in document
    assert count(PAGE_DISMISS) == 0 and count(DISMISS) == 0
    # Only this patch is put back: the runner's own patches stay.
    monkeypatch.setattr(admin_integration_actions, "_command_event", original)
    code, document, _ = dismiss(admin, secret, "parishsoft", request_id)
    assert code == 0 and document["result"]["dismissed"] is True


def test_a_malformed_saved_folder_is_an_invalid_link(backup, monkeypatch):
    """With no --folder, a saved link that is not a Drive folder (an older or
    restored configuration) is ``invalid`` at ``folder``, not ``required``."""
    original = integration_views._records

    def malformed(configuration):
        """The records with the saved folder link broken."""
        value = deepcopy(original(configuration))
        value["backup"]["values"]["settings"]["target"] = "https://example.org/x"
        return value

    monkeypatch.setattr(integration_views, "_records", malformed)
    _, secret, _ = paired(backup.service)
    code, document, _ = probe(backup, secret)
    assert code == 1
    assert refused(document, "invalid") == [("folder", "invalid")]
    assert not BackupDriveProbe.objects.exists() and count(PROBE) == 0


def test_a_required_integration_not_set_up_is_not_available(admin):
    """Google Workspace mail not set up: the page's 404."""
    _, secret, _ = paired(admin.service)
    code, document, _ = dismiss(admin, secret, "google_workspace", uuid4())
    assert code == 1
    refused(document, "not_available")


def test_refused_sessions(backup, monkeypatch):
    """A read-only session and Staff are denied; an ended session is exit 5;
    nothing is queued or recorded."""
    request_id = uuid4()
    line(monkeypatch, "failed", request_id)
    _, reader, _ = paired(backup.service, scope="read-only")
    for code, document, _ in (
        probe(backup, reader),
        dismiss(backup, reader, "parishsoft", request_id),
    ):
        assert code == 1
        refused(document, "denied")
    _, secret, _ = paired(backup.service)
    revoke(backup.service, secret)
    for code, document, _ in (
        probe(backup, secret),
        dismiss(backup, secret, "parishsoft", request_id),
    ):
        assert code == 5
        refused(document, "session_ended")
    _, staff, _ = paired(backup.service)
    as_staff(monkeypatch)
    for code, document, _ in (
        probe(backup, staff),
        dismiss(backup, staff, "parishsoft", request_id),
    ):
        assert code == 1
        refused(document, "denied")
    assert not BackupDriveProbe.objects.exists()
    assert count(PROBE) == count(DISMISS) == count(PAGE_DISMISS) == 0
