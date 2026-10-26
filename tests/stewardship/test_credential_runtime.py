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


class _Rows:
    """The awaiting requests a consumer's pass reads, without a database."""

    def __init__(self, rows):
        self.rows = rows

    def filter(self, **kwargs):
        """Every filter returns the same fixed rows."""
        return self

    def exclude(self, **kwargs):
        """Already-acknowledged requests are left out by the fixture itself."""
        return self

    def values(self, *args):
        """The excluded subquery's values."""
        return self

    def __iter__(self):
        return iter(self.rows)


@pytest.fixture
def pickup(tmp_path, monkeypatch):
    """A worker whose ParishSoft folder is mounted, with its pass's SQL faked."""
    from datetime import UTC, datetime, timedelta

    from parishkit.stewardship.accounts import (
        credential_installation,
        secret_models,
    )

    from .test_runtime_topology import configuration_at

    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration,
        service_role=ServiceRole.WORKER,
        secrets={
            "parishsoft": configuration.paths["credentials"]
            / "parishsoft"
            / "credential",
            "token_public": tmp_path / "token_public",
        },
    )
    folder = configuration.secrets["parishsoft"].parent
    for path in (*reversed(folder.parents[:2]), folder):
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)
    now = datetime(2054, 10, 1, 12, tzinfo=UTC)
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.sessions.database_now", lambda: now
    )
    rows = []
    monkeypatch.setattr(
        secret_models.SecretReplacementRequest, "objects", _Rows(rows), raising=False
    )
    monkeypatch.setattr(
        secret_models.CredentialConsumerAcknowledgement,
        "objects",
        _Rows([]),
        raising=False,
    )
    acks, published, alerts = [], [], []
    monkeypatch.setattr(
        credential_installation,
        "acknowledge_loaded_credential",
        lambda **kwargs: acks.append(kwargs),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        lambda configuration, receipts, deferred: published.append(
            (dict(receipts), deferred)
        ),
    )
    monkeypatch.setattr(
        credential_runtime, "emit", lambda event, **kwargs: alerts.append(kwargs)
    )
    monkeypatch.setattr(credential_runtime, "_overdue_alerted", {})

    def request(value, *, minutes):
        """An awaiting request for ``value``, installed ``minutes`` ago."""
        from parishkit.stewardship.accounts.metrics_credentials import (
            credential_receipt,
        )

        row = SimpleNamespace(
            pk=uuid4(),
            target="parishsoft",
            resulting_fingerprint=credential_receipt(value, "parishsoft"),
            updated_at=now - timedelta(minutes=minutes),
        )
        rows.append(row)
        return row

    return SimpleNamespace(
        configuration=configuration,
        request=request,
        acks=acks,
        published=published,
        alerts=alerts,
        rows=rows,
    )


def _install(configuration, value):
    """Rename ``value`` into the mounted folder as the installer would."""
    from parishkit.stewardship.accounts.key_files import write_private

    write_private(configuration.secrets["parishsoft"], value)


def test_setup_key_is_acknowledged_from_the_mount_with_its_deferral(pickup):
    """#2228: setup's first key appears in the mounted folder; the consumer
    acknowledges exactly those bytes through its own hook and republishes
    its receipts with the deferral it was admitted with."""
    row = pickup.request(b"setup-key", minutes=1)
    _install(pickup.configuration, b"setup-key")
    receipts = {}
    deferred = frozenset({"parishsoft"})
    acknowledged = credential_runtime.acknowledge_rotations(
        pickup.configuration, receipts, deferred
    )
    assert acknowledged == [row.pk]
    assert pickup.acks == [
        {"request_id": row.pk, "consumer": "worker", "loaded_value": b"setup-key"}
    ]
    assert pickup.published == [({"parishsoft": row.resulting_fingerprint}, deferred)]
    assert pickup.alerts == []


@pytest.mark.parametrize("minutes,logged", [(9, False), (10, True)])
def test_a_key_not_yet_visible_is_logged_only_once_overdue(pickup, minutes, logged):
    """No acknowledgement without the installed bytes; after 10 minutes the
    pass logs credential_pickup_overdue, at most hourly per request."""
    from parishkit.stewardship.observability import FailureKind

    pickup.request(b"setup-key", minutes=minutes)
    for _ in range(2):
        assert credential_runtime.acknowledge_rotations(pickup.configuration, {}) == []
    assert pickup.acks == [] and pickup.published == []
    assert pickup.alerts == (
        [
            {
                "level": 40,
                "failure_kind": FailureKind.CREDENTIAL_PICKUP_OVERDUE,
            }
        ]
        if logged
        else []
    )


def test_bytes_that_would_fail_readmission_are_never_acknowledged(pickup):
    """The consumer parses the key as its next start will (#2228): an
    installed ParishSoft file that SourceCredential refuses is not
    acknowledged, and the pass goes on to the next request."""
    bad = pickup.request(b"two\nlines\n", minutes=11)
    _install(pickup.configuration, b"two\nlines\n")
    assert credential_runtime.acknowledge_rotations(pickup.configuration, {}) == []
    assert pickup.acks == []
    assert len(pickup.alerts) == 1
    # A later request for parseable bytes still goes through in the same pass.
    pickup.rows.remove(bad)
    good = pickup.request(b"setup-key", minutes=11)
    pickup.rows.insert(0, bad)
    _install(pickup.configuration, b"setup-key")
    assert credential_runtime.acknowledge_rotations(pickup.configuration, {}) == [
        good.pk
    ]


def test_a_refused_acknowledgement_is_logged_and_the_pass_goes_on(pickup, monkeypatch):
    """A guard refusal (an expired request, say) is per request (#2228)."""
    from parishkit.stewardship.accounts import credential_installation

    first = pickup.request(b"setup-key", minutes=1)
    second = pickup.request(b"setup-key", minutes=1)
    _install(pickup.configuration, b"setup-key")

    def acknowledge(**kwargs):
        """The SQL guard refuses the first request only."""
        if kwargs["request_id"] == first.pk:
            raise ConfigError("synthetic refusal")
        pickup.acks.append(kwargs)

    failures = Mock()
    monkeypatch.setattr(
        credential_installation, "acknowledge_loaded_credential", acknowledge
    )
    monkeypatch.setattr(credential_runtime, "emit_failure", failures)
    assert credential_runtime.acknowledge_rotations(pickup.configuration, {}) == [
        second.pk
    ]
    failures.assert_called_once()


def test_a_rolled_back_setup_key_leaves_the_receipts(pickup):
    """Cancellation removes the installed file; the process no longer claims
    it, while a required credential's receipt is never dropped."""
    deferred = frozenset({"parishsoft"})
    receipts = {"parishsoft": "e" * 64, "token_public": "f" * 64}
    assert (
        credential_runtime.acknowledge_rotations(
            pickup.configuration, receipts, deferred
        )
        == []
    )
    assert receipts == {"token_public": "f" * 64}
    assert pickup.published == [({"token_public": "f" * 64}, deferred)]
    # Without a deferral ParishSoft is required: its receipt stays.
    receipts = {"parishsoft": "e" * 64, "token_public": "f" * 64}
    pickup.published.clear()
    credential_runtime.acknowledge_rotations(pickup.configuration, receipts)
    assert set(receipts) == {"parishsoft", "token_public"}
    assert pickup.published == []
