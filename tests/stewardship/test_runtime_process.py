"""Operational dispatch cannot turn a role flag into authority or leak failures."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_process
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.startup_interlock import StartupLease

from .bootstrap_factory import bootstrap_fixture
from .test_runtime_topology import configuration_at


def test_web_process_settings_keep_finite_reserved_headroom(tmp_path):
    """The supervisor uses the same process budget as downloads and Compose."""
    configuration = configuration_at(tmp_path)
    options = runtime_process.gunicorn_options(configuration)
    assert options["workers"] == 2
    assert options["threads"] == 8
    assert options["accesslog"] is None
    assert options["forwarded_allow_ips"] == ""
    assert options["graceful_timeout"] > configuration.runtime_budget.download_seconds
    assert options["timeout"] < configuration.runtime_budget.proxy_timeout_seconds


def test_runtime_dispatch_holds_real_online_lease_until_runner_exits(
    tmp_path, monkeypatch
):
    """Offline migration cannot start even before the runtime opens its web port."""
    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=ServiceRole.WEB)
    monkeypatch.setattr(runtime_process, "load_deployment", lambda path: configuration)
    monkeypatch.setattr(runtime_process, "configure_logging", lambda: None)
    called = []

    def runner(config, lease):
        """The lifecycle inode is real; only the process body is substituted."""
        called.append(config)
        lease.check()
        with pytest.raises(ConfigError), StartupLease(lease.path, offline=True):
            pass
        return 0

    monkeypatch.setattr(runtime_process, "serve_web", runner)
    assert main(["runtime", "--config", "operator-input.yaml"]) == 0
    assert called == [configuration]
    from parishkit.stewardship.runtime_paths import RuntimeLayout

    with StartupLease(RuntimeLayout(configuration).interlock, offline=True):
        pass


@pytest.mark.parametrize("configured", [False, True])
def test_runtime_errors_never_print_private_input(configured, monkeypatch, capsys):
    """No traceback or exception text reaches shell diagnostics on startup failure."""
    monkeypatch.setattr(runtime_process, "configure_logging", lambda: None)

    def failure(path):
        raise ValueError("private-input-token")

    monkeypatch.setattr(runtime_process, "load_deployment", failure)
    arguments = ["runtime"]
    if configured:
        arguments += ["--config", "private-input-path"]
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert "private-input" not in output.out + output.err
    assert "runtime unavailable" in output.err


def test_bounded_installer_retries_and_closes_database_without_spinning(monkeypatch):
    """Transient failures back off finitely; success resets delay and shutdown wins."""
    waits, calls, closes = [], [], []
    stop = Event()

    def wait(delay):
        """Drive deterministic retries without delaying the test process."""
        waits.append(delay)
        if len(waits) == 3:
            stop.set()

    def run_once():
        """Two failures followed by success exercise retry and delay reset."""
        calls.append(True)
        if len(calls) < 3:
            raise ValueError("private-provider-message")

    monkeypatch.setattr(stop, "wait", wait)
    monkeypatch.setattr("django.db.connections.close_all", lambda: closes.append(True))
    runtime_process.bounded_loop(
        run_once, lease=SimpleNamespace(check=lambda: None), stop=stop
    )
    assert waits == [4, 8, 2]
    assert len(calls) == len(closes) == 3


def test_lost_lifecycle_lease_is_fatal_not_a_dependency_retry():
    """The service cannot keep performing work after its exclusion proof is lost."""

    def invalid():
        raise ConfigError("Lease invalid")

    with pytest.raises(ConfigError):
        runtime_process.bounded_loop(
            lambda: pytest.fail("must not execute"),
            lease=SimpleNamespace(check=invalid),
            stop=Event(),
        )


def test_gunicorn_worker_print_cannot_receive_private_startup_exception(
    tmp_path, monkeypatch
):
    """Gunicorn prints exception strings outside its logging path during boot."""

    def fail(configuration):
        raise ValueError("private-file-password-or-provider-error")

    monkeypatch.setattr("parishkit.stewardship.runtime_web.configure_web", fail)
    with pytest.raises(ConfigError) as error:
        runtime_process.load_web_application(
            configuration_at(tmp_path), SimpleNamespace(check=lambda: None)
        )
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__
