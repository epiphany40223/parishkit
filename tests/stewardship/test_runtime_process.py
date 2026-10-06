"""Operational dispatch cannot turn a role flag into authority or leak failures."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_process
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.startup_interlock import StartupLease

from .bootstrap_factory import bootstrap_fixture
from .test_runtime_topology import configuration_at


@pytest.mark.parametrize("production", [False, True])
def test_web_process_settings_keep_finite_reserved_headroom(tmp_path, production):
    """The supervisor uses the same process budget as downloads and Compose."""
    configuration = configuration_at(tmp_path, production=production)
    options = runtime_process.gunicorn_options(configuration)
    assert options["workers"] == 2
    assert options["threads"] == 8
    assert options["accesslog"] is None
    assert options["forwarded_allow_ips"] == ""
    budget = configuration.runtime_budget
    assert options["graceful_timeout"] > budget.download_seconds
    # The drain kill, and its durable entry, come before Docker's kill (#374).
    assert options["graceful_timeout"] == budget.drain_seconds - 15
    assert options["graceful_timeout"] < options["timeout"]
    assert options["timeout"] < configuration.runtime_budget.proxy_timeout_seconds
    assert options["reload"] is (not production)
    assert options["preload_app"] is False
    assert options["control_socket_disable"] is True
    assert options["worker_class"] == (
        "parishkit.stewardship.web_worker.DrainingThreadWorker"
    )
    assert options["post_worker_init"] is runtime_process.admitted_worker_started
    assert options["worker_exit"] is runtime_process.admitted_worker_exited


def test_web_runs_the_recording_master_with_its_own_login(tmp_path, monkeypatch):
    """The master that kills workers at a limit is the one that logs it (#374).

    It reads the web's SQL login only when an entry is written.
    """
    from parishkit.stewardship import web_supervisor

    configuration = configuration_at(tmp_path, production=True)
    started = []

    class Master:
        """Stands in for the arbiter; records what it was built with."""

        def __init__(self, app, *, database):
            started.append((app, database))

        def run(self):
            """Return at once instead of serving."""

    monkeypatch.setattr(web_supervisor, "RecordingArbiter", Master)
    monkeypatch.setattr(runtime_process, "private_directory", Mock())
    reads = []
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_database.database_settings",
        lambda value: reads.append(value) or {"USER": "pk_stewardship_web"},
    )
    assert runtime_process.serve_web(configuration, Mock()) == 0
    [(app, database)] = started
    assert app.cfg.graceful_timeout == configuration.runtime_budget.web_grace_seconds
    assert reads == []
    assert database() == {"USER": "pk_stewardship_web"}
    assert reads == [configuration]


def test_sibling_consumers_share_the_web_stop_margin():
    """One margin before Docker's kill for every process that kills at a limit."""
    from parishkit.stewardship.runtime_budget import STOP_MARGIN_SECONDS

    assert runtime_process.SiblingConsumer.KILL_MARGIN == STOP_MARGIN_SECONDS


@pytest.mark.parametrize(
    ("role", "entry"),
    [
        (ServiceRole.WEB, "serve_web"),
        (ServiceRole.WORKER, "serve_background"),
        (ServiceRole.SCHEDULER, "serve_background"),
    ],
)
def test_runtime_dispatch_holds_real_online_lease_until_runner_exits(
    tmp_path, monkeypatch, role, entry
):
    """Offline migration cannot start even before the runtime opens its web port."""
    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=role)
    monkeypatch.setattr(runtime_process, "load_deployment", lambda path: configuration)
    monkeypatch.setattr(runtime_process, "configure_logging", lambda: None)
    called = []

    def wait(config):
        """The database wait runs first, already under the online lease (#453)."""
        from parishkit.stewardship.runtime_paths import RuntimeLayout

        assert called == []
        called.append("waited")
        with (
            pytest.raises(ConfigError),
            StartupLease(RuntimeLayout(config).interlock, offline=True),
        ):
            pass

    monkeypatch.setattr("parishkit.stewardship.runtime_database.await_database", wait)

    def runner(config, lease, **options):
        """The lifecycle inode is real; only the process body is substituted."""
        if role is ServiceRole.WORKER:
            assert options == {"source": False}
        called.append(config)
        lease.check()
        with pytest.raises(ConfigError), StartupLease(lease.path, offline=True):
            pass
        return 0

    monkeypatch.setattr(runtime_process, entry, runner)
    assert main(["runtime", "--config", "operator-input.yaml"]) == 0
    assert called == ["waited", configuration]
    from parishkit.stewardship.runtime_paths import RuntimeLayout

    with StartupLease(RuntimeLayout(configuration).interlock, offline=True):
        pass


@pytest.mark.parametrize(
    "role,queue,selected",
    [
        (ServiceRole.WORKER, "source", "source"),
        (ServiceRole.MAIL_DISPATCH, "mail", "mail"),
        (ServiceRole.WORKER, "mail", None),
        (ServiceRole.MAIL_DISPATCH, "source", None),
        (ServiceRole.SCHEDULER, "source", None),
        (ServiceRole.SCHEDULER, "mail", None),
    ],
)
def test_runtime_queue_option_selects_only_its_own_sibling_consumer(
    tmp_path, monkeypatch, role, queue, selected
):
    """``--queue source`` is the worker's sibling, ``--queue mail`` mail's.

    Any other pairing is refused before a process is assembled.
    """
    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=role)
    monkeypatch.setattr(runtime_process, "load_deployment", lambda path: configuration)
    monkeypatch.setattr(runtime_process, "configure_logging", lambda: None)
    wait = Mock()
    monkeypatch.setattr("parishkit.stewardship.runtime_database.await_database", wait)
    runner = Mock(return_value=0)
    monkeypatch.setattr(runtime_process, "serve_background", runner)
    result = main(["runtime", "--config", "operator-input.yaml", "--queue", queue])
    # A refused pairing is refused before waiting for anything.
    assert wait.called is (selected is not None)
    if selected is None:
        assert result == 2
        runner.assert_not_called()
    else:
        assert result == 0
        assert runner.call_args.kwargs == {selected: True}


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


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        ("database", "database_unavailable"),
        (ConfigError("private-input-token"), "configuration_unavailable"),
        (OSError("private-input-token"), "filesystem_unavailable"),
        (ValueError("private-input-token"), "unexpected_failure"),
    ],
)
def test_startup_rejected_names_its_category_at_error(
    tmp_path, monkeypatch, capsys, caplog, error, kind
):
    """The rejection says which kind of check refused, never its text (#453)."""
    import json

    from django.db.utils import OperationalError

    from parishkit.stewardship.observability import SafeJsonFormatter

    if error == "database":
        error = OperationalError("private-input-token")
    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=ServiceRole.SCHEDULER)
    monkeypatch.setattr(runtime_process, "load_deployment", lambda path: configuration)
    monkeypatch.setattr(runtime_process, "configure_logging", lambda: None)

    def wait(config):
        """The database wait refused, or the runner failed after it."""
        if isinstance(error, OperationalError):
            raise error

    def runner(config, lease, **options):
        """Only reached when the wait succeeded."""
        raise error

    monkeypatch.setattr("parishkit.stewardship.runtime_database.await_database", wait)
    monkeypatch.setattr(runtime_process, "serve_background", runner)
    with caplog.at_level("ERROR", logger="parishkit.stewardship"):
        assert main(["runtime", "--config", "operator-input.yaml"]) == 2
    events = [json.loads(SafeJsonFormatter().format(r)) for r in caplog.records]
    assert [(e["message"], e["level"], e["extra"]) for e in events] == [
        ("startup_rejected", "ERROR", {"failure_kind": kind})
    ]
    output = capsys.readouterr()
    assert "private-input" not in output.out + output.err + caplog.text
    assert "runtime unavailable" in output.err


def test_bounded_installer_retries_and_closes_database_without_spinning(monkeypatch):
    """Transient failures back off finitely; success resets delay and shutdown wins.

    Only a failure closes the database connection (#639): a successful pass
    keeps it for the next one, so an idle loop never reconnects.
    """
    waits, calls, closes = [], [], []
    stop = Event()

    def wait(delay):
        """Drive deterministic retries without delaying the test process."""
        waits.append(delay)
        if len(waits) == 4:
            stop.set()

    def run_once():
        """Two failures followed by successes exercise retry and delay reset."""
        calls.append(True)
        if len(calls) < 3:
            raise ValueError("private-provider-message")

    monkeypatch.setattr(stop, "wait", wait)
    monkeypatch.setattr("django.db.connections.close_all", lambda: closes.append(True))
    runtime_process.bounded_loop(
        run_once, lease=SimpleNamespace(check=lambda: None), stop=stop
    )
    assert waits == [4, 8, 2, 2]
    assert len(calls) == 4
    assert len(closes) == 2


class IdleLoop:
    """Drive bounded_loop on a fake clock: each wake waits ``delay`` seconds.

    ``work`` lists, per full pass, whether run_once reports work; ``hints``
    lists, per pending() call, whether the cheap check finds work (False once
    exhausted). Records when each full pass and pending() call happened, and
    stops after ``wakes`` heartbeats.
    """

    def __init__(self, monkeypatch, *, wakes, work=(), hints=(), fail=()):
        self.now, self.wakes = 0.0, wakes
        self.work, self.hints, self.fail = list(work), list(hints), set(fail)
        self.passes, self.probes, self.beats, self.closes = [], [], [], []
        self.stop = Event()
        monkeypatch.setattr(self.stop, "wait", self.wait)
        monkeypatch.setattr(
            "django.db.connections.close_all", lambda: self.closes.append(self.now)
        )

    def wait(self, delay):
        """Advance the fake clock instead of sleeping."""
        self.now += delay
        if len(self.beats) >= self.wakes:
            self.stop.set()

    def run_once(self):
        """One full pass, failing on the listed pass numbers."""
        self.passes.append(self.now)
        if len(self.passes) in self.fail:
            raise ValueError("synthetic pass failure")
        return self.work.pop(0) if self.work else False

    def pending(self):
        """The cheap work check."""
        self.probes.append(self.now)
        return self.hints.pop(0) if self.hints else False

    def run(self, **options):
        """Run the loop to completion with this harness's callbacks."""
        runtime_process.bounded_loop(
            self.run_once,
            lease=SimpleNamespace(check=lambda: None),
            stop=self.stop,
            heartbeat=lambda: self.beats.append(self.now),
            pending=self.pending,
            clock=lambda: self.now,
            **options,
        )
        return self


def test_idle_installer_backs_off_full_passes_but_wakes_every_two_seconds(
    monkeypatch,
):
    """Idle full passes spread out to 30 s; heartbeat and check stay at 2 s."""
    loop = IdleLoop(monkeypatch, wakes=60).run()
    # Gaps of 4, 8, 16, then the 30 s ceiling.
    assert loop.passes == [0, 4, 12, 28, 58, 88, 118]
    # Every wake publishes the heartbeat, whatever the full-pass gap.
    assert loop.beats == [2.0 * index for index in range(60)]
    assert max(b - a for a, b in zip(loop.beats, loop.beats[1:], strict=False)) == 2
    # Every wake that is not a full pass asks the cheap check instead.
    assert sorted(loop.passes + loop.probes) == loop.beats
    # A healthy idle loop keeps its connection.
    assert loop.closes == []


def test_idle_installer_wakes_at_once_when_the_cheap_check_finds_work(monkeypatch):
    """Work the check sees runs on the next 2 s wake, then polling stays fast."""
    loop = IdleLoop(
        monkeypatch,
        wakes=15,
        # Deep in idle backoff (next full pass due at 28 s), the check finds
        # work on its ninth call, at 22 s.
        hints=[False] * 8 + [True],
        work=[False, False, False, True, True],
    ).run()
    assert loop.probes[8] == 22
    assert loop.passes[:3] == [0, 4, 12]
    # The work runs on that same wake. While passes keep finding work they
    # run on every wake; once idle again the gap doubles from 2 s.
    assert loop.passes[3:] == [22, 24, 26]
    assert loop.beats[-1] == 28


def test_idle_installer_retries_a_failed_pass_with_a_fresh_connection(monkeypatch):
    """A failure closes the connection, backs off, and retries in full."""
    loop = IdleLoop(monkeypatch, wakes=6, fail={2}).run()
    # Pass 2 (at 4 s) fails: close, wait 4 s, retry the full pass at 8 s,
    # then back off again from there.
    assert loop.closes == [4]
    assert loop.passes == [0, 4, 8, 12]
    assert loop.beats == [0, 2, 4, 8, 10, 12]


def test_failing_cheap_check_reconnects_and_retries_a_full_pass(monkeypatch):
    """The cheap check's own failure is handled like a failed pass."""
    loop = IdleLoop(monkeypatch, wakes=5)

    def broken():
        """The database went away while the loop was idle."""
        loop.probes.append(loop.now)
        if len(loop.probes) == 1:
            raise ValueError("server closed the connection unexpectedly")
        return False

    loop.pending = broken
    loop.run()
    assert loop.probes[0] == 2
    assert loop.closes == [2]
    # Retry after the failure backoff runs a full pass, not just the check.
    assert loop.passes == [0, 6, 10]


def test_loop_without_a_work_check_runs_every_pass(monkeypatch):
    """The configuration installer has no cheap check: it keeps its 2 s pass."""
    passes, stop = [], Event()

    def wait(delay):
        """Stop after five wakes."""
        if len(passes) == 5:
            stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    monkeypatch.setattr(
        "django.db.connections.close_all", lambda: pytest.fail("must stay open")
    )
    runtime_process.bounded_loop(
        lambda: passes.append(True),
        lease=SimpleNamespace(check=lambda: None),
        stop=stop,
    )
    assert len(passes) == 5


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


@pytest.mark.parametrize("lost_before", [False, True])
def test_independent_producer_never_swallows_lost_scheduler_ownership(lost_before):
    """Isolating owner failures must not turn a lost scheduler lease into success."""
    guard = Mock()
    guard.check.side_effect = (
        [ConfigError("Lost scheduler lease")]
        if lost_before
        else [None, ConfigError("Lost scheduler lease")]
    )
    operation = Mock(side_effect=ValueError("private-provider-value"))
    with pytest.raises(ConfigError, match="Lost scheduler lease"):
        runtime_process.independent_producer(guard, operation)
    assert operation.call_count == (0 if lost_before else 1)


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


def test_gunicorn_worker_receipt_hook_sanitizes_private_failures(monkeypatch):
    """Post-init failures are outside the application loader's error boundary."""

    def fail(worker):
        raise ValueError("private-credential-path-or-value")

    monkeypatch.setattr(runtime_process, "publish_worker_receipts", fail)
    with pytest.raises(ConfigError) as error:
        runtime_process.admitted_worker_started(SimpleNamespace(pid=1))
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "target,failure",
    [("metrics", None)]
    + [
        (target, failure)
        for target in ("parishsoft", "google_workspace", "slack")
        for failure in (None, "relay", "stage")
    ],
)
def test_credential_service_publishes_only_after_admission(
    tmp_path, monkeypatch, target, failure
):
    """Key discovery derives from the admitted installer before its queue starts."""
    from parishkit.stewardship.accounts.credential_installation import (
        CredentialInstaller,
    )

    configuration = replace(
        configuration_at(tmp_path),
        service_role=ServiceRole.CREDENTIAL_INSTALLER,
        credential_target=target,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda _: ServiceRole.CREDENTIAL_INSTALLER,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.admit_lifecycle_mounts", Mock()
    )
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.configure_operator_database", Mock()
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_grants.admit_runtime_database", Mock()
    )
    installer, lease = Mock(), Mock()
    monkeypatch.setattr(
        CredentialInstaller, "from_configuration", Mock(return_value=installer)
    )
    publish = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.handoff_discovery.publish_handoff", publish
    )
    relay = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.source.setup_exchange.relay_pending", relay
    )
    mail_relay = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_mail_exchange.relay_pending", mail_relay
    )
    slack_send = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_notifications.run_pending", slack_send
    )
    probes = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.backup_probes.run_pending_probes", probes
    )
    stage_initial = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_credential_installation.stage_initial_credential",
        stage_initial,
    )
    if failure:
        failing = (
            stage_initial
            if failure == "stage"
            else {
                "parishsoft": relay,
                "google_workspace": mail_relay,
                "slack": slack_send,
            }[target]
        )
        failing.side_effect = RuntimeError("synthetic setup failure")

    def serve(run_once, actual_lease, *, pending):
        """Queue processing cannot race ahead of the advertised encryption key."""
        publish.assert_called_once_with(installer.files.private)
        lease.check.assert_called_once()
        assert actual_lease is lease
        assert callable(pending)
        if failure:
            with pytest.raises(RuntimeError, match="synthetic setup failure"):
                run_once()
            # A stuck exchange cannot starve ordinary rotations or rollback.
            installer.run_once.assert_called_once()
            failing.assert_called_once()
            return 0
        assert run_once() is True
        installer.run_once.assert_called_once()
        if target == "parishsoft":
            relay.assert_called_once_with(installer.files.private)
            assert lease.check.call_count == 3
        else:
            relay.assert_not_called()
        if target == "google_workspace":
            mail_relay.assert_called_once_with(installer.files.private)
            # Off-site backup access checks use this key and its egress.
            probes.assert_called_once_with(installer.files.path, check=lease.check)
            assert lease.check.call_count == 3
        else:
            mail_relay.assert_not_called()
            probes.assert_not_called()
        if target == "slack":
            slack_send.assert_called_once_with(
                installer.files.private, check=lease.check
            )
            assert lease.check.call_count == 3
        else:
            slack_send.assert_not_called()
        if target == "metrics":
            stage_initial.assert_not_called()
        else:
            stage_initial.assert_called_once_with(installer.files)
            # Idle steps report no work; a recorded installation's receipt
            # counts only while its request is still pending (#639).
            installer.run_once.return_value = None
            for step in (relay, mail_relay, slack_send):
                step.return_value = False
            probes.return_value = 0
            stage_initial.return_value = SimpleNamespace(state="applied")
            assert run_once() is False
            stage_initial.return_value = SimpleNamespace(state="awaiting_ack")
            assert run_once() is True
        return 0

    monkeypatch.setattr(runtime_process, "serve_installer_loop", serve)
    assert runtime_process.serve_credential_installer(configuration, lease) == 0


def test_configuration_service_restores_on_an_idle_pass(tmp_path, monkeypatch):
    """An empty queue runs the requestless restore; a selected request runs alone."""
    from contextlib import nullcontext

    from parishkit.stewardship.accounts.configuration_service import (
        ConfigurationInstaller,
    )

    configuration = replace(
        configuration_at(tmp_path), service_role=ServiceRole.CONFIG_INSTALLER
    )
    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda _: ServiceRole.CONFIG_INSTALLER,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.admit_lifecycle_mounts", Mock()
    )
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.configure_operator_database", Mock()
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_grants.admit_runtime_database", Mock()
    )
    installer, lease = Mock(), Mock()
    monkeypatch.setattr(
        ConfigurationInstaller, "from_configuration", Mock(return_value=installer)
    )
    monkeypatch.setattr(
        runtime_process, "next_configuration_request", Mock(side_effect=[None, "id"])
    )
    monkeypatch.setattr(runtime_process, "installer_request", lambda _: nullcontext())

    def serve(run_once, actual_lease):
        """The idle pass restores; a queued request is installed without one.

        The configuration installer has no cheap work check, so its loop
        runs the full pass on every wake.
        """
        assert actual_lease is lease
        run_once()
        installer.restore_refused.assert_called_once_with()
        installer.run_request.assert_not_called()
        run_once()
        installer.run_request.assert_called_once_with("id")
        installer.restore_refused.assert_called_once_with()
        return 0

    monkeypatch.setattr(runtime_process, "serve_installer_loop", serve)
    assert runtime_process.serve_configuration_installer(configuration, lease) == 0


@pytest.mark.parametrize(
    "role,held",
    [
        (ServiceRole.WORKER, False),
        (ServiceRole.SCHEDULER, False),
        (ServiceRole.SCHEDULER, True),
        (ServiceRole.MAIL_DISPATCH, False),
    ],
)
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize(
    "failing_producer",
    [
        None,
        "expiry",
        "finalization",
        "mail",
        "slack",
        "campaign",
        "family_tests",
        "boundary",
        "schedules",
        "digests",
        "daily",
        "daily_finalization",
        "weekly",
        "weekly_finalization",
        "source",
        "cleanup",
        "setup_cleanup",
        "export_cleanup",
        "facts",
        "verification",
        "operational",
        "operational_fanout",
        "operational_slack",
        "maintenance",
    ],
)
def test_background_process_keeps_scope_receipts_and_cleans_up_on_exit(
    tmp_path, monkeypatch, role, fail, held, failing_producer
):
    """Restore signals and close broker/SQL after normal or failed drainage."""
    import signal

    from parishkit.stewardship import runtime_background

    configuration = replace(configuration_at(tmp_path), service_role=role)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    broker, lease, closes, receipts, healthy = Mock(), Mock(), Mock(), Mock(), Mock()
    assembled = SimpleNamespace(broker=broker, store=object(), handlers={}, receipts={})
    stops, selected = [], []
    sibling, mail_sibling = Mock(), Mock()
    monkeypatch.setattr(runtime_process, "SourceConsumer", sibling)
    monkeypatch.setattr(runtime_process, "MailConsumer", mail_sibling)

    def configure(config, *, stop, heartbeat, queues=None):
        """Retain the common stop event and exercise the actual health callback."""
        assert config is configuration
        stops.append(stop)
        selected.append(queues)
        heartbeat()
        return assembled

    def serve(actual, *, lease, stop, heartbeat, **kwargs):
        """Substitute the external process loop, not runtime lifecycle logic."""
        assert actual is broker and stop is stops[0]
        if role is ServiceRole.SCHEDULER:
            outputs = {
                "operational": "operational-receipt",
                "operational_fanout": "operational-fanout-receipt",
                "security_fanout": "security-fanout-receipt",
                "operational_slack": "operational-slack-receipt",
                "finalization": "finalization-receipt",
                "boundary": "boundary-receipt",
                "schedules": "schedule-receipt",
                "digests": "digest-receipt",
                "daily": "daily-receipt",
                "daily_finalization": "daily-finalization-receipt",
                "weekly": "weekly-receipt",
                "weekly_finalization": "weekly-finalization-receipt",
                "source": "source-receipt",
                "cleanup": "cleanup-receipt",
                "export_cleanup": "export-cleanup-receipt",
                "facts": "facts-receipt",
                "verification": "verification-receipt",
                "setup_cleanup": "setup-cleanup-receipt",
                "maintenance": "maintenance-receipt",
            }
            # One patched fanout producer serves both alert owners, so its
            # failure loses the security receipt as well.
            failing = {failing_producer}
            if failing_producer == "operational_fanout":
                failing.add("security_fanout")
            assert kwargs["produce"](guard) == (
                (
                    tuple(
                        outputs[owner]
                        for owner in (
                            "operational",
                            "finalization",
                        )
                        if owner not in failing
                    )
                )
                if held
                else tuple(
                    receipt
                    for owner, receipt in outputs.items()
                    if owner not in failing
                )
            )
        else:
            # Consumers acknowledge rotated credentials from their idle timer.
            kwargs["idle"]()
            # Each consumer container runs its second process (#336 and the
            # second mail consumer).
            assert kwargs["companion"] is {
                ServiceRole.WORKER: sibling.return_value,
                ServiceRole.MAIL_DISPATCH: mail_sibling.return_value,
            }.get(role)
            rotations.assert_called_once_with(configuration, assembled.receipts)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stop.is_set()
        if fail:
            raise RuntimeError("synthetic startup failure")
        return 0

    producer, matching = Mock(return_value=("source-receipt",)), Mock()
    schedules = Mock(return_value=("schedule-receipt",))
    digests = Mock(return_value=("digest-receipt",))
    daily = Mock(return_value=("daily-receipt",))
    daily_finalization = Mock(return_value=("daily-finalization-receipt",))
    weekly = Mock(return_value=("weekly-receipt",))
    weekly_finalization = Mock(return_value=("weekly-finalization-receipt",))
    guard, cleanup = Mock(), Mock(return_value=("cleanup-receipt",))
    export_cleanup = Mock(return_value=("export-cleanup-receipt",))
    facts = Mock(return_value=("facts-receipt",))
    verification = Mock(return_value=("verification-receipt",))
    operational = Mock(return_value=("operational-receipt",))
    operational_slack = Mock(return_value=("operational-slack-receipt",))
    maintenance = Mock(return_value=("maintenance-receipt",))
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.automation_maintenance.MaintenanceProducer",
        lambda: maintenance,
    )

    def fanout(guard, owner=None):
        """The scheduler runs one fanout producer per alert owner in turn."""
        from parishkit.stewardship.jobs.security_owner import SECURITY

        if owner is None:
            return ("operational-fanout-receipt",)
        assert owner is SECURITY
        return ("security-fanout-receipt",)

    operational_fanout = Mock(side_effect=fanout)
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_slack_tasks.produce_slack",
        operational_slack,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_fanout.produce_fanout",
        operational_fanout,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_collection.produce_collection",
        operational,
    )
    expiry = Mock(return_value=0)
    matching.side_effect = lambda _: expiry.assert_called_once_with(guard)
    if held:
        matching.side_effect = ConfigError("Selected initial setup is held.")
    hold = Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_startup.initial_setup_hold", hold
    )
    monkeypatch.setattr(runtime_background, "configure_background", configure)
    monkeypatch.setattr(runtime_background, "matching_authority", matching)
    monkeypatch.setattr(
        "parishkit.stewardship.source.production.SourceProducer", lambda _: producer
    )
    monkeypatch.setattr(
        "parishkit.stewardship.campaigns.schedule_production.FamilyScheduleProducer",
        lambda _: schedules,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.campaigns.digest_schedule_planning.DigestScheduleProducer",
        lambda _: digests,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.digest_ownership.DailyDigestProducer",
        lambda _: daily,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.digest_finalization.DailyDigestFinalizeProducer",
        lambda _: daily_finalization,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.weekly_ownership.WeeklyDigestProducer",
        lambda _: weekly,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.digest_finalization.WeeklyDigestFinalizeProducer",
        lambda _: weekly_finalization,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.branding_cleanup.produce_cleanup", cleanup
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.export_cleanup.produce_cleanup", export_cleanup
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.fact_production.produce_facts", facts
    )
    monkeypatch.setattr(
        "parishkit.stewardship.reports.verification_production.produce_verifications",
        verification,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_staging.produce_setup_expiry", expiry
    )
    finalization = Mock(return_value=("finalization-receipt",))
    monkeypatch.setattr(
        "parishkit.stewardship.source.setup_final_production.produce_finalization",
        finalization,
    )
    mail_recovery = Mock(return_value=0)
    campaign_recovery, slack_recovery = Mock(return_value=0), Mock(return_value=0)
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.campaign_mail_delivery.recover_pending",
        campaign_recovery,
    )
    family_recovery = Mock(return_value=0)
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_test_tasks.recover_pending",
        family_recovery,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_mail.recover_pending", mail_recovery
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_notifications.recover_pending",
        slack_recovery,
    )
    setup_cleanup = Mock(return_value=("setup-cleanup-receipt",))
    boundary = Mock(return_value=("boundary-receipt",))
    monkeypatch.setattr(
        "parishkit.stewardship.campaigns.boundary_production.produce_boundaries",
        boundary,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.source.setup_cleanup.produce_setup_cleanup",
        setup_cleanup,
    )
    if failing_producer:
        {
            "expiry": expiry,
            "finalization": finalization,
            "mail": mail_recovery,
            "slack": slack_recovery,
            "campaign": campaign_recovery,
            "family_tests": family_recovery,
            "boundary": boundary,
            "schedules": schedules,
            "digests": digests,
            "daily": daily,
            "daily_finalization": daily_finalization,
            "weekly": weekly,
            "weekly_finalization": weekly_finalization,
            "source": producer,
            "cleanup": cleanup,
            "setup_cleanup": setup_cleanup,
            "export_cleanup": export_cleanup,
            "facts": facts,
            "verification": verification,
            "operational": operational,
            "operational_fanout": operational_fanout,
            "operational_slack": operational_slack,
            "maintenance": maintenance,
        }[failing_producer].side_effect = RuntimeError("synthetic-owner-failure")
    rotations = Mock(return_value=[])
    monkeypatch.setattr(
        "parishkit.stewardship.credential_runtime.acknowledge_rotations", rotations
    )
    monkeypatch.setattr("parishkit.stewardship.jobs.processes.serve_consumer", serve)
    monkeypatch.setattr("parishkit.stewardship.jobs.processes.serve_scheduler", serve)
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        receipts,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.installer_health.publish_heartbeat", healthy
    )
    monkeypatch.setattr("django.db.connections.close_all", closes)
    if fail:
        with pytest.raises(RuntimeError):
            runtime_process.serve_background(configuration, lease)
    else:
        assert runtime_process.serve_background(configuration, lease) == 0
    assert stops[0].is_set()
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
    receipts.assert_called_once_with(configuration, assembled.receipts)
    healthy.assert_called_once()
    assert lease.check.call_count == 2
    if role is ServiceRole.WORKER:
        # The main worker process consumes everything but source work and
        # starts, forwards stops to and finally closes its source sibling.
        assert selected == [frozenset({WorkQueue.GENERAL, WorkQueue.RESTORE_GENERAL})]
        sibling.assert_called_once()
        sibling.return_value.close.assert_called_once()
    else:
        assert selected == [None]
        sibling.assert_not_called()
    broker.app.close.assert_called_once()
    closes.assert_called_once()
    if role is ServiceRole.SCHEDULER:
        matching.assert_called_once_with(assembled.store)
        finalization.assert_called_once_with(assembled.store, guard)
        expiry.assert_called_once_with(guard)
        operational.assert_called_once_with(guard)
        if held:
            operational_fanout.assert_not_called()
            operational_slack.assert_not_called()
            hold.assert_called_once_with(assembled.store)
            for operation in (
                boundary,
                schedules,
                digests,
                daily,
                daily_finalization,
                weekly,
                weekly_finalization,
                producer,
                cleanup,
                export_cleanup,
                facts,
                verification,
                mail_recovery,
                campaign_recovery,
                family_recovery,
                slack_recovery,
                maintenance,
            ):
                operation.assert_not_called()
            return
        hold.assert_not_called()
        from parishkit.stewardship.jobs.security_owner import SECURITY

        assert operational_fanout.call_args_list == [
            call(guard),
            call(guard, SECURITY),
        ]
        operational_slack.assert_called_once_with(guard)
        boundary.assert_called_once_with(guard)
        schedules.assert_called_once_with(guard)
        digests.assert_called_once_with(guard)
        daily.assert_called_once_with(guard)
        daily_finalization.assert_called_once_with(guard)
        weekly.assert_called_once_with(guard)
        weekly_finalization.assert_called_once_with(guard)
        producer.assert_called_once_with(guard)
        cleanup.assert_called_once_with(guard)
        export_cleanup.assert_called_once_with(guard)
        facts.assert_called_once_with(guard)
        verification.assert_called_once_with(guard)
        expiry.assert_called_once_with(guard)
        mail_recovery.assert_called_once_with()
        campaign_recovery.assert_called_once_with()
        family_recovery.assert_called_once_with()
        slack_recovery.assert_called_once_with()
        maintenance.assert_called_once_with(guard)
        # Twenty-four independent producers, each bracketed by two checks.
        assert guard.check.call_count == 48
    else:
        operational.assert_not_called()
        operational_fanout.assert_not_called()
        operational_slack.assert_not_called()
        daily.assert_not_called()
        daily_finalization.assert_not_called()
        weekly.assert_not_called()
        weekly_finalization.assert_not_called()
        boundary.assert_not_called()
        mail_recovery.assert_not_called()
        cleanup.assert_not_called()
        export_cleanup.assert_not_called()
        facts.assert_not_called()
        verification.assert_not_called()
        expiry.assert_not_called()
        maintenance.assert_not_called()


def test_background_failed_admission_restores_signals_without_publishing_receipts(
    tmp_path, monkeypatch
):
    """Partial assembly is never advertised as a loaded consumer or left running."""
    import signal

    previous = signal.getsignal(signal.SIGTERM)
    receipts, closes = Mock(), Mock()
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_background.configure_background",
        Mock(side_effect=ConfigError("Invalid assembly")),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        receipts,
    )
    monkeypatch.setattr("django.db.connections.close_all", closes)
    with pytest.raises(ConfigError):
        runtime_process.serve_background(configuration_at(tmp_path), Mock())
    assert signal.getsignal(signal.SIGTERM) == previous
    receipts.assert_not_called()
    closes.assert_called_once()
