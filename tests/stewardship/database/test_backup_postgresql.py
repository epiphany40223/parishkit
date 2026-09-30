"""The backup record, the overdue alert and the upgrade admission on real SQL."""

import json
import logging
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import backup, backup_commands
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import backup_health
from parishkit.stewardship.jobs.backup_models import BackupRun
from parishkit.stewardship.jobs.operational_content import IncidentKind
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.ownership import database_now

from .test_background_grants_postgresql import task_login
from .test_operational_collection_postgresql import schedule

pytestmark = pytest.mark.django_db(transaction=True)

FACTS = {
    "database_bytes": 1024,
    "files_bytes": 512,
    "manifest_digest": "a" * 64,
    "recipient_fingerprint": "b" * 16,
    "application_version": "0.1.0",
}


def now():
    """The database clock, which the ownership helper reads in a transaction."""
    with transaction.atomic():
        return database_now()


def recorded(*, age=timedelta(0), **facts):
    """One completed run, optionally aged by moving both times back."""
    instant = now() - age
    with transaction.atomic():
        return BackupRun.objects.create(
            started_at=instant - timedelta(minutes=1),
            completed_at=instant,
            **{**FACTS, **facts},
        )


def episode():
    """The open overdue-backup episode, if any."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.BACKUP_RPO_BREACH, resolved_at__isnull=True
    ).first()


def observe():
    """Observe as the worker that runs the operational collection."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        backup_health.observe_backup_health()


@contextmanager
def backup_login():
    """The backup login as provisioned: its grants plus reading everything.

    Production makes it a member of pg_read_all_data (with inheritance) that
    bypasses row-level security, which ``task_login`` does not model; the
    backup command reads the active configuration through them.
    """
    name = "pk_stewardship_backup_worker"
    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(f"GRANT pg_read_all_data TO {name} WITH INHERIT TRUE")
            cursor.execute(f"ALTER ROLE {name} BYPASSRLS")
            cursor.execute(f"SET SESSION AUTHORIZATION {name}")
        yield


def test_the_backup_login_writes_one_row_and_nothing_edits_it():
    """Insert under the real backup identity; update and delete are refused."""
    instant = now()
    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        with transaction.atomic():
            row = BackupRun.objects.create(
                started_at=instant, completed_at=instant, **FACTS
            )
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupRun.objects.filter(pk=row.pk).update(database_bytes=1)
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupRun.objects.filter(pk=row.pk).delete()
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupRun.objects.create(
                started_at=instant,
                completed_at=instant - timedelta(seconds=1),
                **FACTS,
            )
        with pytest.raises(DatabaseError), transaction.atomic():
            BackupRun.objects.create(
                started_at=instant,
                completed_at=instant,
                **{**FACTS, "manifest_digest": "short"},
            )
    assert BackupRun.objects.count() == 1


def test_overdue_is_judged_from_the_newest_run_and_the_mode(monkeypatch):
    """No run yet alerts only in Production; a stale run alerts in every mode."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.stewardship_backup_run')")
        assert cursor.fetchone()[0] is not None
    # A fresh install has no runtime row at all: not Production.
    assert SystemConfiguration.objects.count() == 0
    assert backup_health.production_mode() is False
    observe()
    assert episode() is None
    monkeypatch.setattr(backup_health, "production_mode", lambda: True)
    # An idle deployment must wake its own collector for the first breach.
    with work_transaction():
        assert backup_health.needs_backup_observation() is True
    assert schedule() != ()
    observe()
    assert episode() is not None
    recorded()
    observe()
    assert episode() is None
    recorded(age=timedelta(hours=25))
    # The newest run is still the fresh one: no alert.
    observe()
    assert episode() is None


def test_a_stale_backup_opens_and_a_fresh_one_resolves_in_testing_too():
    """Once a backup exists, the day-long window applies regardless of mode."""
    assert backup_health.production_mode() is False
    recorded(age=timedelta(hours=24, minutes=1))
    observe()
    opened = episode()
    assert opened is not None and opened.signal_level == "CRITICAL"
    observe()
    assert episode().pk == opened.pk
    recorded()
    observe()
    assert episode() is None
    with work_transaction():
        assert not backup_health.needs_backup_observation()


def test_upgrade_admission_reads_the_same_evidence():
    """The offline commands accept a run inside the window and nothing older."""
    with connection.cursor() as cursor:
        assert backup.recent_backup_recorded(cursor) is False
        recorded(age=timedelta(hours=24, seconds=5))
        assert backup.recent_backup_recorded(cursor) is False
        recorded(age=timedelta(hours=23))
        assert backup.recent_backup_recorded(cursor) is True


def test_a_changed_recipient_key_is_logged_as_a_warning(caplog):
    """The first run and a same-key run are quiet; a different key warns."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")

    def warnings():
        """The recipient-change warnings logged so far."""
        return [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and record.extra.get("failure_kind") == "backup_recipient_changed"
        ]

    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        assert backup_commands.recipient_changed("c" * 16) is False
    recorded()
    with task_login(ServiceRole.BACKUP_WORKER, exact=True):
        assert (
            backup_commands.recipient_changed(FACTS["recipient_fingerprint"]) is False
        )
        assert warnings() == []
        assert backup_commands.recipient_changed("c" * 16) is True
    assert len(warnings()) == 1


def key_episode():
    """The open key-change episode, if any."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.BACKUP_KEY_CHANGED, resolved_at__isnull=True
    ).first()


def key_logs():
    """The System log entries explaining a key change."""
    from parishkit.stewardship.audit.models import OperationalLog

    return OperationalLog.objects.filter(
        event="configuration_digest_mismatch", context__outcome="changed"
    )


def test_a_changed_backup_key_opens_a_critical_incident_for_the_window():
    """A run sealed to a new key alerts, explains itself once, and ages out."""
    from parishkit.stewardship.audit.log_descriptions import describe

    recorded(age=timedelta(days=3))
    observe()
    assert key_episode() is None
    recorded(age=timedelta(hours=1), recipient_fingerprint="c" * 16)
    with work_transaction():
        assert backup_health.needs_backup_observation()
    observe()
    opened = key_episode()
    assert opened is not None and opened.signal_level == "CRITICAL"
    # The overdue alert is unaffected: the new run is recent.
    assert episode() is None
    # One plain-language System log entry for the episode, not per observation.
    observe()
    assert key_episode().pk == opened.pk
    [entry] = key_logs()
    assert entry.level == "WARNING"
    assert "different encryption key" in str(describe(entry.event, entry.context))
    assert "did not match what the database expects" in str(
        describe(entry.event, {"outcome": "failed"})
    )
    # A later backup with the new key does not hide the change inside the
    # window; the window ending resolves it.
    recorded(recipient_fingerprint="c" * 16)
    observe()
    assert key_episode().pk == opened.pk
    later = now() + backup_health.KEY_CHANGE_WINDOW + timedelta(hours=2)
    with transaction.atomic():
        assert not backup_health.key_changed(later)


def test_a_key_change_is_seen_after_several_new_key_backups():
    """Backups that ran with the new key before the check do not hide it.

    For example the scheduler was stopped for a deploy while two backups ran
    (#359 review L1).
    """
    recorded(age=timedelta(days=5))
    for hours in (5, 3, 1):
        recorded(age=timedelta(hours=hours), recipient_fingerprint="c" * 16)
    observe()
    assert key_episode() is not None


def test_backups_that_completed_together_still_compare_in_a_fixed_order():
    """An exact completion-time tie between keys is ordered by row ID."""
    instant = now() - timedelta(hours=1)
    with transaction.atomic():
        for fingerprint in ("b" * 16, "c" * 16):
            BackupRun.objects.create(
                started_at=instant - timedelta(minutes=1),
                completed_at=instant,
                **{**FACTS, "recipient_fingerprint": fingerprint},
            )
    with transaction.atomic():
        assert backup_health.key_changed() is True
    observe()
    assert key_episode() is not None


def test_the_backup_command_says_when_the_key_changed(monkeypatch, capsys):
    """The command's JSON line carries recipient_changed from the real record."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from parishkit.stewardship import (
        backup_boundaries,
        operator_commands,
        runtime_database,
        runtime_paths,
        startup_interlock,
    )
    from parishkit.stewardship.cli import main

    fingerprint = ["b" * 16]

    def run_backup(configuration, *, record, recipient):
        """Record one run with the current key, as the real backup does."""
        assert recipient is None  # no key configured in the portal
        with transaction.atomic():
            started = database_now()
        record(
            started_at=started,
            **{**FACTS, "recipient_fingerprint": fingerprint[0]},
        )
        return {
            "database": {"plaintext_bytes": 1024},
            "files": {"plaintext_bytes": 512},
            "recipient_fingerprint": fingerprint[0],
        }

    # Everything around the record is the host's; the record is real SQL.
    monkeypatch.setattr(backup_commands, "configure_logging", lambda: None)
    monkeypatch.setattr(backup_commands, "load_deployment", lambda path: None)
    monkeypatch.setattr(backup_commands, "_admit_backup_identity", lambda: None)
    monkeypatch.setattr(backup_commands, "_copy_offsite", lambda c: {"state": "x"})
    monkeypatch.setattr(backup_boundaries, "admit_backup_service", lambda c: None)
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda c: None
    )
    monkeypatch.setattr(runtime_database, "require_current_schema", lambda: None)
    monkeypatch.setattr(
        runtime_paths, "RuntimeLayout", lambda c: SimpleNamespace(interlock=None)
    )
    monkeypatch.setattr(
        startup_interlock, "StartupLease", lambda path, offline: nullcontext()
    )
    monkeypatch.setattr(backup, "run_backup", run_backup)

    def changed():
        """Run the command once and return its recipient_changed field."""
        with backup_login():
            assert main(["backup", "--config", "unused"]) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["recipient_source"] == "file"
        return output["recipient_changed"]

    assert changed() is False
    assert changed() is False
    fingerprint[0] = "c" * 16
    assert changed() is True
    assert BackupRun.objects.count() == 3


def test_worker_and_scheduler_read_the_record_and_cannot_write_it():
    """The collection's identities see the evidence; only the backup writes it."""
    recorded()
    instant = now()
    for role in (ServiceRole.WORKER, ServiceRole.SCHEDULER):
        with task_login(role, exact=True):
            assert BackupRun.objects.count() == 1
            with pytest.raises(DatabaseError), transaction.atomic():
                BackupRun.objects.create(
                    id=uuid4(), started_at=instant, completed_at=instant, **FACTS
                )
