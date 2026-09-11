"""Offline CLI requires explicit intent and never reflects sensitive failures."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship import operator_commands
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole


@pytest.mark.parametrize("command", ["bootstrap", "migrate", "recover-admin"])
def test_offline_commands_require_config_and_refuse_unmounted_host(command, capsys):
    """A host invocation or configured role label alone grants no operator profile."""
    assert main([command]) == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert "offline operation refused" in result.err


def test_cli_forwards_only_explicit_bootstrap_parameters(monkeypatch, tmp_path, capsys):
    """The syntax boundary preserves typed intent without weakening kernel checks."""
    marker = object()
    monkeypatch.setattr(operator_commands, "load_deployment", lambda path: marker)
    calls = []

    def bootstrap(config, **parameters):
        """Isolate dispatch here; real offline mount and process tests are separate."""
        calls.append((config, parameters))
        return {"completed": True}

    monkeypatch.setattr(operator_commands, "bootstrap_command", bootstrap)
    assert (
        main(
            [
                "bootstrap",
                "--config",
                str(tmp_path / "input.yaml"),
                "--phase",
                "prepare",
                "--deployment-id",
                "00000000-0000-0000-0000-000000000001",
                "--admin-email",
                "admin@example.org",
            ]
        )
        == 0
    )
    assert calls == [
        (
            marker,
            {
                "phase": "prepare",
                "deployment_id": "00000000-0000-0000-0000-000000000001",
                "admin_email": "admin@example.org",
            },
        )
    ]
    assert "admin@example.org" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "error", [ValueError("private-password"), OSError("private-path")]
)
def test_operator_failures_do_not_print_exception_values(
    monkeypatch, tmp_path, capsys, error
):
    """Private-value sanitization includes errors raised before Django startup."""

    def fail(path):
        raise error

    monkeypatch.setattr(operator_commands, "load_deployment", fail)
    assert main(["migrate", "--config", str(tmp_path / "private-path")]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err


@pytest.mark.parametrize("value", [None, "private-token", True, "0" * 32])
def test_operator_confirmations_require_canonical_uuid(value):
    with pytest.raises(ValueError):
        operator_commands._uuid(value)


def test_bootstrap_cannot_run_as_an_online_service(monkeypatch):
    """Operator dispatch repeats the specific role check after kernel admission."""
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda config: ServiceRole.WEB
    )
    with pytest.raises(ValueError):
        operator_commands.bootstrap_command(
            SimpleNamespace(),
            phase="prepare",
            deployment_id="00000000-0000-0000-0000-000000000001",
            admin_email="admin@example.org",
        )
