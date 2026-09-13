"""Only a complete admitted consumer may confirm target-specific installation."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import credential_runtime
from parishkit.stewardship.accounts.credential_installation import (
    CredentialValidationUnavailable,
)
from parishkit.stewardship.accounts.metrics_credentials import MetricsCredential
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole

from .bootstrap_factory import bootstrap_fixture


@pytest.fixture(autouse=True)
def logging_boundary(monkeypatch):
    """CLI unit tests must not retain handlers bound to a finished capture stream."""
    configure = Mock()
    monkeypatch.setattr(credential_runtime, "configure_logging", configure)
    return configure


def test_only_metrics_has_a_local_complete_validator():
    """Provider tests and retirement evidence cannot be replaced with syntax checks."""
    assert credential_runtime.validate_metrics_candidate(
        MetricsCredential.generate().serialize()
    )
    with pytest.raises(ValueError):
        credential_runtime.validate_metrics_candidate(b"plain-token")
    with pytest.raises(CredentialValidationUnavailable):
        credential_runtime.validation_unavailable(b"private-provider-value")


def test_acknowledgement_failure_before_django_setup_is_private():
    """A fresh CLI process must classify startup errors without importing models."""
    import json
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from parishkit.stewardship.cli import main; raise SystemExit(main())",
            "acknowledge-credential",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    assert "credential acknowledgement refused" in result.stderr
    event = json.loads(result.stderr.splitlines()[0])
    assert event["extra"]["failure_kind"] == "configuration_unavailable"


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    """Keep process/SQL effects explicit so each refusal can prove no ACK was made."""
    configuration, _ = bootstrap_fixture(tmp_path)
    credential = MetricsCredential.generate()
    raw = credential.serialize()
    configuration = replace(
        configuration,
        service_role=ServiceRole.WEB,
        secrets={"metrics": tmp_path / "credential"},
    )
    row = SimpleNamespace(
        target="metrics", resulting_fingerprint=credential.receipt, state="awaiting_ack"
    )
    receipts = {"metrics": credential.receipt}
    acknowledgements, closes = [], []
    runtime = SimpleNamespace(
        telemetry_client=None,
        client=SimpleNamespace(
            connection_pool=SimpleNamespace(disconnect=lambda: closes.append("valkey"))
        ),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.configure_web", lambda config: runtime
    )
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.loaded_service_receipts",
        lambda config: receipts.copy(),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.secret_models.SecretReplacementRequest.objects.get",
        lambda **kwargs: row,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.key_files.read_private", lambda path: raw
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.credential_installation.acknowledge_loaded_credential",
        lambda **kwargs: acknowledgements.append(kwargs),
    )
    monkeypatch.setattr("django.db.connections.close_all", lambda: closes.append("sql"))
    return configuration, row, receipts, acknowledgements, closes


def test_acknowledgement_requires_loaded_worker_and_mounted_file_match(admitted):
    """The operator command passes exact mounted bytes only after the cohort check."""
    configuration, _, receipts, acknowledgements, closes = admitted
    identifier = uuid4()
    credential_runtime.acknowledge_web(configuration, identifier)
    assert len(acknowledgements) == 1
    value = acknowledgements[0]
    assert value["request_id"] == identifier
    assert value["consumer"] == "web"
    assert MetricsCredential.parse(value["loaded_value"]).receipt == receipts["metrics"]
    assert closes == ["sql", "valkey"]


@pytest.mark.parametrize("failure", ["target", "receipt", "state", "mounted", "cohort"])
def test_no_acknowledgement_for_incomplete_or_changed_evidence(
    admitted, monkeypatch, failure
):
    """Every rejected proof still closes connections without making a durable ACK."""
    configuration, row, receipts, acknowledgements, closes = admitted
    if failure == "target":
        row.target = "token_private"
    elif failure == "receipt":
        row.resulting_fingerprint = "b" * 64
    elif failure == "state":
        row.state = "failed"
    elif failure == "mounted":
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.key_files.read_private",
            lambda path: MetricsCredential.generate().serialize(),
        )
    else:
        snapshots = iter([receipts, {"metrics": "b" * 64}])
        monkeypatch.setattr(
            "parishkit.stewardship.consumer_runtime.loaded_service_receipts",
            lambda config: next(snapshots),
        )
    with pytest.raises(ConfigError):
        credential_runtime.acknowledge_web(configuration, uuid4())
    assert not acknowledgements
    assert closes == ["sql", "valkey"]


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--config", "private-path"],
        ["--config", "private-path", "--request-id", "private-invalid-value"],
    ],
)
def test_acknowledgement_cli_never_echoes_private_input(
    arguments, capsys, logging_boundary
):
    """Configuration and request parser failures use fixed safe operator guidance."""
    assert main(["acknowledge-credential", *arguments]) == 2
    logging_boundary.assert_called_once_with()
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
    assert "acknowledgement refused" in output.err


@pytest.mark.parametrize("role", [ServiceRole.WEB, ServiceRole.MAIL_DISPATCH])
def test_acknowledgement_cli_success_requires_lifecycle_lease(
    tmp_path, monkeypatch, capsys, role
):
    """A shared real lease covers the whole admitted durable acknowledgement."""
    from parishkit.stewardship.runtime_paths import RuntimeLayout
    from parishkit.stewardship.startup_interlock import StartupLease

    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=role)
    monkeypatch.setattr(
        credential_runtime, "load_deployment", lambda path: configuration
    )
    calls = []

    def acknowledge(kind, config, identifier, *, lease=None):
        """Migration remains excluded while this consumer confirmation is active."""
        with (
            pytest.raises(ConfigError),
            StartupLease(RuntimeLayout(config).interlock, offline=True),
        ):
            pass
        calls.append((kind, identifier))

    monkeypatch.setattr(
        credential_runtime,
        "acknowledge_web",
        lambda *args, **kwargs: acknowledge("web", *args, **kwargs),
    )
    monkeypatch.setattr(
        credential_runtime,
        "acknowledge_background",
        lambda *args, **kwargs: acknowledge("background", *args, **kwargs),
    )
    identifier = uuid4()
    assert (
        main(
            [
                "acknowledge-credential",
                "--config",
                "operator.yaml",
                "--request-id",
                str(identifier),
            ]
        )
        == 0
    )
    assert calls == [("web" if role is ServiceRole.WEB else "background", identifier)]
    assert "acknowledgement recorded" in capsys.readouterr().out


@pytest.mark.parametrize(
    "role,target",
    [
        (ServiceRole.WORKER, "parishsoft"),
        (ServiceRole.SCHEDULER, "token_public"),
        (ServiceRole.MAIL_DISPATCH, "google_workspace"),
    ],
)
@pytest.mark.parametrize("failure", [None, "admission", "cohort", "mounted", "state"])
def test_background_ack_requires_live_original_process_and_cleans_resources(
    admitted, monkeypatch, role, target, failure
):
    """Admission cannot create replacement process evidence to acknowledge itself."""
    configuration, row, receipts, acknowledgements, closes = admitted
    configuration = replace(
        configuration,
        service_role=role,
        secrets={target: configuration.secrets["metrics"]},
    )
    row.target = target
    receipt = row.resulting_fingerprint
    receipts.clear()
    receipts[target] = receipt
    broker, lease = Mock(), Mock()
    stopped = []

    def configure(config, *, stop, heartbeat):
        """No queue or receipt publisher is started during acknowledgement admission."""
        assert config is configuration and heartbeat == lease.check
        stopped.append(stop)
        if failure == "admission":
            raise ConfigError("Synthetic admission refusal")
        return SimpleNamespace(broker=broker)

    monkeypatch.setattr(
        "parishkit.stewardship.runtime_background.configure_background", configure
    )
    publisher = Mock(side_effect=AssertionError("Command must not publish readiness"))
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        publisher,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.metrics_credentials.credential_receipt",
        lambda value, name: "0" * 64 if failure == "mounted" else receipt,
    )
    if failure == "cohort":
        evidence = iter([receipts, {}])
        monkeypatch.setattr(
            "parishkit.stewardship.consumer_runtime.loaded_service_receipts",
            lambda config: next(evidence),
        )
    if failure == "state":
        row.state = "failed"
    if failure:
        with pytest.raises(ConfigError):
            credential_runtime.acknowledge_background(
                configuration, uuid4(), lease=lease
            )
        assert not acknowledgements
    else:
        identifier = uuid4()
        credential_runtime.acknowledge_background(
            configuration, identifier, lease=lease
        )
        assert acknowledgements[0]["consumer"] == role.value
        assert acknowledgements[0]["request_id"] == identifier
    assert closes == ["sql"]
    assert stopped[0].is_set()
    publisher.assert_not_called()
    if failure != "admission":
        broker.app.close.assert_called_once()


@pytest.mark.parametrize(
    "role", [ServiceRole.WEB, ServiceRole.BACKUP_WORKER, ServiceRole.CONFIG_INSTALLER]
)
def test_background_ack_does_not_admit_unimplemented_or_wrong_runtime(admitted, role):
    """An unrelated or unimplemented service cannot borrow consumer proof."""
    configuration, _, _, acknowledgements, _ = admitted
    with pytest.raises(ConfigError):
        credential_runtime.acknowledge_background(
            replace(configuration, service_role=role), uuid4(), lease=Mock()
        )
    assert not acknowledgements
