"""Finite private IPC for readiness sends, with no automatic resubmission."""

import base64
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from parishkit.stewardship import readiness_delivery_process as parent
from parishkit.stewardship import readiness_delivery_worker as worker
from parishkit.stewardship.provider_checks import (
    ProviderCheckDrainFailure,
    ProviderCheckOwnershipLost,
)
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_provider_checks import Process
from .test_readiness_delivery import SETTINGS, sample


def payload():
    """Nothing in this synthetic request can authenticate with a real provider."""
    return json.dumps(
        {
            "settings": SETTINGS,
            "candidate": base64.b64encode(b"synthetic-private").decode(),
            "mail": sample().payload(),
        }
    ).encode()


def invoke(**overrides):
    """The production Task supplies the owning check; these tests isolate transport."""
    return parent.submit_sample(
        b"synthetic-private",
        SETTINGS,
        sample(),
        **({"seconds": 30, "check": lambda: None} | overrides),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"{}",
        b'{"candidate":1,"candidate":2}',
        b"x" * (worker.MAX_INPUT + 1),
    ],
)
def test_decoder_rejects_before_any_provider_call(monkeypatch, raw):
    """Malformed private inputs are definitely not submitted to any mail server."""
    adapter = Mock()
    monkeypatch.setattr(worker, "deliver_sample", adapter)
    assert worker.submit_request(raw) is DeliveryOutcome.NOT_SENT
    adapter.assert_not_called()


@pytest.mark.parametrize("defect", ["candidate_type", "empty", "large", "context"])
def test_invalid_candidate_and_message_scope_never_submit(monkeypatch, defect):
    """The helper repeats candidate bounds and the explicit single-recipient scope."""
    data = json.loads(payload())
    if defect == "candidate_type":
        data["candidate"] = 42
    elif defect == "empty":
        data["candidate"] = ""
    elif defect == "large":
        data["candidate"] = base64.b64encode(
            b"x" * (worker.MAX_FILE_BYTES + 1)
        ).decode()
    else:
        data["mail"]["recipient"] = "different@example.org"
    adapter = Mock()
    monkeypatch.setattr(worker, "deliver_sample", adapter)
    assert worker.submit_request(json.dumps(data).encode()) is DeliveryOutcome.NOT_SENT
    adapter.assert_not_called()


@pytest.mark.parametrize("result", [*DeliveryOutcome, "private garbage", None])
def test_worker_exposes_only_closed_outcomes(monkeypatch, result):
    """A malformed adapter result cannot leak back through stdout or become success."""
    adapter = Mock(return_value=result)
    monkeypatch.setattr(worker, "deliver_sample", adapter)
    assert worker.submit_request(payload()) is (
        result if isinstance(result, DeliveryOutcome) else DeliveryOutcome.UNKNOWN
    )
    assert adapter.call_args.args[0] == b"synthetic-private"


def test_unexpected_adapter_exception_is_private_and_unknown(monkeypatch):
    """Once the adapter is invoked, an uncaught exception is not proof of no send."""
    monkeypatch.setattr(
        worker, "deliver_sample", Mock(side_effect=RuntimeError("private"))
    )
    assert worker.submit_request(payload()) is DeliveryOutcome.UNKNOWN


@pytest.mark.parametrize(
    "output,code,expected",
    [
        (b"accepted\n", 0, DeliveryOutcome.ACCEPTED),
        (b"not_sent\n", 0, DeliveryOutcome.NOT_SENT),
        (b"delivery_unknown\n", 0, DeliveryOutcome.UNKNOWN),
        (b"private garbage\n", 0, DeliveryOutcome.UNKNOWN),
        (b"accepted\n", 1, DeliveryOutcome.UNKNOWN),
    ],
)
def test_parent_has_closed_process_arguments_and_outcomes(
    monkeypatch, output, code, expected
):
    """Candidates/messages travel only over private stdin, not argv or environment."""
    process = Process(output, code)
    factory = Mock(return_value=process)
    monkeypatch.setattr(parent.subprocess, "Popen", factory)
    assert invoke() is expected
    assert factory.call_args.args == (
        [sys.executable, "-I", "-m", "parishkit.stewardship.readiness_delivery_worker"],
    )
    assert factory.call_args.kwargs == {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": {},
    }
    assert worker.decode_request(process.inputs[0])[0] == b"synthetic-private"
    assert len(process.inputs) == 1 and process.stdin.closed and process.stdout.closed


def test_timeout_kills_and_reaps_without_retry(monkeypatch):
    """The missing final response must remain unknown, even after confirmed drainage."""
    process = Process(returncode=None)

    def communicate(*, input, timeout):
        """Simulate timeout after accepting the full private input on the pipe."""
        process.inputs.append(input)
        raise subprocess.TimeoutExpired("synthetic", timeout)

    process.communicate = communicate
    monkeypatch.setattr(parent.subprocess, "Popen", lambda *args, **kwargs: process)
    assert invoke() is DeliveryOutcome.UNKNOWN
    assert len(process.inputs) == 1 and process.killed and process.returncode == -9
    assert process.stdin.closed and process.stdout.closed


def test_launch_failure_proves_no_submitted_message(monkeypatch):
    """Only failure before any helper exists may be classified definitely unsent."""
    monkeypatch.setattr(
        parent.subprocess, "Popen", Mock(side_effect=OSError("private"))
    )
    assert invoke() is DeliveryOutcome.NOT_SENT


def test_ownership_loss_and_failed_drain_are_fatal(monkeypatch):
    """A lost worker cannot claim success or bypass confirmed child drainage."""
    factory = Mock()
    monkeypatch.setattr(parent.subprocess, "Popen", factory)
    with pytest.raises(ProviderCheckOwnershipLost):
        invoke(check=Mock(side_effect=PermissionError("private")))
    factory.assert_not_called()
    process = Process(returncode=None)
    process.wait = Mock(side_effect=subprocess.TimeoutExpired("synthetic", 5))
    factory.return_value = process
    with pytest.raises(ProviderCheckDrainFailure):
        invoke()
    assert process.killed and not process.stdin.closed


@pytest.mark.parametrize("seconds", [0, -1, 31, True, float("inf"), float("nan")])
def test_invalid_limits_cannot_start_a_helper(monkeypatch, seconds):
    """Absolute finite limits cannot be weakened through coercion or truthiness."""
    factory = Mock()
    monkeypatch.setattr(parent.subprocess, "Popen", factory)
    with pytest.raises(ValueError):
        invoke(seconds=seconds)
    factory.assert_not_called()


def test_real_helper_rejects_synthetic_key_without_network():
    """Execute the installed isolated entry point and inspect its literal protocol."""
    result = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.stewardship.readiness_delivery_worker"],
        input=payload(),
        capture_output=True,
        timeout=10,
        env={},
    )
    assert result.returncode == 0
    assert result.stdout == b"not_sent\n" and result.stderr == b""


def test_large_private_pipe_finishes_after_slow_child_start(monkeypatch):
    """The sole communicate call drains a payload larger than OS pipe capacity."""
    original = subprocess.Popen
    processes = []

    def launch(args, **options):
        """Replace only the helper with a bounded offline reader, not the transport."""
        assert args[-1] == "parishkit.stewardship.readiness_delivery_worker"
        process = original(
            [
                sys.executable,
                "-I",
                "-c",
                "import json,sys,time; time.sleep(0.3); request=json.load(sys.stdin); "
                "print('accepted' if len(request['mail']['text'])==100000 "
                "else 'not_sent')",
            ],
            **options,
        )
        processes.append(process)
        return process

    from dataclasses import replace

    monkeypatch.setattr(parent.subprocess, "Popen", launch)
    value = replace(sample(), text="x" * 100000)
    check = Mock()
    assert (
        parent.submit_sample(
            b"synthetic-private", SETTINGS, value, seconds=5, check=check
        )
        is DeliveryOutcome.ACCEPTED
    )
    assert processes[0].poll() == 0 and processes[0].stdin.closed
    assert check.call_count >= 3
