"""Only a complete admitted consumer may confirm target-specific installation."""

from dataclasses import replace
from types import SimpleNamespace
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


def test_only_metrics_has_a_local_complete_validator():
    """Provider tests and retirement evidence cannot be replaced with syntax checks."""
    assert credential_runtime.validate_metrics_candidate(
        MetricsCredential.generate().serialize()
    )
    with pytest.raises(ValueError):
        credential_runtime.validate_metrics_candidate(b"plain-token")
    with pytest.raises(CredentialValidationUnavailable):
        credential_runtime.validation_unavailable(b"private-provider-value")


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
        client=SimpleNamespace(
            connection_pool=SimpleNamespace(disconnect=lambda: closes.append("valkey"))
        )
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
def test_acknowledgement_cli_never_echoes_private_input(arguments, capsys):
    """Configuration and request parser failures use fixed safe operator guidance."""
    assert main(["acknowledge-credential", *arguments]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
    assert "acknowledgement refused" in output.err


def test_acknowledgement_cli_success_requires_lifecycle_lease(
    tmp_path, monkeypatch, capsys
):
    """A shared real lease covers the whole admitted durable acknowledgement."""
    from parishkit.stewardship.runtime_paths import RuntimeLayout
    from parishkit.stewardship.startup_interlock import StartupLease

    configuration, _ = bootstrap_fixture(tmp_path)
    monkeypatch.setattr(
        credential_runtime, "load_deployment", lambda path: configuration
    )
    calls = []

    def acknowledge(config, identifier):
        """Migration remains excluded while this consumer confirmation is active."""
        with (
            pytest.raises(ConfigError),
            StartupLease(RuntimeLayout(config).interlock, offline=True),
        ):
            pass
        calls.append(identifier)

    monkeypatch.setattr(credential_runtime, "acknowledge_web", acknowledge)
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
    assert calls == [identifier]
    assert "acknowledgement recorded" in capsys.readouterr().out
