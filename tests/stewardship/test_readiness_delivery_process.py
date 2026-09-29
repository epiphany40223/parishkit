"""Finite private IPC for readiness sends, with no automatic resubmission."""

import base64
import json
import subprocess
import sys
import time
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


ORIGIN = "https://parish.example.org"
BANNER_URL = f"{ORIGIN}/branding/0f0e0d0c-0b0a-4908-8706-050403020100.png"


def banner_payload(origin):
    """A campaign sample with the server-built banner (#248) in a helper envelope."""
    from dataclasses import replace

    from parishkit.stewardship.web.content import email_banner

    banner = email_banner({"url": BANNER_URL, "width": 1024, "height": 217}, "Renewal")
    base = sample()
    mail = replace(base, html=banner + base.html, banner_origin=ORIGIN)
    request = {
        "settings": SETTINGS,
        "candidate": base64.b64encode(b"synthetic-private").decode(),
        "mail": mail.payload(),
    }
    if origin is not None:
        request["banner_origin"] = origin
    return mail, json.dumps(request).encode()


def test_parent_sends_its_banner_origin_and_helper_admits_the_banner(monkeypatch):
    """The helper validates a campaign banner against the parent's own origin."""
    mail, raw = banner_payload(ORIGIN)
    assert worker.decode_request(raw)[2] == mail
    process = Process(b"not_sent\n", 0)
    monkeypatch.setattr(parent.subprocess, "Popen", lambda *a, **k: process)
    parent.submit_sample(
        b"synthetic-private", SETTINGS, mail, seconds=30, check=lambda: None
    )
    assert json.loads(process.inputs[0])["banner_origin"] == ORIGIN


@pytest.mark.parametrize("origin", [None, "", "https://tracker.example.net"])
def test_helper_refuses_a_banner_without_the_matching_origin(origin):
    """No origin, or another host, means the banner image is not admitted."""
    _, raw = banner_payload(origin)
    with pytest.raises(ValueError):
        worker.decode_request(raw)


def test_deadline_stop_is_recorded_with_its_limit(monkeypatch):
    """A helper stopped at its deadline is logged (#293); its outcome stays unknown."""
    from parishkit.stewardship.audit import timeouts

    process = Process(returncode=None)

    def communicate(*, input, timeout):
        """Hold the pipe until the deadline passes, as a stuck provider would."""
        process.inputs.append(input)
        time.sleep(timeout)
        raise subprocess.TimeoutExpired("synthetic", timeout)

    process.communicate = communicate
    killed_first = []
    recorded = Mock(side_effect=lambda *a, **k: killed_first.append(process.killed))
    monkeypatch.setattr(timeouts, "record_timeout", recorded)
    monkeypatch.setattr(parent.subprocess, "Popen", lambda *args, **kwargs: process)
    assert invoke(seconds=0.3) is DeliveryOutcome.UNKNOWN
    # Logged just after the kill, naming which helper was stopped.
    assert process.killed and killed_first == [True]
    assert recorded.call_args.kwargs["what"] == "mail_helper"
    assert recorded.call_args.kwargs["helper"] == "readiness_delivery_worker"
    assert recorded.call_args.kwargs["limit_seconds"] == 0.3
    assert recorded.call_args.kwargs["elapsed_seconds"] >= 0.3
    recorded.reset_mock()
    monkeypatch.setattr(
        process, "communicate", Mock(side_effect=subprocess.TimeoutExpired("s", 1))
    )
    assert invoke() is DeliveryOutcome.UNKNOWN
    recorded.assert_not_called()


def gated_helper(monkeypatch, output=b"accepted\n"):
    """A helper whose reply waits until the lease check lets it finish.

    Returns ``(release, done)``: set ``release`` to let the helper answer, and
    ``done`` is set once it has.
    """
    import threading

    process = Process(output, 0)
    release, done = threading.Event(), threading.Event()

    def communicate(*, input, timeout):
        process.inputs.append(input)
        release.wait(5)
        done.set()
        return output, None

    process.communicate = communicate
    monkeypatch.setattr(parent.subprocess, "Popen", lambda *args, **kwargs: process)
    return release, done


def blocked_check(release, done, *, then=None, calls=2):
    """A lease check that, once the helper started, waits until it finished.

    The first ``calls`` checks (before the helper and at its start) pass. The
    next one lets the helper finish, then either waits past the deadline (a
    work-order lock held by a busy writer) or raises ``then``.
    """
    import time

    seen = []

    def check():
        seen.append(None)
        if len(seen) <= calls:
            return
        release.set()
        done.wait(5)
        if then is not None:
            raise then
        time.sleep(0.4)

    return check


def test_a_result_finished_during_a_slow_check_is_kept(monkeypatch):
    """A check blocked past the deadline no longer discards a finished send (#318).

    The helper finished, so the deadline did not stop it: no timeout is
    recorded either.
    """
    from parishkit.stewardship.audit import timeouts

    recorded = Mock()
    monkeypatch.setattr(timeouts, "record_timeout", recorded)
    release, done = gated_helper(monkeypatch)
    check = blocked_check(release, done)
    assert invoke(seconds=0.2, check=check) is DeliveryOutcome.ACCEPTED
    recorded.assert_not_called()


def test_a_result_finished_before_a_failed_check_is_kept(monkeypatch):
    """Ownership lost after the helper finished: its real result is returned.

    Discarding it would record "delivery unknown" for mail the provider told
    us it accepted. The caller's settlement still rechecks ownership under its
    locks, so a truly lost task records nothing and is recovered as usual.
    """
    release, done = gated_helper(monkeypatch)
    check = blocked_check(release, done, then=PermissionError("private"))
    assert invoke(check=check) is DeliveryOutcome.ACCEPTED


def test_ownership_lost_before_the_helper_finished_still_stops_it(monkeypatch):
    """Only a finished helper's result survives; an unfinished one is killed."""
    import threading

    process = Process(returncode=None)
    gate = threading.Event()

    def communicate(*, input, timeout):
        process.inputs.append(input)
        gate.wait(5)
        raise subprocess.TimeoutExpired("synthetic", timeout)

    def kill():
        """Killing the helper ends its pending reply, as a real kill would."""
        process.killed = True
        gate.set()

    process.communicate, process.kill = communicate, kill
    monkeypatch.setattr(parent.subprocess, "Popen", lambda *args, **kwargs: process)
    check = Mock(side_effect=[None, None, PermissionError("private")])
    with pytest.raises(ProviderCheckOwnershipLost):
        invoke(check=check)
    assert process.killed
