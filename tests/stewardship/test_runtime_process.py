"""Operational dispatch cannot turn a role flag into authority or leak failures."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_process
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole
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
    assert options["graceful_timeout"] > configuration.runtime_budget.download_seconds
    assert options["timeout"] < configuration.runtime_budget.proxy_timeout_seconds
    assert options["reload"] is (not production)
    assert options["preload_app"] is False
    assert options["post_worker_init"] is runtime_process.admitted_worker_started


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

    def runner(config, lease):
        """The lifecycle inode is real; only the process body is substituted."""
        called.append(config)
        lease.check()
        with pytest.raises(ConfigError), StartupLease(lease.path, offline=True):
            pass
        return 0

    monkeypatch.setattr(runtime_process, entry, runner)
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

    def serve(run_once, actual_lease):
        """Queue processing cannot race ahead of the advertised encryption key."""
        publish.assert_called_once_with(installer.files.private)
        lease.check.assert_called_once()
        assert actual_lease is lease
        if failure:
            with pytest.raises(RuntimeError, match="synthetic setup failure"):
                run_once()
            # A stuck exchange cannot starve ordinary rotations or rollback.
            installer.run_once.assert_called_once()
            failing.assert_called_once()
            return 0
        run_once()
        installer.run_once.assert_called_once()
        if target == "parishsoft":
            relay.assert_called_once_with(installer.files.private)
            assert lease.check.call_count == 3
        else:
            relay.assert_not_called()
        if target == "google_workspace":
            mail_relay.assert_called_once_with(installer.files.private)
            assert lease.check.call_count == 3
        else:
            mail_relay.assert_not_called()
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
        return 0

    monkeypatch.setattr(runtime_process, "serve_installer_loop", serve)
    assert runtime_process.serve_credential_installer(configuration, lease) == 0


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
        "source",
        "cleanup",
        "setup_cleanup",
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
    stops = []

    def configure(config, *, stop, heartbeat):
        """Retain the common stop event and exercise the actual health callback."""
        assert config is configuration
        stops.append(stop)
        heartbeat()
        return assembled

    def serve(actual, *, lease, stop, heartbeat, **kwargs):
        """Substitute the external process loop, not runtime lifecycle logic."""
        assert actual is broker and stop is stops[0]
        if role is ServiceRole.SCHEDULER:
            outputs = {
                "finalization": "finalization-receipt",
                "source": "source-receipt",
                "cleanup": "cleanup-receipt",
                "setup_cleanup": "setup-cleanup-receipt",
            }
            assert kwargs["produce"](guard) == (
                (
                    ()
                    if failing_producer == "finalization"
                    else ("finalization-receipt",)
                )
                if held
                else tuple(
                    receipt
                    for owner, receipt in outputs.items()
                    if owner != failing_producer
                )
            )
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stop.is_set()
        if fail:
            raise RuntimeError("synthetic startup failure")
        return 0

    producer, matching = Mock(return_value=("source-receipt",)), Mock()
    guard, cleanup = Mock(), Mock(return_value=("cleanup-receipt",))
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
        "parishkit.stewardship.accounts.branding_cleanup.produce_cleanup", cleanup
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
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_mail.recover_pending", mail_recovery
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_notifications.recover_pending",
        slack_recovery,
    )
    setup_cleanup = Mock(return_value=("setup-cleanup-receipt",))
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
            "source": producer,
            "cleanup": cleanup,
            "setup_cleanup": setup_cleanup,
        }[failing_producer].side_effect = RuntimeError("synthetic-owner-failure")
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
    broker.app.close.assert_called_once()
    closes.assert_called_once()
    if role is ServiceRole.SCHEDULER:
        matching.assert_called_once_with(assembled.store)
        finalization.assert_called_once_with(assembled.store, guard)
        expiry.assert_called_once_with(guard)
        if held:
            hold.assert_called_once_with(assembled.store)
            for operation in (
                producer,
                cleanup,
                mail_recovery,
                campaign_recovery,
                slack_recovery,
            ):
                operation.assert_not_called()
            return
        hold.assert_not_called()
        producer.assert_called_once_with(guard)
        cleanup.assert_called_once_with(guard)
        expiry.assert_called_once_with(guard)
        mail_recovery.assert_called_once_with()
        campaign_recovery.assert_called_once_with()
        slack_recovery.assert_called_once_with()
        assert guard.check.call_count == 16
    else:
        mail_recovery.assert_not_called()
        cleanup.assert_not_called()
        expiry.assert_not_called()


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
