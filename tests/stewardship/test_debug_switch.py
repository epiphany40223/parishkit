"""The debug-off switch inside one process, without a database (#530).

``debug_switch.apply`` turns a process's debug logging off for a set switch
and back on after a clear, once each; ``refresh`` never raises and does
nothing in a process started without debug logging. The PostgreSQL side
(grants, guard, the connection hook) is database/test_debug_off_switch_postgresql.py.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from parishkit.stewardship import cli, debug_switch, observability

VARIABLE = observability.DEBUG_LOGGING_VARIABLE


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    """Each test starts as a process with no switch applied."""
    monkeypatch.setattr(debug_switch, "_held", None)
    monkeypatch.setattr(debug_switch, "_raised", [])
    monkeypatch.setattr(debug_switch, "_present", [])
    monkeypatch.delenv(VARIABLE, raising=False)


@pytest.fixture
def handlers():
    """Root handlers at DEBUG and WARNING, as configured before the switch."""
    debug, warning = logging.NullHandler(), logging.NullHandler()
    debug.setLevel(logging.DEBUG)
    warning.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(debug)
    root.addHandler(warning)
    yield debug, warning
    root.removeHandler(debug)
    root.removeHandler(warning)


def events(caplog, name):
    """How many records named ``name`` were logged, at WARNING."""
    return [record.levelno for record in caplog.records if record.msg == name].count(
        logging.WARNING
    )


def test_a_set_switch_turns_debug_logging_off_once_and_a_clear_back_on(
    monkeypatch, caplog, handlers
):
    """Environment, thresholds and one WARNING each way; repeats change nothing."""
    debug, warning = handlers
    monkeypatch.setenv(VARIABLE, "1")
    assert debug_switch.watching() and observability.debug_logging_enabled()
    debug_switch.apply(True)
    debug_switch.apply(True)
    assert VARIABLE not in os.environ
    assert not observability.debug_logging_enabled()
    assert (debug.level, warning.level) == (logging.INFO, logging.WARNING)
    assert events(caplog, "debug_logging_switched_off") == 1
    # Still watching: the switch may be cleared.
    assert debug_switch.watching()
    debug_switch.apply(False)
    debug_switch.apply(False)
    assert observability.debug_logging_enabled()
    assert (debug.level, warning.level) == (logging.DEBUG, logging.WARNING)
    assert events(caplog, "debug_logging_enabled") == 1


def test_a_clear_restores_handlers_logging_rebuilt_meanwhile(monkeypatch, handlers):
    """Handlers a reconfigure built at INFO while held go to DEBUG; others stay."""
    debug, warning = handlers
    info = logging.NullHandler()
    info.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(info)
    monkeypatch.setenv(VARIABLE, "1")
    try:
        debug_switch.apply(True)
        # configure_logging again: the old DEBUG handler goes, a new one is
        # built at INFO because the setting is out of the environment.
        root.removeHandler(debug)
        rebuilt = logging.NullHandler()
        rebuilt.setLevel(logging.INFO)
        root.addHandler(rebuilt)
        debug_switch.apply(False)
        assert rebuilt.level == logging.DEBUG
        assert (info.level, warning.level) == (logging.INFO, logging.WARNING)
    finally:
        root.removeHandler(info)
        root.removeHandler(rebuilt)


def test_one_refresh_at_a_time_and_the_others_skip(monkeypatch):
    """A refresh while another reads skips, so a stale read is never applied late."""
    from threading import Event, Thread

    monkeypatch.setenv(VARIABLE, "1")
    reading, release, reads = Event(), Event(), []

    def slow(database):
        """The first read waits; a nested or concurrent one is recorded."""
        reads.append(database)
        if database == "first":
            reading.set()
            release.wait(5)
        return True

    monkeypatch.setattr(debug_switch, "read_switch", slow)
    first = Thread(target=debug_switch.refresh, args=("first",))
    first.start()
    assert reading.wait(5)
    debug_switch.refresh("second")
    release.set()
    first.join(5)
    assert reads == ["first"]
    assert not observability.debug_logging_enabled()
    # The lock is free again afterwards.
    monkeypatch.setattr(debug_switch, "read_switch", lambda database: False)
    debug_switch.refresh("third")
    assert observability.debug_logging_enabled()


def test_django_setup_alone_registers_the_connection_hook():
    """The jobs app's ready() connects it; no other import is needed."""
    script = (
        "import django; django.setup(); "
        "from django.db.backends.signals import connection_created as s; "
        "print(any(key[0] == 'stewardship-debug-off-switch' "
        "for key, *_ in s.receivers))"
    )
    environment = os.environ | {
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "DJANGO_SETTINGS_MODULE": "parishkit.stewardship.settings.test",
    }
    output = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    assert output.stdout.strip() == "True"


def test_a_process_started_without_debug_logging_is_never_changed(caplog, handlers):
    """The switch can only turn debug logging off, never on."""
    debug, _ = handlers
    assert not debug_switch.watching()
    debug_switch.apply(True)
    debug_switch.apply(False)
    assert not observability.debug_logging_enabled()
    assert debug.level == logging.DEBUG
    assert not caplog.records


def test_any_other_value_of_the_setting_is_not_debug_logging(monkeypatch):
    """Only exactly "1" is on, so only that is held off."""
    monkeypatch.setenv(VARIABLE, "0")
    assert not debug_switch.watching()
    debug_switch.apply(True)
    assert os.environ[VARIABLE] == "0"


def test_refresh_skips_without_debug_logging_and_never_raises(monkeypatch):
    """No read at all without the setting; a failing or unknown read changes nothing."""
    calls = []

    def read(database):
        """Record the read, then fail as a refused query would."""
        calls.append(database)
        raise RuntimeError("refused")

    monkeypatch.setattr(debug_switch, "read_switch", read)
    debug_switch.refresh(object())
    assert calls == []
    monkeypatch.setenv(VARIABLE, "1")
    debug_switch.refresh(object())
    assert len(calls) == 1 and observability.debug_logging_enabled()
    monkeypatch.setattr(debug_switch, "read_switch", lambda database: None)
    debug_switch.refresh(object())
    assert observability.debug_logging_enabled()
    monkeypatch.setattr(debug_switch, "read_switch", lambda database: True)
    debug_switch.refresh(object())
    assert not observability.debug_logging_enabled()


def test_a_helper_started_while_the_switch_is_set_inherits_no_setting(monkeypatch):
    """The setting leaves the process environment, so children never see it."""
    monkeypatch.setenv(VARIABLE, "1")
    debug_switch.apply(True)
    script = f"import os; print(os.environ.get({VARIABLE!r}, 'absent'))"
    output = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True
    )
    assert output.stdout.strip() == "absent"


def test_only_postgresql_connections_read_the_switch(monkeypatch):
    """The connection hook ignores other database engines."""
    seen = []
    monkeypatch.setattr(debug_switch, "refresh", seen.append)
    sqlite = type("Connection", (), {"vendor": "sqlite"})()
    postgresql = type("Connection", (), {"vendor": "postgresql"})()
    debug_switch._connected(None, sqlite)
    debug_switch._connected(None, postgresql)
    assert seen == [postgresql]


def test_debug_off_clear_takes_only_its_configuration(monkeypatch):
    """--config and nothing else; it runs as an offline operator command."""
    calls = []
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.execute_operator",
        lambda args: calls.append(args) or 0,
    )
    assert cli.main(["debug-off-clear", "--config", "x"]) == 0
    assert calls[0].command == "debug-off-clear"
    with pytest.raises(SystemExit):
        cli.main(["debug-off-clear", "--config", "x", "--reason", "restore"])


def test_debug_off_clear_refuses_any_profile_but_admin_recovery(monkeypatch):
    """The clear's definer is admin recovery's; no other profile tries."""
    from types import SimpleNamespace

    from parishkit.config import ConfigError
    from parishkit.stewardship import operator_commands
    from parishkit.stewardship.deployment import ServiceRole

    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda _: ServiceRole.BOOTSTRAP
    )
    with pytest.raises(ConfigError, match="admin-recovery profile"):
        operator_commands.debug_off_clear_command(SimpleNamespace())
