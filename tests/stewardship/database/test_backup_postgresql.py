"""The backup record, the overdue alert and the upgrade admission on real SQL."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import backup
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


def recorded(*, age=timedelta(0)):
    """One completed run, optionally aged by moving both times back."""
    instant = now() - age
    with transaction.atomic():
        return BackupRun.objects.create(
            started_at=instant - timedelta(minutes=1), completed_at=instant, **FACTS
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
