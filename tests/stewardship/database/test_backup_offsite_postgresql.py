"""Off-site backup copies: recorded outcomes, access checks, alerts and the web."""

import json
import logging
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import backup_offsite, backup_probes, observability
from parishkit.stewardship.accounts import backup_destination, key_files
from parishkit.stewardship.accounts.backup_destination import (
    latest_probe,
    offsite_status,
    request_probe,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.backup_drive import PROBE_WAIT
from parishkit.stewardship.backup_offsite import SEALED_FILES, copy_offsite
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import backup_health
from parishkit.stewardship.jobs.backup_models import (
    BackupDriveProbe,
    BackupRun,
    BackupUpload,
)
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


def logged(caplog):
    """The structured fields of each formatted process-log line so far."""
    return [
        json.loads(observability.SafeJsonFormatter().format(record)).get("extra", {})
        for record in caplog.records
    ]


def copy(offsite):
    """Copy under the real backup identity, never sleeping between retries.

    The copy closes its idle connection before uploading, so the test role
    must survive a reconnect.
    """
    with task_login(ServiceRole.BACKUP_WORKER, exact=True, reconnect=True):
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


def test_the_whole_copy_is_bounded_in_time(offsite, caplog):
    """Past the copy's deadline no set is started; the outcome is recorded."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    ticks = iter([0, backup_offsite.COPY_SECONDS + 1])
    with task_login(ServiceRole.BACKUP_WORKER, exact=True, reconnect=True):
        result = copy_offsite(
            offsite.configuration,
            session_factory=lambda value, subject: None,
            sleep=lambda seconds: None,
            clock=lambda: next(ticks),
        )
    assert result == {"state": "failed", "failure_kind": "unavailable"}
    assert offsite.drive.calls == []
    # The log tells the stopped copy from a Drive outage: what, limit, elapsed.
    [stopped] = [line for line in logged(caplog) if "timeout" in line]
    assert stopped["timeout"] == "drive_copy_budget"
    assert stopped["limit_seconds"] == backup_offsite.COPY_SECONDS
    assert stopped["elapsed_seconds"] == backup_offsite.COPY_SECONDS + 1


def test_a_failed_copy_alerts_until_a_copy_succeeds(offsite, caplog):
    """A refused folder records a failure and opens a critical incident."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    offsite.drive.fail["create_folder"] = ["permission"]
    assert copy(offsite) == {"state": "failed", "failure_kind": "permission"}
    # The process log names the Drive category, not only that the copy failed.
    assert {
        "failure_kind": "backup_offsite_failed",
        "drive_failure": "permission",
    }.items() <= logged(caplog)[-1].items()
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


def new_set(offsite, name):
    """Add one more complete local set and return its directory."""
    directory = offsite.directory.with_name(name)
    directory.mkdir(mode=0o700)
    for file in SEALED_FILES:
        (directory / file).write_bytes(name.encode() + b" " + file.encode())
    return directory


def record_run(directory):
    """Record the completed backup run for one local set, as the backup does."""
    with transaction.atomic():
        started = database_now()
    run = BackupRun.objects.create(
        started_at=started,
        database_bytes=1,
        files_bytes=1,
        manifest_digest=backup_offsite._digest(directory),
        recipient_fingerprint="c" * 16,
        application_version="test",
    )
    run.refresh_from_db()
    return run


def test_a_copy_that_records_nothing_still_alerts(offsite):
    """A copy killed before recording an outcome is noticed after the grace.

    The newest row stays the previous "uploaded", so without this rule the
    pages would keep showing that success and nothing would alert.
    """
    assert copy(offsite)["state"] == "uploaded"
    # The next backup completes, but its copy dies before recording anything.
    newer = new_set(offsite, "20260928T020000Z")
    run = record_run(newer)
    grace = backup_health.OFFSITE_GRACE
    assert not backup_health.offsite_failing(run.completed_at + grace / 2)
    assert backup_health.offsite_failing(run.completed_at + grace * 2)
    # Once the next copy records that set's outcome, the silence is over.
    assert copy(offsite) == {"state": "uploaded", "sets": 1}
    assert not backup_health.offsite_failing(run.completed_at + grace * 2)


def configured_at(monkeypatch, instant):
    """Say the applied configuration naming the folder was prepared then."""
    monkeypatch.setattr(backup_health, "destination_configured_since", lambda: instant)


def test_a_first_copy_that_records_nothing_still_alerts(offsite, monkeypatch):
    """With no outcome at all yet, a silent first copy is noticed too (#324 M1).

    The Drive folder was configured for the first time and every copy since
    died before recording anything, so no row exists.
    """
    grace = backup_health.OFFSITE_GRACE
    run = record_run(offsite.directory)
    # Copies are off: a backup never copied is not a failure.
    configured_at(monkeypatch, None)
    assert not backup_health.offsite_failing(run.completed_at + grace * 2)
    # A backup taken before the folder was configured is not expected on Drive.
    configured_at(monkeypatch, run.completed_at + timedelta(seconds=1))
    assert not backup_health.offsite_failing(run.completed_at + grace * 2)
    # Within the grace the configuration is not even read.
    monkeypatch.setattr(
        backup_health,
        "destination_configured_since",
        lambda: pytest.fail("read the configuration too early"),
    )
    assert not backup_health.offsite_failing(run.completed_at + grace / 2)
    # One taken after it must record an outcome within the grace.
    configured_at(monkeypatch, run.completed_at - timedelta(hours=1))
    assert not backup_health.offsite_failing(run.completed_at + grace / 2)
    assert backup_health.offsite_failing(run.completed_at + grace * 2)
    with task_login(ServiceRole.WORKER, exact=True):
        assert backup_health.offsite_failing(run.completed_at + grace * 2)
    assert copy(offsite) == {"state": "uploaded", "sets": 1}
    assert not backup_health.offsite_failing(run.completed_at + grace * 2)


def test_a_reenabled_copy_that_records_nothing_still_alerts(offsite, monkeypatch):
    """Turned off and back on, a silent copy is noticed despite "disabled"."""
    grace = backup_health.OFFSITE_GRACE
    assert copy(offsite)["state"] == "uploaded"
    offsite.target[0] = None
    assert copy(offsite) == {"state": "not_configured"}
    # A backup while copies were off is not expected on Drive.
    off = record_run(new_set(offsite, "20260928T020000Z"))
    configured_at(monkeypatch, None)
    assert not backup_health.offsite_failing(off.completed_at + grace * 2)
    # Turned back on; the next backup's copy dies before recording anything.
    configured_at(monkeypatch, off.completed_at + timedelta(microseconds=1))
    assert not backup_health.offsite_failing(off.completed_at + grace * 2)
    run = record_run(new_set(offsite, "20260929T020000Z"))
    assert BackupUpload.objects.order_by("-created_at").first().state == "disabled"
    assert not backup_health.offsite_failing(run.completed_at + grace / 2)
    assert backup_health.offsite_failing(run.completed_at + grace * 2)


def test_a_catch_up_killed_during_the_newest_set_still_alerts(offsite, monkeypatch):
    """An older set copied in the same run does not hide the newest one.

    Two sets are pending; the first uploads and records its row, then the
    process dies uploading the second. The newest row is a fresh "uploaded",
    but it names the older set, so the newest backup still counts as failing.
    """
    newest = new_set(offsite, "20260928T020000Z")
    run = record_run(newest)
    upload = offsite.drive.upload

    def killed(path, name, parent, **kwargs):
        """The process stops (e.g. the container is killed) mid-upload."""
        if path.parent == newest:
            raise SystemExit(137)
        return upload(path, name, parent, **kwargs)

    monkeypatch.setattr(offsite.drive, "upload", killed)
    with pytest.raises(SystemExit):
        copy(offsite)
    rows = BackupUpload.objects.values_list("state", "set_name")
    assert list(rows) == [("uploaded", offsite.directory.name)]
    grace = backup_health.OFFSITE_GRACE
    assert backup_health.offsite_failing(run.completed_at + grace * 2)


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


def test_the_installer_answers_access_checks(workspace, monkeypatch):
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


def database_now_sql():
    """The database's current time, read in its own transaction."""
    with transaction.atomic():
        return database_now()


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
    # The Integrations list links to the setup page even before it's set up.
    index = browser.get("/admin/configuration/integrations").content
    assert b"Off-site backups (Google Drive)" in index
    assert f'href="{URL}">Set up off-site backups</a>'.encode() in index
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
    # After Test access, the tested folder stays in the field for Save and is
    # named beside the result, whether the check is pending, passed or failed.
    tested = browser.get(URL).content
    assert f'value="{LINK}"'.encode() in tested
    assert b"Folder tested:" in tested
    BackupDriveProbe.objects.filter(pk=probe.pk).update(
        state="failed", failure_kind="permission", completed_at=database_now_sql()
    )
    failed = browser.get(URL).content
    assert f'value="{LINK}"'.encode() in failed and b"Folder tested:" in failed
    assert b"Test access:" in failed
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
    # The scheduler's alert sees the folder as configured from now on.
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        assert backup_health.destination_configured_since() is not None
    # Saving the tested folder clears the earlier Test access result.
    assert b"Test access:" not in browser.get(URL).content
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
    index = browser.get("/admin/configuration/integrations").content
    assert f'href="{URL}">Change</a>'.encode() in index
    assert b"copied to Google Drive" in browser.get(URL).content
    preview = hidden(post(browser, URL, {"action": "remove"}), "preview")
    apply(store, post(browser, URL, {"action": "confirm", "preview": preview}))
    assert backups(store) == []
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        assert backup_health.destination_configured_since() is None


def test_a_check_that_waited_too_long_closes_unanswered(workspace, monkeypatch, caplog):
    """The installer never runs a stale check; the page already said so."""
    from datetime import timedelta

    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
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
    # Closing it at its limit is logged with the limit and how long it waited.
    [closed] = [line for line in logged(caplog) if "timeout" in line]
    assert closed["timeout"] == "drive_probe_wait"
    assert closed["limit_seconds"] == 0 and closed["elapsed_seconds"] >= 0
    with transaction.atomic():
        assert latest_probe(actor, database_now()).kind == "unanswered"


def insert_probe(subject):
    """Insert one access check as the real web login; return any SQL error."""
    try:
        with task_login(ServiceRole.WEB, exact=True), transaction.atomic():
            BackupDriveProbe.objects.create(
                requested_by_id=uuid4(), folder_id=FOLDER, subject=subject
            )
    except DatabaseError as error:
        return error.__cause__.sqlstate
    return None


def test_a_check_may_only_name_the_applied_workspace_mailbox(workspace):
    """The web cannot make the installer impersonate another domain user."""
    assert insert_probe("mail@example.org") is None
    assert insert_probe("someone.else@example.org") == "23514"
    assert insert_probe("MAIL@example.org") == "23514"
    row = BackupDriveProbe.objects.get(subject="mail@example.org")
    with pytest.raises(DatabaseError), transaction.atomic():
        BackupDriveProbe.objects.filter(pk=row.pk).update(
            subject="someone.else@example.org"
        )


def test_a_check_needs_an_applied_workspace_integration():
    """With no Workspace mail applied there is no user a check may name."""
    assert insert_probe("mail@example.org") == "23514"


def test_the_probe_guard_is_a_non_callable_definer():
    """The guard reads applied settings as its owner and cannot be called."""
    for login in (
        lambda: task_login(ServiceRole.WEB, exact=True),
        lambda: target_login("google_workspace"),
    ):
        with login(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT prosecdef, proconfig, "
                "has_function_privilege(current_user, oid, 'EXECUTE') "
                "FROM pg_proc WHERE "
                "oid='public.stewardship_backup_probe_guard_v1()'::regprocedure"
            )
            definer, configuration, executable = cursor.fetchone()
            assert definer and not executable
            assert configuration == ["search_path=pg_catalog, public, pg_temp"]


def finished_probe(actor, state, failure=None, folder=FOLDER):
    """One access check the installer completed with ``state``."""
    probe = request_probe(actor, folder, "mail@example.org")
    BackupDriveProbe.objects.filter(pk=probe.pk).update(
        state=state, failure_kind=failure, completed_at=database_now_sql()
    )
    probe.refresh_from_db()
    return probe


def test_access_check_results_show_only_briefly(workspace, monkeypatch):
    """Pending shows until it resolves; results go after ten minutes or Save."""
    shown = backup_destination.PROBE_SHOWN_FOR
    tick = timedelta(microseconds=1)
    saved = [(None, False)]
    monkeypatch.setattr(
        backup_destination, "saved_folder_since", lambda since: saved[0]
    )

    def kind(actor, now):
        """The shown result's kind at ``now``, or None when hidden."""
        status = latest_probe(actor, now)
        return status and status.kind

    actor = uuid4()
    probe = request_probe(actor, FOLDER, "mail@example.org")
    probe.refresh_from_db()
    created = probe.created_at
    # Pending: shown until PROBE_WAIT, then as unanswered for ten minutes.
    assert kind(actor, created + PROBE_WAIT / 2) == "pending"
    assert kind(actor, created + PROBE_WAIT + shown) == "unanswered"
    assert kind(actor, created + PROBE_WAIT + shown + tick) is None
    # A closed "unanswered" check also ended at PROBE_WAIT, not at its close.
    actor = uuid4()
    probe = finished_probe(actor, "failed", "unanswered")
    assert kind(actor, probe.created_at + PROBE_WAIT + shown) == "unanswered"
    assert kind(actor, probe.created_at + PROBE_WAIT + shown + tick) is None
    for state, failure in (("succeeded", None), ("failed", "permission")):
        # Checks are append-only, so each case uses its own Administrator.
        actor = uuid4()
        done = finished_probe(actor, state, failure).completed_at
        saved[0] = (None, False)
        assert kind(actor, done + shown) == state
        assert kind(actor, done + shown + tick) is None
        # An unrelated configuration change keeps it...
        saved[0] = (FOLDER, False)
        assert kind(actor, done + shown / 2) == state
        # ...and so does saving a different folder after the test...
        saved[0] = ("0OtherFolder12345", True)
        assert kind(actor, done + shown / 2) == state
        # ...but saving the tested folder after the test hides it at once.
        saved[0] = (FOLDER, True)
        assert kind(actor, done + shown / 2) is None
    # Only the newest check counts: a hidden newer one never lets an older,
    # otherwise visible, result show instead.
    actor = uuid4()
    finished_probe(actor, "failed", "permission", folder="0OtherFolder12345")
    done = finished_probe(actor, "succeeded").completed_at
    saved[0] = (FOLDER, True)
    assert kind(actor, done + shown / 2) is None


def test_saving_a_folder_counts_only_backup_folder_changes(workspace):
    """Real versions: a save after the test counts; later ones don't."""
    store = workspace.store
    browser, _ = signed_in()
    other = "0OtherFolder12345"
    before = database_now_sql()
    preview = hidden(
        post(
            browser,
            URL,
            {
                "action": "preview",
                "base_digest": store.active().digest,
                "target": f"https://drive.google.com/drive/folders/{other}",
            },
        ),
        "preview",
    )
    apply(store, post(browser, URL, {"action": "confirm", "preview": preview}))
    after = database_now_sql()
    assert backup_destination.saved_folder_since(before) == (other, True)
    assert backup_destination.saved_folder_since(after) == (other, False)
