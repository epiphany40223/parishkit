"""Off-site backup copies: recorded outcomes, access checks, alerts and the web."""

from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import backup_offsite, backup_probes
from parishkit.stewardship.accounts import key_files
from parishkit.stewardship.accounts.backup_destination import (
    latest_probe,
    offsite_status,
    request_probe,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.backup_offsite import SEALED_FILES, copy_offsite
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import backup_health
from parishkit.stewardship.jobs.backup_models import BackupDriveProbe, BackupUpload
from parishkit.stewardship.jobs.operational_content import IncidentKind
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.ownership import database_now

from ..drive_fakes import FakeDrive
from . import campaign_builders
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_integration_views_postgresql import hidden, post
from .test_setup_exchange_postgresql import target_login

pytestmark = pytest.mark.django_db(transaction=True)
FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"
LINK = f"https://drive.google.com/drive/folders/{FOLDER}"
URL = "/admin/configuration/integrations/backup"


@pytest.fixture
def offsite(tmp_path, monkeypatch):
    """A backup profile with one local set, a fake Drive and a fake key file."""
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    directory = backups / "20260927T020000Z"
    directory.mkdir(mode=0o700)
    for name in SEALED_FILES:
        (directory / name).write_bytes(b"sealed " + name.encode())
    drive = FakeDrive(FOLDER)
    target = [(FOLDER, "mail@example.org")]
    monkeypatch.setattr(backup_offsite, "destination", lambda: target[0])
    monkeypatch.setattr(backup_offsite, "DriveClient", lambda session, tag: drive)
    monkeypatch.setattr(backup_offsite, "set_tag", lambda: drive.tag)
    monkeypatch.setattr(
        backup_offsite, "RuntimeLayout", lambda c: SimpleNamespace(credential=str)
    )
    monkeypatch.setattr(key_files, "read_private", lambda path: b"key")
    return SimpleNamespace(
        configuration=SimpleNamespace(paths={"backups": backups}),
        drive=drive,
        target=target,
        directory=directory,
    )


def copy(offsite):
    """Copy under the real backup identity, never sleeping between retries."""
    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        return copy_offsite(
            offsite.configuration,
            session_factory=lambda value, subject: None,
            sleep=lambda seconds: None,
        )


def observe():
    """Observe as the worker that runs the operational collection."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        backup_health.observe_backup_health()


def episode():
    """The open off-site failure episode, if any."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.BACKUP_OFFSITE_FAILED, resolved_at__isnull=True
    ).first()


def test_a_copy_is_recorded_once_and_the_rows_are_immutable(offsite):
    """The backup login records the copy; a second run uploads nothing."""
    assert copy(offsite) == {"state": "uploaded", "sets": 1}
    assert offsite.drive.sets() == [offsite.directory.name]
    row = BackupUpload.objects.get()
    assert (row.state, row.set_name, row.folder_id) == (
        "uploaded",
        offsite.directory.name,
        FOLDER,
    )
    assert len(row.manifest_digest) == 64
    assert copy(offsite) == {"state": "uploaded", "sets": 0}
    assert BackupUpload.objects.count() == 1
    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupUpload.objects.filter(pk=row.pk).update(state="failed")
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupUpload.objects.filter(pk=row.pk).delete()
    status = offsite_status()
    assert status.kind == "uploaded" and status.set_name == offsite.directory.name


def other_session():
    """A second autocommit session, as a concurrent portal request arrives."""
    settings = connection.settings_dict
    return psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        dbname=settings["NAME"],
        autocommit=True,
    )


def test_a_slow_upload_holds_no_lock_the_portals_need(offsite, monkeypatch):
    """While a file is in flight, the copy holds no transaction or lock.

    A portal write takes the work-order lock and row locks; during the
    stubbed slow upload another session takes both at once, and the
    backup's own session has no open transaction and no advisory or
    stewardship-table lock.
    """
    seen = []
    upload = offsite.drive.upload

    def slow(path, name, parent, **kwargs):
        """Look at the database from inside the "network" call."""
        assert not connection.in_atomic_block
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            backend = cursor.fetchone()[0]
        with other_session() as other:
            held = other.execute(
                "SELECT count(*) FROM pg_locks l "
                "LEFT JOIN pg_class c ON c.oid = l.relation "
                "WHERE l.pid = %s AND (l.locktype = 'advisory' "
                "OR l.locktype = 'transactionid' "
                "OR c.relname LIKE 'stewardship_%%')",
                (backend,),
            ).fetchone()[0]
            idle = other.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE pid = %s AND state = 'idle in transaction'",
                (backend,),
            ).fetchone()[0]
            with other.transaction():
                taken = other.execute(
                    "SELECT pg_try_advisory_xact_lock(736220, 1)"
                ).fetchone()[0]
                other.execute(
                    "SELECT id FROM stewardship_backup_upload FOR UPDATE NOWAIT"
                ).fetchall()
            seen.append((held, idle, taken))
        return upload(path, name, parent, **kwargs)

    monkeypatch.setattr(offsite.drive, "upload", slow)
    assert copy(offsite)["state"] == "uploaded"
    assert seen and all(item == (0, 0, True) for item in seen)


def test_the_whole_copy_is_bounded_in_time(offsite):
    """Past the copy's deadline no set is started; the outcome is recorded."""
    ticks = iter([0, backup_offsite.COPY_SECONDS + 1])
    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        result = copy_offsite(
            offsite.configuration,
            session_factory=lambda value, subject: None,
            sleep=lambda seconds: None,
            clock=lambda: next(ticks),
        )
    assert result == {"state": "failed", "failure_kind": "unavailable"}
    assert offsite.drive.calls == []


def test_a_failed_copy_alerts_until_a_copy_succeeds(offsite):
    """A refused folder records a failure and opens a critical incident."""
    offsite.drive.fail["create_folder"] = ["permission"]
    assert copy(offsite) == {"state": "failed", "failure_kind": "permission"}
    assert BackupUpload.objects.get().failure_kind == "permission"
    with work_transaction():
        assert backup_health.needs_backup_observation()
    observe()
    opened = episode()
    assert opened is not None and opened.signal_level == "CRITICAL"
    assert offsite_status().kind == "failed"
    assert copy(offsite)["state"] == "uploaded"
    observe()
    assert episode() is None


def test_an_unexpected_failure_is_still_recorded_and_alerted(offsite, monkeypatch):
    """A failure outside the Drive categories records a row and alerts."""

    def broken(*args, **kwargs):
        raise ValueError("malformed provider reply")

    monkeypatch.setattr(offsite.drive, "upload", broken)
    assert copy(offsite) == {"state": "failed", "failure_kind": "unexpected"}
    assert BackupUpload.objects.get().failure_kind == "unexpected"
    observe()
    assert episode() is not None


def test_transient_failures_are_retried_then_recorded(offsite):
    """Unavailable Drive is retried; only the last failure is recorded."""
    offsite.drive.fail["create_folder"] = ["unavailable"] * 3
    assert copy(offsite)["failure_kind"] == "unavailable"
    assert offsite.drive.calls.count("create_folder") == 3
    offsite.drive.fail["create_folder"] = ["unavailable"]
    assert copy(offsite)["state"] == "uploaded"


def test_removing_the_destination_records_that_copies_stopped(offsite):
    """A deliberate turn-off clears an old failure without repeating rows."""
    offsite.drive.fail["create_folder"] = ["permission"]
    copy(offsite)
    offsite.target[0] = None
    assert copy(offsite) == {"state": "not_configured"}
    assert copy(offsite) == {"state": "not_configured"}
    states = BackupUpload.objects.order_by("created_at").values_list("state", flat=True)
    assert list(states) == ["failed", "disabled"]
    assert offsite_status().kind == "disabled"
    observe()
    assert episode() is None


def test_the_installer_answers_access_checks(monkeypatch):
    """The Workspace installer login completes checks; the guard keeps them final."""
    drive = FakeDrive(FOLDER)
    monkeypatch.setattr(key_files, "read_private", lambda path: b"key")
    actor = uuid4()
    first = request_probe(actor, FOLDER, "mail@example.org")
    missing = request_probe(actor, "0MissingFolder123", "mail@example.org")
    broken = request_probe(actor, FOLDER, "mail@example.org")
    calls = []

    def client(session):
        """The third check's Drive reply is malformed."""
        calls.append(session)
        if len(calls) == 3:
            raise ValueError("malformed provider reply")
        return drive

    with transaction.atomic():
        assert latest_probe(actor, database_now()).kind == "pending"
    monkeypatch.setattr(backup_probes, "DriveClient", client)
    with target_login("google_workspace"):
        assert (
            backup_probes.run_pending_probes(
                "unused", session_factory=lambda value, subject: None
            )
            == 3
        )
    for row in (first, missing, broken):
        row.refresh_from_db()
    assert (broken.state, broken.failure_kind) == ("failed", "unexpected")
    assert first.state == "succeeded" and first.completed_at is not None
    assert (missing.state, missing.failure_kind) == ("failed", "not_found")
    with pytest.raises(DatabaseError), transaction.atomic():
        BackupDriveProbe.objects.filter(pk=first.pk).update(state="failed")
    with pytest.raises(DatabaseError), transaction.atomic():
        BackupDriveProbe.objects.filter(pk=first.pk).delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        BackupDriveProbe.objects.create(
            requested_by_id=actor,
            folder_id=FOLDER,
            subject="mail@example.org",
            state="succeeded",
        )


@pytest.fixture
def workspace(monkeypatch, request):
    """An applied configuration that already sends mail through Workspace."""
    original = campaign_builders.configuration_document

    def document():
        value = original()
        value["sections"]["integrations"].append(
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "google_workspace",
                    "settings": {"delegated_email": "mail@example.org"},
                    "credential_fingerprint": "b" * 64,
                },
            }
        )
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    request.getfixturevalue("google")
    return request.getfixturevalue("auth_service")


def apply(store, response):
    """Install the queued configuration change the web just recorded."""
    assert response.status_code == 302, response.content
    row = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    result = install_request(store, request_id=row.pk, correlation_id=uuid4())
    assert result.state == "applied"


def backups(store):
    """The applied backup integration settings, if any."""
    return [
        item["values"]
        for item in store.active().document()["sections"]["integrations"]
        if item["values"]["kind"] == "backup"
    ]


def test_admin_sets_tests_and_removes_the_drive_folder(workspace):
    """The Backups page adds the folder, queues a check, and turns copies off."""
    store = workspace.store
    browser, _ = signed_in()
    assert b"Off-site backups are not set up" in browser.get("/admin/").content
    assert (
        b"Off-site backups (Google Drive)"
        in browser.get("/admin/configuration/integrations").content
    )
    page = browser.get(URL)
    assert page.status_code == 200
    assert b"Google Drive folder link" in page.content
    assert b"https://www.googleapis.com/auth/drive" in page.content
    response = post(
        browser,
        URL,
        {"action": "test", "target": f"{LINK}?usp=sharing"},
    )
    assert response.status_code == 302
    probe = BackupDriveProbe.objects.get()
    assert (probe.folder_id, probe.subject) == (FOLDER, "mail@example.org")
    status = browser.get(URL + "/status")
    assert status.status_code == 200 and b"Checking access" in status.content
    assert post(browser, URL, {"action": "test", "target": "x"}).status_code == 400
    preview = hidden(
        post(
            browser,
            URL,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "target": f"{LINK}?usp=sharing",
            },
        ),
        "preview",
    )
    apply(store, post(browser, URL, {"action": "confirm", "preview": preview}))
    assert backups(store) == [
        {
            "kind": "backup",
            "settings": {"target": LINK},
            "credential_fingerprint": None,
        }
    ]
    BackupUpload.objects.create(
        state="uploaded",
        set_name="20260927T020000Z",
        manifest_digest="a" * 64,
        folder_id=FOLDER,
    )
    home = browser.get("/admin/")
    assert b"Off-site backups" in home.content
    assert b"copied to Google Drive" in browser.get(URL).content
    preview = hidden(post(browser, URL, {"action": "remove"}), "preview")
    apply(store, post(browser, URL, {"action": "confirm", "preview": preview}))
    assert backups(store) == []


def test_a_check_that_waited_too_long_closes_unanswered(monkeypatch):
    """The installer never runs a stale check; the page already said so."""
    from datetime import timedelta

    monkeypatch.setattr(backup_probes, "PROBE_WAIT", timedelta(0))
    monkeypatch.setattr(
        backup_probes, "DriveClient", lambda session: pytest.fail("contacted Drive")
    )
    actor = uuid4()
    row = request_probe(actor, FOLDER, "mail@example.org")
    checks = []
    with target_login("google_workspace"):
        assert (
            backup_probes.run_pending_probes("unused", check=lambda: checks.append(1))
            == 1
        )
    assert checks == [1, 1]
    row.refresh_from_db()
    assert (row.state, row.failure_kind) == ("failed", "unanswered")
    with transaction.atomic():
        assert latest_probe(actor, database_now()).kind == "unanswered"
