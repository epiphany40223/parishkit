"""Operator-only health output includes fixed booleans, never exception values."""

import json

import pytest

from parishkit.stewardship import runtime_diagnostics
from parishkit.stewardship.cli import main


@pytest.mark.parametrize("ready", [False, True])
def test_cli_reports_each_internal_check_and_correct_exit_code(
    ready, monkeypatch, capsys
):
    checks = {"database": True, "valkey": ready}
    marker = object()
    monkeypatch.setattr(runtime_diagnostics, "load_deployment", lambda path: marker)
    monkeypatch.setattr(runtime_diagnostics, "health_command", lambda config: checks)
    assert main(["health", "--config", "private-input.yaml"]) == (0 if ready else 1)
    result = capsys.readouterr()
    assert json.loads(result.out) == {"checks": checks, "ready": ready}
    assert result.err == ""


def test_cli_health_refuses_missing_config_and_private_errors(monkeypatch, capsys):
    """Host diagnosis cannot silently use different credentials or expose failures."""
    assert main(["health"]) == 2

    def fail(path):
        raise ValueError("private-error-value")

    monkeypatch.setattr(runtime_diagnostics, "load_deployment", fail)
    assert main(["health", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
