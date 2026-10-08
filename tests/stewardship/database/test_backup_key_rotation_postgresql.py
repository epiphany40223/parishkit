"""Backups follow the key an Administrator configured, without an alarm (#198)."""

import base64
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship import backup, backup_sealing
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import backup_health

from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_backup_postgresql import (
    backup_login,
    key_episode,
    key_logs,
    observe,
    recorded,
)

pytestmark = pytest.mark.django_db(transaction=True)


def keypair():
    """A new key pair: (private key, canonical public key text)."""
    private, public = backup_sealing.generate_keypair()
    return backup_sealing.PrivateKey(base64.b64decode(private)), public.strip()


@pytest.fixture
def rotated(auth_service, google):
    """An applied configuration naming a new backup key; returns its fingerprint."""
    _, public = keypair()
    version = auth_service.store.active()
    change(
        auth_service.store,
        version,
        uuid4(),
        [
            {
                "operation": "add",
                "section": "integrations",
                "id": str(uuid4()),
                "values": {
                    "kind": "backup_key",
                    "settings": {"public_key": public},
                    "credential_fingerprint": None,
                },
            }
        ],
    )
    return backup_sealing.parse_public_key(public).fingerprint


def test_a_portal_rotation_opens_a_warning_that_escalates_like_any_other(rotated):
    """A backup that switched to the portal's key still opens the incident.

    It was announced to every Administrator as a security event, so the
    episode opens as a WARNING, which sends no notice by itself. Like every
    WARNING episode it escalates to CRITICAL, and is then routed (Slack
    included), once it has lasted the policy's escalation window (15
    minutes by default) and is observed again; the episode stays open for
    the whole key-change window, so that always happens. The scheduler
    login can judge it too.
    """
    from parishkit.stewardship.jobs.operational_models import OperationalNotice

    from .auth_builders import unguarded

    recorded(age=timedelta(days=3))
    recorded(recipient_fingerprint=rotated)
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        assert backup_health.key_change() == "portal"
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        assert backup_health.needs_backup_observation()
    observe()
    opened = key_episode()
    assert opened is not None and opened.signal_level == "WARNING"
    assert not OperationalNotice.objects.filter(incident=opened).exists()
    assert key_logs().count() == 1
    observe()
    assert key_episode().signal_level == "WARNING"
    # Past the escalation window, the next observation escalates and notifies.
    with unguarded():
        type(opened).objects.filter(pk=opened.pk).update(
            first_seen=opened.first_seen - timedelta(minutes=20)
        )
    observe()
    assert key_episode().level == "CRITICAL"
    notice = OperationalNotice.objects.get(incident=opened)
    assert (notice.phase, notice.level) == ("escalated", "CRITICAL")


def test_a_hand_made_change_after_a_rotation_is_critical_at_once(rotated):
    """A later change away from the portal's key is CRITICAL immediately."""
    recorded(age=timedelta(days=3))
    recorded(recipient_fingerprint=rotated)
    observe()
    recorded(recipient_fingerprint="e" * 16)
    observe()
    assert key_episode().signal_level == "CRITICAL"


def test_a_change_before_the_key_was_applied_still_alerts(rotated):
    """Only a key applied before that backup completed counts as deliberate."""
    recorded(age=timedelta(days=3))
    recorded(age=timedelta(hours=1), recipient_fingerprint=rotated)
    observe()
    assert key_episode().signal_level == "CRITICAL"


def test_the_backup_profile_reads_the_configured_key(rotated):
    """The backup login finds the applied key; without one it uses the file."""
    with backup_login(), transaction.atomic():
        assert backup.configured_recipient().fingerprint == rotated


def test_without_a_configured_key_the_file_is_used():
    """No runtime row, or no backup_key record, means the installed file."""
    with backup_login(), transaction.atomic():
        assert backup.configured_recipient() is None


def test_the_backup_command_seals_to_the_configured_key(rotated, monkeypatch, capsys):
    """End to end through the command: the configured key, and it says so."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from parishkit.stewardship import (
        backup_boundaries,
        backup_commands,
        operator_commands,
        runtime_database,
        runtime_paths,
        startup_interlock,
    )
    from parishkit.stewardship.cli import main
    from parishkit.stewardship.jobs.ownership import database_now

    used = []

    def run_backup(configuration, *, record, migrations, recipient):
        """Record one run sealed to the key the command handed over."""
        used.append(recipient)
        with transaction.atomic():
            started = database_now()
        record(
            started_at=started,
            database_bytes=1,
            files_bytes=1,
            manifest_digest="a" * 64,
            recipient_fingerprint=recipient.fingerprint,
            application_version="0.1.0",
        )
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": recipient.fingerprint,
        }

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
    with backup_login():
        assert main(["backup", "--config", "unused"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["recipient_source"] == "configured"
    assert output["recipient_fingerprint"] == rotated == used[0].fingerprint


def test_the_activating_login_may_read_only_the_newest_backup_key():
    """The configuration installer's alert names the key before; nothing more."""
    from django.db import DatabaseError, connection

    recorded(recipient_fingerprint="f" * 16)
    with task_login(ServiceRole.CONFIG_INSTALLER, exact=True):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT recipient_fingerprint FROM public.stewardship_backup_run "
                "ORDER BY completed_at DESC, id DESC LIMIT 1"
            )
            assert cursor.fetchone() == ("f" * 16,)
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT manifest_digest FROM public.stewardship_backup_run")
