"""The installer's run-time re-check of a queued "Test access" check (#269).

The probe table, its transactions and the database clock are faked: only
which outcome a check records, and whether Drive was contacted, matter here.
The PostgreSQL tests in ``database/test_backup_offsite_postgresql.py`` run
the same path against the real definer and grants.
"""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parishkit.stewardship import backup_probes
from parishkit.stewardship.accounts import key_files
from parishkit.stewardship.backup_drive import DriveFailure
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.jobs import backup_models, ownership

NOW = datetime(2026, 10, 24, tzinfo=UTC)
SUBJECT = "mail@example.org"


@pytest.fixture
def queued(monkeypatch):
    """One pending check for ``SUBJECT``, queued now; returns what it records."""
    row = SimpleNamespace(
        pk=1, created_at=NOW, subject=SUBJECT, folder_id="1AbCdEfGhIjKlMnOpQrStUv"
    )
    recorded = []

    class Rows:
        """The two probe-table queries the pass makes."""

        def order_by(self, *fields):
            return self

        def first(self):
            return row

        def update(self, **fields):
            recorded.append(fields)

    monkeypatch.setattr(
        backup_models,
        "BackupDriveProbe",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **k: Rows())),
    )
    monkeypatch.setattr(
        backup_probes, "transaction", SimpleNamespace(atomic=nullcontext)
    )
    monkeypatch.setattr(ownership, "database_now", lambda: NOW)
    return SimpleNamespace(row=row, recorded=recorded)


def run(monkeypatch, applied, *, key=lambda path: b"key"):
    """Run one installer pass with ``applied`` as the applied mailbox user.

    ``key`` stands in for reading the Workspace key file. Returns the
    subjects Drive sessions were built for: any entry means the check
    impersonated that user and went on to contact Drive.
    """
    sessions = []
    monkeypatch.setattr(backup_probes, "applied_mailbox", lambda: applied)
    monkeypatch.setattr(key_files, "read_private", key)
    monkeypatch.setattr(backup_probes, "DriveClient", lambda session, **k: session)
    monkeypatch.setattr(backup_probes, "probe", lambda client, folder: None)
    assert (
        backup_probes.run_pending_probes(
            "unused",
            profile=DeploymentProfile.PRODUCTION,
            session_factory=lambda value, subject: sessions.append(subject),
        )
        == 1
    )
    return sessions


@pytest.mark.parametrize(
    "applied", ["new.mailbox@example.org", "MAIL@example.org", None]
)
def test_a_check_for_another_user_closes_without_contacting_drive(
    queued, monkeypatch, applied
):
    """The applied user changed (or was removed) after the check was queued."""

    def unread(path):
        pytest.fail("read the Workspace key for a changed user")

    assert run(monkeypatch, applied, key=unread) == []
    assert queued.recorded == [{"state": "failed", "failure_kind": "settings_changed"}]


def test_a_check_for_the_applied_user_runs_as_that_user(queued, monkeypatch):
    """An unchanged user is the one the check impersonates."""
    assert run(monkeypatch, SUBJECT) == [SUBJECT]
    assert queued.recorded == [{"state": "succeeded", "failure_kind": None}]


def test_a_stale_check_closes_unanswered_before_any_comparison(queued, monkeypatch):
    """Past PROBE_WAIT the page already said unanswered; nothing is read."""
    queued.row.created_at = NOW - backup_probes.PROBE_WAIT - timedelta(seconds=1)

    def unread():
        pytest.fail("read the applied settings for a stale check")

    sessions = []
    monkeypatch.setattr(backup_probes, "applied_mailbox", unread)
    assert (
        backup_probes.run_pending_probes(
            "unused",
            profile=DeploymentProfile.PRODUCTION,
            session_factory=lambda value, subject: sessions.append(subject),
        )
        == 1
    )
    assert sessions == []
    assert queued.recorded == [{"state": "failed", "failure_kind": "unanswered"}]


def test_every_probe_failure_kind_has_plain_words():
    """Each kind a check can record is explained; settings_changed says what next."""
    for kind in backup_models.PROBE_FAILURE_KINDS:
        assert kind in DriveFailure.MESSAGES, kind
    assert set(backup_models.PROBE_FAILURE_KINDS) - set(
        backup_models.FAILURE_KINDS
    ) == {"settings_changed"}
    message = DriveFailure("settings_changed").message
    assert "Select Test access again" in message
