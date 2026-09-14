"""The full-suite Compose deadline retains bounded diagnostics and cleanup."""

import subprocess
import sys
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from . import test_compose as compose_tests


@pytest.mark.parametrize(
    "kind,cleanup_timeout",
    [("bytes", False), ("text", False), ("empty", False), ("bytes", True)],
)
def test_baseline_timeout_preserves_progress_and_disposable_cleanup(
    tmp_path, monkeypatch, kind, cleanup_timeout
):
    """Inject a timeout without invoking Docker, providers or retained storage."""
    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    monkeypatch.setattr(compose_tests, "ROOT", checkout)
    monkeypatch.delenv("PARISHKIT_COMPOSE_NATIVE_POSTGRES", raising=False)
    output = "discarded-old-line\n" * 100 + "last-running-test\n"
    if kind == "bytes":
        output = output.encode() + b"invalid-utf8-\xff"
    elif kind == "empty":
        output = None

    def run(command, **kwargs):
        """Fail only full-suite execution and allow the existing cleanup command."""
        if command[0] == sys.executable:
            assert kwargs["timeout"] == 120
            return subprocess.CompletedProcess(command, 0, stdout="unused", stderr="")
        if "down" in command:
            assert kwargs["timeout"] == 60
            if cleanup_timeout:
                raise subprocess.TimeoutExpired(command, 60, output=b"cleanup")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        assert command[0:2] == ["docker", "compose"]
        assert "--collection-manifest" in command and "--ci-progress" in command
        assert kwargs["timeout"] == 300
        raise subprocess.TimeoutExpired(command, 300, output=output)

    runner = Mock(side_effect=run)
    monkeypatch.setattr(compose_tests.subprocess, "run", runner)
    warning = (
        pytest.warns(UserWarning, match="Disposable Compose fixture cleanup failed")
        if cleanup_timeout
        else nullcontext()
    )
    with (
        warning,
        pytest.raises(pytest.fail.Exception, match="exceeded 300s") as failure,
    ):
        compose_tests.test_development_container_lifecycle(tmp_path)
    assert len(str(failure.value)) < 8500
    if kind != "empty":
        assert "last-running-test" in str(failure.value)
        assert str(failure.value).count("discarded-old-line") < 40
    if kind == "bytes":
        assert "invalid-utf8-\ufffd" in str(failure.value)
    assert runner.call_count == 3
    commands = [call.args[0] for call in runner.call_args_list]
    assert "down" in commands[-1]
    project = commands[1][commands[1].index("-p") + 1]
    assert project.startswith("pk-stewardship-smoke-")
    assert commands[2][commands[2].index("-p") + 1] == project


def test_liveness_timeout_still_retries_through_actual_compose_helper(
    tmp_path, monkeypatch
):
    """Do not translate the probe's native timeout into an immediate pytest failure."""
    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    monkeypatch.setattr(compose_tests, "ROOT", checkout)
    monkeypatch.delenv("PARISHKIT_COMPOSE_NATIVE_POSTGRES", raising=False)
    monkeypatch.setattr(compose_tests.time, "sleep", lambda _: None)
    probes = []

    class StopAfterLiveness(Exception):
        """Stop before any HTTP or persistence work in this isolated unit test."""

    monkeypatch.setattr(compose_tests, "wait_http", Mock(side_effect=StopAfterLiveness))

    def run(command, **kwargs):
        """Exercise the real nested helper and live-probe retry loop with fake I/O."""
        output = ""
        if command[0] == sys.executable or "--collection-manifest" in command:
            output = 'PARISHKIT_TEST_NODEIDS=["synthetic"]\n'
        elif "id" in command:
            output = "10001\n"
        elif "port" in command:
            output = "127.0.0.1:1234\n"
        elif "-c" in command:
            probes.append(command)
            assert kwargs["timeout"] == 5
            if len(probes) == 1:
                raise subprocess.TimeoutExpired(command, 5)
            output = "ok\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    runner = Mock(side_effect=run)
    monkeypatch.setattr(compose_tests.subprocess, "run", runner)
    with pytest.raises(StopAfterLiveness):
        compose_tests.test_development_container_lifecycle(tmp_path)
    assert len(probes) == 2
    assert "down" in runner.call_args.args[0]
