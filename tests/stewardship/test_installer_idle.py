"""Idle credential installers stay cheap without weakening their admission (#639)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db.backends.signals import connection_created

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_process
from parishkit.stewardship.accounts import (
    credential_database,
    setup_credential_installation,
    setup_mail_exchange,
    setup_notifications,
)
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.credential_installation import (
    CredentialInstaller,
)
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.source import setup_exchange


def private(target):
    """A synthetic target handoff key; nothing here decrypts with it."""
    return PrivateHandoff(target, Key("handoff", "active", b"h" * 32))


class Admission:
    """Stand in for the database: a clock, the login's role and the grant check."""

    def __init__(self, monkeypatch):
        self.now, self.role = 1000.0, 41
        self.database = SimpleNamespace()
        self.grants = Mock()
        monkeypatch.setattr(credential_database, "monotonic", lambda: self.now)
        monkeypatch.setattr(credential_database, "_identity", lambda _: self.role)
        monkeypatch.setattr(
            credential_database, "connections", {"default": self.database}
        )
        monkeypatch.setattr(credential_database, "_admit_installer_grants", self.grants)

    def admit(self, target="slack"):
        """One queue operation's admission."""
        credential_database.admit_installer_database(target)
        return self.grants.call_count


def test_grant_check_runs_once_per_connection_and_interval(monkeypatch):
    """The catalog-wide check repeats only after its interval, not per call."""
    admission = Admission(monkeypatch)
    assert admission.admit() == 1
    admission.now += credential_database.GRANT_RECHECK_SECONDS - 1
    assert admission.admit() == admission.admit() == 1
    admission.now += 1
    assert admission.admit() == 2
    admission.grants.assert_called_with("slack")


def test_grant_check_runs_again_on_a_new_connection(monkeypatch):
    """Every reconnect clears the record, so its first admission is complete."""
    admission = Admission(monkeypatch)
    assert admission.admit() == 1
    connection_created.send(sender=None, connection=admission.database)
    assert admission.admit() == 2
    assert admission.admit() == 2


@pytest.mark.parametrize("change", ["role", "target"])
def test_grant_check_runs_again_for_another_role_or_target(monkeypatch, change):
    """A recreated role, or another target, never inherits an earlier pass."""
    admission = Admission(monkeypatch)
    assert admission.admit() == 1
    if change == "role":
        admission.role = 42
        assert admission.admit() == 2
    else:
        assert admission.admit("parishsoft") == 2


def test_refused_grants_are_never_remembered(monkeypatch):
    """After a refusal every call checks again, and keeps refusing."""
    admission = Admission(monkeypatch)
    admission.grants.side_effect = ConfigError("Installer database grants excessive.")
    for _ in range(2):
        with pytest.raises(ConfigError):
            admission.admit()
    assert admission.grants.call_count == 2
    assert admission.database.stewardship_installer_admission is None


def test_identity_is_still_checked_on_every_call(monkeypatch):
    """A cached grant pass never skips the per-call login check."""
    admission = Admission(monkeypatch)
    admission.admit()
    monkeypatch.setattr(
        credential_database,
        "_identity",
        Mock(side_effect=ConfigError("identity is not isolated")),
    )
    with pytest.raises(ConfigError, match="identity"):
        admission.admit()


def refuse(*args, **kwargs):
    """A lock or locked step an idle installer must not reach."""
    pytest.fail("An idle installer took a lock.")


@pytest.mark.parametrize(
    "module,target",
    [(setup_exchange, "parishsoft"), (setup_mail_exchange, "google_workspace")],
)
def test_idle_relay_skips_the_work_order_lock(monkeypatch, module, target):
    """No pending exchange: return before the global work-order transaction."""
    admit = Mock()
    monkeypatch.setattr(module, "admit_installer_database", admit)
    monkeypatch.setattr(module, "has_pending", lambda: False)
    monkeypatch.setattr(module, "work_transaction", refuse)
    assert module.relay_pending(private(target)) is False
    admit.assert_called_once_with(target)


def test_idle_slack_installer_skips_recovery_and_claim_locks(monkeypatch):
    """No queued or submitting test: neither locked transaction runs."""
    monkeypatch.setattr(setup_notifications, "admit_installer_database", Mock())
    monkeypatch.setattr(setup_notifications, "has_pending", lambda: False)
    monkeypatch.setattr(setup_notifications, "recover_pending", refuse)
    monkeypatch.setattr(setup_notifications, "_begin", refuse)
    check = Mock()
    assert setup_notifications.run_pending(private("slack"), check=check) is False
    check.assert_called_once_with()


def test_idle_initial_staging_skips_file_and_work_order_locks(monkeypatch, tmp_path):
    """No frozen setup: neither the target file lock nor the global lock."""
    files = CredentialFiles(tmp_path.resolve() / "credential", private("slack"))
    monkeypatch.setattr(
        setup_credential_installation, "admit_installer_database", Mock()
    )
    monkeypatch.setattr(setup_credential_installation, "_frozen", lambda: False)
    monkeypatch.setattr(setup_credential_installation, "work_transaction", refuse)
    monkeypatch.setattr(files, "lock", refuse)
    assert setup_credential_installation.stage_initial_credential(files) is None


def test_queue_hint_counts_an_interrupted_journal_without_a_query(
    monkeypatch, tmp_path
):
    """A replacement journal left by a crash is work, before any SQL read."""
    from parishkit.stewardship.accounts import credential_installation

    files = CredentialFiles(tmp_path.resolve() / "credential", private("slack"))
    installer = CredentialInstaller(files, validate=lambda value: True)
    admit = Mock()
    monkeypatch.setattr(credential_installation, "admit_installer_database", admit)
    files.journal_path.write_bytes(b"{}")
    assert installer.pending() is True
    admit.assert_called_once_with("slack")


@pytest.mark.parametrize(
    "target,expected",
    [
        ("metrics", ["queue"]),
        ("google_oauth", ["queue"]),
        ("parishsoft", ["queue", "source", "initial"]),
        ("google_workspace", ["queue", "mail", "probe", "initial"]),
        ("slack", ["queue", "slack", "initial"]),
    ],
)
def test_each_target_checks_exactly_its_own_work(monkeypatch, target, expected):
    """The cheap check asks the queue plus only that target's setup steps."""
    from parishkit.stewardship import backup_probes

    asked = []

    def hint(name):
        """Record that this step was asked (and for which target); none has work."""
        return lambda *args: asked.append((name, *args)) or False

    installer = SimpleNamespace(pending=hint("queue"))
    monkeypatch.setattr(setup_exchange, "has_pending", hint("source"))
    monkeypatch.setattr(setup_mail_exchange, "has_pending", hint("mail"))
    monkeypatch.setattr(backup_probes, "has_pending", hint("probe"))
    monkeypatch.setattr(setup_notifications, "has_pending", hint("slack"))
    monkeypatch.setattr(setup_credential_installation, "has_pending", hint("initial"))
    assert runtime_process.installer_pending(installer, target)() is False
    assert asked == [
        (name, target) if name == "initial" else (name,) for name in expected
    ]


def test_cheap_check_stops_at_the_first_step_with_work(monkeypatch):
    """Work found by the queue check needs no further reads."""
    monkeypatch.setattr(setup_exchange, "has_pending", refuse)
    monkeypatch.setattr(setup_credential_installation, "has_pending", refuse)
    installer = SimpleNamespace(pending=lambda: True)
    assert runtime_process.installer_pending(installer, "parishsoft")() is True


class Wrapper:
    """A kept database connection that does or does not still answer."""

    def __init__(self, usable, *, opened=True):
        self.connection = object() if opened else None
        self.usable, self.closed = usable, False

    def is_usable(self):
        """The wrapper's own one-statement liveness check."""
        return self.usable

    def close(self):
        """Record that the connection was dropped."""
        self.closed = True


def test_a_connection_the_server_ended_is_dropped_quietly():
    """After a PostgreSQL restart the wake reconnects instead of failing."""
    dead, alive, unopened = Wrapper(False), Wrapper(True), Wrapper(False, opened=False)
    connections = SimpleNamespace(all=lambda initialized_only: [dead, alive, unopened])
    runtime_process.drop_unusable(connections)
    assert (dead.closed, alive.closed, unopened.closed) == (True, False, False)
