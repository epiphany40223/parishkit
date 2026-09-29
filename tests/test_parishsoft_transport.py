"""Finite isolated source reads using fake HTTP and disposable local processes."""

import io
import json
import subprocess
import sys
import threading
import time
from contextlib import nullcontext, suppress
from decimal import Decimal
from unittest.mock import Mock

import pytest

from parishkit import parishsoft_http_worker as helper
from parishkit import parishsoft_transport as transport
from parishkit.parishsoft import (
    DEFAULT_API_BASE_URL,
    ParishSoftClient,
    ParishSoftConfig,
)
from parishkit.parishsoft_transport import (
    BoundedSourceSession,
    ExactSourceResponse,
    InvalidSourceResponse,
    SourceTransportDrainFailure,
    SourceTransportError,
)
from parishkit.retry import RetryError, RetryPolicy


def request(**overrides):
    """Use only synthetic credentials; the real provider is never contacted."""
    return (
        dict(
            method="GET",
            url=DEFAULT_API_BASE_URL + "/families/change/list",
            parameters={"StartDate": "2026-09-10"},
            api_key="SYNTHETIC-PRIVATE-KEY",
            timeout=30,
        )
        | overrides
    )


def session(**overrides):
    """Default owning callbacks are explicit in this isolated transport fixture."""
    result = BoundedSourceSession(
        **dict(before_request=lambda seconds: None, check=lambda: None) | overrides
    )
    result.headers["x-api-key"] = "SYNTHETIC-PRIVATE-KEY"
    return result


@pytest.mark.parametrize(
    "path",
    [
        "families/change/list",
        "families/5",
        "families/5/member/list",
        "families/group/lookup/list",
        "families/workgroup/list",
        "families/workgroup/5/list",
        "members/workgroup/lookup/list",
        "members/workgroup/5/list",
        "members/5",
        "ministry/type/list",
        "ministry/5/minister/list",
        "offering/5/funds",
        "offering/pledge/list",
        "offering/contributiondetail/list",
    ],
)
def test_only_compiled_get_vocabulary_is_accepted(path):
    """The bounded transport supports the actual shared full/delta read paths."""
    assert helper.validate_request(request(url=DEFAULT_API_BASE_URL + "/" + path))


@pytest.mark.parametrize("path", sorted(helper.READ_POSTS))
def test_search_post_vocabulary_does_not_enable_provider_writes(path):
    """POST is a read only for the explicit known search endpoints."""
    assert helper.validate_request(
        request(method="POST", url=DEFAULT_API_BASE_URL + "/" + path)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "https://other.example/api/v2/families/search"},
        {"url": DEFAULT_API_BASE_URL + "/families/5?private=value"},
        {"url": DEFAULT_API_BASE_URL + "/families/../members/search"},
        {"method": "PUT"},
        {"method": "POST"},
        {"api_key": "private\nheader"},
        {"api_key": ""},
        {"timeout": True},
        {"timeout": 0},
        {"timeout": 241},
        {"timeout": float("nan")},
        {"parameters": []},
        {"extra": "private"},
    ],
)
def test_invalid_or_nonread_request_is_rejected_without_private_diagnostics(overrides):
    """No credential relocation, URL parameters, user hooks or broad HTTP verbs."""
    with pytest.raises(ValueError) as error:
        helper.validate_request(request(**overrides))
    assert "private" not in str(error.value)


class HTTPResponse:
    """Minimal context-managed response that records whether its body was read."""

    def __init__(self, status=200, chunks=(b"[]",)):
        """Store only synthetic test response input and iterator evidence."""
        self.status_code, self.chunks = status, chunks
        self.read = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        """Expose decoded chunks just as Requests' streaming iterator does."""
        assert chunk_size == 65536
        self.read = True
        yield from self.chunks


def fake_http(response):
    """Stand in for the helper's keep-alive Session at the network boundary."""
    instance = Mock(headers={})
    instance.request.return_value = response
    return instance


def test_helper_session_has_no_environment_authority():
    """The one keep-alive Session cannot inherit netrc/proxy settings or cookies."""
    with helper.new_session("SYNTHETIC-PRIVATE-KEY") as http:
        assert http.trust_env is False
        assert http.headers["x-api-key"] == "SYNTHETIC-PRIVATE-KEY"
        assert not http.cookies and http.hooks == {"response": []}


def test_helper_streams_with_closed_http_options():
    """Each request streams without redirects under its own socket timeout."""
    response = HTTPResponse(chunks=(b"[", b"]"))
    http = fake_http(response)
    assert helper.perform(http, request()) == (200, b"[]")
    assert http.request.call_args.kwargs == dict(
        timeout=30,
        stream=True,
        allow_redirects=False,
        params={"StartDate": "2026-09-10"},
    )
    assert response.closed


@pytest.mark.parametrize("status", [301, 302, 400, 401, 429, 500, 503])
def test_helper_never_reads_or_returns_provider_error_body(status):
    """Non-success status alone is sufficient for the shared retry/error policy."""
    response = HTTPResponse(status, chunks=(b"PRIVATE PROVIDER BODY",))
    assert helper.perform(fake_http(response), request()) == (status, b"")
    assert response.closed and not response.read


@pytest.mark.parametrize("chunks", [(b"123", b"4"), ()])
def test_helper_rejects_oversized_or_empty_decoded_body(monkeypatch, chunks):
    """Apply the bound after decompression, without trusting length headers."""
    response = HTTPResponse(chunks=chunks)
    monkeypatch.setattr(helper, "MAX_RESPONSE_BYTES", 3)
    with pytest.raises(ValueError):
        helper.perform(fake_http(response), request())
    assert response.closed


def test_session_fences_before_exchange_and_returns_lossless_json(monkeypatch):
    """The per-request payload carries no key; the helper got it once at start."""
    calls = []
    current = session(before_request=lambda seconds: calls.append(("fence", seconds)))

    def exchange(payload, **options):
        """Record the trusted transport boundary without starting any process."""
        calls.append(("exchange", options["seconds"]))
        assert options["helper"].key == "SYNTHETIC-PRIVATE-KEY"
        assert b"SYNTHETIC-PRIVATE-KEY" not in payload
        assert set(json.loads(payload)) == helper.REQUEST_FIELDS
        return b'200\n{"amount":123456789.0123456789}'

    monkeypatch.setattr(transport, "_exchange", exchange)
    response = current.get(request()["url"], timeout=30)
    assert calls == [("fence", 35), ("exchange", 30)]
    assert response.json() == {"amount": Decimal("123456789.0123456789")}
    assert response.request is None and response.cookies.get_dict() == {}
    assert "PRIVATE" not in repr(current) and "PRIVATE" not in repr(current._helper)
    current.close()
    assert not current.headers and current._helper.key is None


def test_shared_client_uses_bounded_session_without_changing_ordinary_defaults(
    monkeypatch, tmp_path
):
    """The injection path exercises the real shared uncached API/JSON handling."""
    monkeypatch.setattr(transport, "_exchange", lambda *args, **kwargs: b"200\n[]")
    source = ParishSoftClient(
        ParishSoftConfig(api_key="SYNTHETIC", cache_dir=tmp_path), session=session()
    )
    assert source.get_uncached("families/change/list") == []
    assert source.post_uncached("organizations/search") == []


@pytest.mark.parametrize(
    "content",
    [
        b'{"private":"value",}',
        b'{"private":1,"private":2}',
        b'{"amount":NaN}',
        b'{"amount":Infinity}',
        b'{"amount":-Infinity}',
        b'{"private":"\xff"}',
        b"[" * 2000,
    ],
)
def test_json_errors_do_not_include_input_fields_or_values(content):
    """Malformed data cannot leak through the shared parser's exception message."""
    response = ExactSourceResponse()
    response._content = content
    with pytest.raises(ValueError) as error:
        response.json()
    assert str(error.value) == "Source response contains invalid JSON."


def test_json_decoder_semantics_cannot_be_overridden():
    """A caller may not opt into lossy float decoding for this source response."""
    with pytest.raises(ValueError):
        ExactSourceResponse().json(parse_float=float)


@pytest.mark.parametrize("wire", [b"ERROR\n", b"99\n[]", b"200", b"999\n[]"])
def test_invalid_helper_frame_has_constant_diagnostics(monkeypatch, wire):
    """No stdout content becomes a status or exception argument."""
    monkeypatch.setattr(transport, "_exchange", lambda *args, **kwargs: wire)
    with pytest.raises(SourceTransportError, match="status is invalid"):
        session().get(request()["url"], timeout=30)


def test_preflight_denial_starts_no_helper(monkeypatch):
    """Provider I/O cannot begin if source claim admission or SQL close fails."""
    exchange = Mock()
    monkeypatch.setattr(transport, "_exchange", exchange)

    def deny(seconds):
        """Stand in for a lost durable source fence before any external call."""
        raise PermissionError("Source fence is no longer owned")

    with pytest.raises(PermissionError):
        session(before_request=deny).get(request()["url"], timeout=30)
    exchange.assert_not_called()


def test_invalid_or_large_request_starts_no_preflight_or_process(monkeypatch):
    """Validation precedes even lease reservation; invalid work has no effect."""
    preflight, exchange = Mock(), Mock()
    monkeypatch.setattr(transport, "_exchange", exchange)
    current = session(before_request=preflight)
    with pytest.raises(ValueError):
        current.get(request()["url"], params={"large": "x" * 65536}, timeout=30)
    with pytest.raises(ValueError):
        current.post(request()["url"], timeout=30)
    preflight.assert_not_called()
    exchange.assert_not_called()


# A stand-in helper that speaks the real pipe protocol without any network.
# Each request's "mode" parameter selects a behavior; success bodies report
# the serving PID, whether the start frame carried the key, and the process
# environment/argv so tests can prove the key never travels there.
FAKE_HELPER = r"""
import json, os, sys, time
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
key = json.loads(stdin.readline())["api_key"]
frames = {
    "garble": b"HELLO\n",
    "oversize": b"200 99999999\n",
    "extra": b"200 2\n[]X",
    "invalid": b"INVALID\n",
    "error": b"ERROR\n",
    "header": b"2" * 40,
    "error-body": b"404 3\nabc",
    "status": b"404 0\n",
}
for line in iter(stdin.readline, b""):
    mode = json.loads(line)["parameters"].get("mode", "ok")
    if mode == "sleep":
        time.sleep(60)
    if mode == "exit":
        os._exit(3)
    if mode == "stray":
        body = b"[]"
        stdout.write(b"200 2\n[]")
        stdout.flush()
        time.sleep(0.2)
        stdout.write(b"X")
        stdout.flush()
        continue
    if mode in frames:
        stdout.write(frames[mode])
        stdout.flush()
        continue
    body = json.dumps(
        {
            "pid": os.getpid(),
            "key": key == "SYNTHETIC-PRIVATE-KEY",
            "environ": dict(os.environ),
            "argv": sys.argv,
        }
    ).encode()
    stdout.write(b"200 %d\n" % len(body) + body)
    stdout.flush()
"""


@pytest.fixture
def launches(monkeypatch, tmp_path):
    """Run the fake helper in place of the installed worker, recording each launch.

    The recorded argv/options are exactly what the transport asked Popen for,
    so tests assert the real isolation contract; only the module is swapped.
    """
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE_HELPER)
    original = subprocess.Popen
    started = []

    def launch(args, **options):
        """Record the requested command, then start the synthetic helper."""
        assert args == [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"]
        assert options == dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={},
        )
        process = original([sys.executable, "-I", str(script)], **options)
        started.append(process)
        return process

    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    yield started
    for process in started:
        with suppress(ProcessLookupError):
            process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()


def fetch(current, mode="ok", timeout=30):
    """Issue one real read through the session and the fake helper process."""
    return current.get(request()["url"], params={"mode": mode}, timeout=timeout)


def test_requests_reuse_one_helper_and_key_travels_only_over_stdin(launches):
    """Startup and connections are amortized; argv/env never carry the key."""
    current = session()
    first, second = fetch(current).json(), fetch(current).json()
    assert len(launches) == 1
    assert first["pid"] == second["pid"] == launches[0].pid
    assert first["key"] is True
    assert "SYNTHETIC-PRIVATE-KEY" not in json.dumps([first["environ"], first["argv"]])
    current.close()
    assert launches[0].poll() is not None
    assert launches[0].stdin.closed and launches[0].stdout.closed
    assert not current.headers and current._helper.process is None


def test_non_success_status_keeps_the_helper_for_the_next_request(launches):
    """A provider 4xx/5xx is a valid reply, not a reason to restart the helper."""
    current = session()
    assert fetch(current, "status").status_code == 404
    assert fetch(current).json()["pid"] == launches[0].pid
    current.close()


def test_deadline_kills_and_reaps_the_helper_then_replaces_it(launches):
    """A stuck read cannot outlive its hard deadline; the next read starts fresh."""
    current = session()
    fetch(current)
    beginning = time.monotonic()
    with pytest.raises(SourceTransportError, match="deadline"):
        fetch(current, "sleep", timeout=0.3)
    assert time.monotonic() - beginning < 5
    assert launches[0].poll() is not None and launches[0].stdout.closed
    assert fetch(current).json()["pid"] == launches[1].pid != launches[0].pid
    current.close()


def test_ownership_loss_during_wait_kills_the_helper(launches):
    """Polling gives lost ownership and shutdown a finite cancellation point."""
    lost = []

    def check():
        """Report lost ownership once the test flips the flag."""
        if lost:
            raise PermissionError("Lost source ownership")

    current = session(check=check)
    fetch(current)
    threading.Timer(0.3, lost.append, args=(True,)).start()
    with pytest.raises(PermissionError):
        fetch(current, "sleep")
    assert launches[0].poll() is not None
    lost.clear()
    assert fetch(current).json()["pid"] == launches[1].pid
    current.close()


def test_helper_death_mid_request_fails_and_the_next_request_restarts(launches):
    """EOF before a complete reply is a retryable outage on a fresh helper."""
    current = session()
    with pytest.raises(SourceTransportError, match="valid response"):
        fetch(current, "exit")
    assert launches[0].poll() == 3
    assert fetch(current).json()["pid"] == launches[1].pid
    current.close()


def test_idle_helper_death_is_detected_before_reuse(launches):
    """A helper that died between requests is reaped and transparently replaced."""
    current = session()
    fetch(current)
    launches[0].kill()
    launches[0].wait()
    assert fetch(current).json()["pid"] == launches[1].pid
    current.close()


def test_stray_bytes_after_a_reply_force_a_fresh_helper(launches):
    """Unsolicited output can never be mistaken for the next request's reply."""
    current = session()
    assert fetch(current, "stray").json() == []
    time.sleep(0.5)
    assert fetch(current).json()["pid"] == launches[1].pid
    assert launches[0].poll() is not None
    current.close()


@pytest.mark.parametrize(
    ("mode", "error", "message"),
    [
        ("garble", SourceTransportError, "valid response"),
        ("error", SourceTransportError, "valid response"),
        ("header", SourceTransportError, "frame is invalid"),
        ("extra", SourceTransportError, "frame is invalid"),
        ("error-body", SourceTransportError, "frame is invalid"),
        ("oversize", InvalidSourceResponse, "body contract"),
        ("invalid", InvalidSourceResponse, "body contract"),
    ],
)
def test_bad_or_oversized_frames_fail_closed_and_kill_the_helper(
    launches, mode, error, message
):
    """Every protocol violation stops the helper; nothing is reused or retried."""
    current = session()
    with pytest.raises(error, match=message):
        fetch(current, mode)
    assert launches[0].poll() is not None and current._helper.process is None
    current.close()


def test_changed_key_starts_a_new_helper(launches):
    """A helper only ever serves the key it was started with."""
    current = session()
    fetch(current)
    current.headers["x-api-key"] = "OTHER-SYNTHETIC-KEY"
    assert fetch(current).json()["key"] is False
    assert len(launches) == 2 and launches[0].poll() is not None
    current.close()


def test_unavailable_process_is_a_retryable_transport_error(monkeypatch):
    """An OS refusal to start the helper is a connection-class failure."""
    monkeypatch.setattr(transport.subprocess, "Popen", Mock(side_effect=OSError))
    with pytest.raises(SourceTransportError, match="unavailable"):
        fetch(session())


def test_real_helper_rejects_invalid_input_without_network_or_traceback():
    """Installed-module startup and constant malformed-input protocol are executable."""
    process = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"],
        input=b'{"private":"NEVER-DISCLOSE"}\n',
        capture_output=True,
        timeout=10,
        env={},
    )
    assert process.returncode == 1
    assert process.stdout == b"ERROR\n" and process.stderr == b""


@pytest.mark.parametrize(
    "frame",
    [
        request(),  # A per-request key would bypass the start-frame binding.
        {k: v for k, v in request(method="PUT").items() if k != "api_key"},
    ],
)
def test_real_helper_validates_each_request_before_any_network(frame):
    """Requests are rejected before any Session I/O, with a constant reply."""
    key = json.dumps({"api_key": "SYNTHETIC-PRIVATE-KEY"}).encode() + b"\n"
    process = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"],
        input=key + json.dumps(frame).encode() + b"\n",
        capture_output=True,
        timeout=10,
        env={},
    )
    assert process.returncode == 1
    assert process.stdout == b"ERROR\n" and process.stderr == b""


def test_real_helper_exits_quietly_when_stdin_closes():
    """An idle helper whose parent closed the pipe exits instead of lingering."""
    process = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"],
        input=json.dumps({"api_key": "SYNTHETIC-PRIVATE-KEY"}).encode() + b"\n",
        capture_output=True,
        timeout=10,
        env={},
    )
    assert process.returncode == 0 and process.stdout == b""


def test_helper_watchdog_exits_when_its_parent_disappears(monkeypatch):
    """A re-parented helper stops even while blocked in provider I/O."""
    monkeypatch.setattr(helper.os, "getppid", lambda: 1)
    monkeypatch.setattr(helper.os, "_exit", Mock(side_effect=SystemExit))
    with pytest.raises(SystemExit):
        helper.watch_parent(12345, interval=0)
    helper.os._exit.assert_called_once_with(1)


def test_unknown_process_drain_is_fatal_not_a_retryable_read_failure():
    """Do not launch another read when the previous helper's death is unconfirmed."""
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("safe-command", 5)
    with pytest.raises(SourceTransportDrainFailure):
        transport._stop(process)


def test_shared_client_retries_drained_transport_with_fresh_preflight(
    tmp_path, monkeypatch
):
    """The retry helper must reserve/fence each attempt, not only the initial call."""
    reservations = []
    exchange = Mock(
        side_effect=[
            SourceTransportError("Source transport unavailable."),
            SourceTransportError("Source transport unavailable."),
            b"200\n[]",
        ]
    )
    monkeypatch.setattr(transport, "_exchange", exchange)
    source = ParishSoftClient(
        ParishSoftConfig("SYNTHETIC", tmp_path / "unused", cache_enabled=False),
        session=session(before_request=reservations.append),
        retry_policy=RetryPolicy(attempts=3, initial_delay=0),
    )
    assert source.get("families/change/list") == []
    assert reservations == [35, 35, 35] and exchange.call_count == 3


def test_shared_transport_retries_have_a_finite_exhaustion_bound(tmp_path, monkeypatch):
    """Exhaustion returns the shared typed error for the owning Task retry policy."""
    exchange = Mock(side_effect=SourceTransportError("Source transport unavailable."))
    monkeypatch.setattr(transport, "_exchange", exchange)
    source = ParishSoftClient(
        ParishSoftConfig("SYNTHETIC", tmp_path / "unused", cache_enabled=False),
        session=session(),
        retry_policy=RetryPolicy(attempts=3, initial_delay=0),
    )
    with pytest.raises(RetryError) as caught:
        source.get("families/change/list")
    assert isinstance(caught.value.last_exception, SourceTransportError)
    assert exchange.call_count == 3


@pytest.mark.parametrize(
    "error", [PermissionError, SourceTransportDrainFailure, InvalidSourceResponse]
)
def test_shared_retry_never_retries_admission_loss_or_unknown_drain(
    tmp_path, monkeypatch, error
):
    """Neither fenced access loss nor an undrained process is a connection retry."""
    exchange = Mock(side_effect=error("Source ownership is unavailable."))
    monkeypatch.setattr(transport, "_exchange", exchange)
    source = ParishSoftClient(
        ParishSoftConfig("SYNTHETIC", tmp_path / "unused", cache_enabled=False),
        session=session(),
        retry_policy=RetryPolicy(attempts=3, initial_delay=0),
    )
    with pytest.raises(error):
        source.get("families/change/list")
    assert exchange.call_count == 1


def test_uncached_client_never_creates_or_uses_private_cache_files(
    monkeypatch, tmp_path
):
    """A coherent refresh with Decimal data has neither stale reads nor disk PII."""
    exchange = Mock(return_value=b'200\n{"amount":0.1234567890123456789}')
    monkeypatch.setattr(transport, "_exchange", exchange)
    cache = tmp_path / "must-not-exist"
    source = ParishSoftClient(
        ParishSoftConfig(api_key="SYNTHETIC", cache_dir=cache, cache_enabled=False),
        session=session(),
    )
    for _ in range(2):
        assert source.get("offering/pledge/list") == {
            "amount": Decimal("0.1234567890123456789")
        }
    assert exchange.call_count == 2
    assert not cache.exists()


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_cache_flag_requires_exact_boolean(tmp_path, value):
    """Callers cannot silently enable caching by passing a truthy text option."""
    from parishkit.config import ConfigError

    with pytest.raises(ConfigError):
        ParishSoftConfig(api_key="SYNTHETIC", cache_dir=tmp_path, cache_enabled=value)


def test_empty_success_frame_cannot_become_a_valid_empty_collection(monkeypatch):
    """Shared legacy empty-body handling does not weaken coherent-source reads."""
    monkeypatch.setattr(transport, "_exchange", lambda *args, **kwargs: b"200\n")
    with pytest.raises(InvalidSourceResponse, match="no JSON body"):
        session().get(request()["url"], timeout=30)


def serve(monkeypatch, *frames, perform):
    """Run the real helper loop on in-memory pipes with a fake keep-alive Session."""
    sessions = []

    def new_session(key):
        """Record each Session; the loop must create exactly one."""
        sessions.append(key)
        return nullcontext(Mock())

    monkeypatch.setattr(helper, "new_session", new_session)
    monkeypatch.setattr(helper, "perform", perform)
    lines = [{"api_key": "SYNTHETIC-PRIVATE-KEY"}, *frames]
    stdin = io.BytesIO(b"".join(json.dumps(line).encode() + b"\n" for line in lines))
    output = io.BytesIO()
    return helper.serve(stdin, output), output.getvalue(), sessions


def wire(**overrides):
    """A key-free per-request frame as the parent sends it."""
    return {k: v for k, v in request(**overrides).items() if k != "api_key"}


def test_helper_serves_many_requests_on_one_session(monkeypatch):
    """One Session (and its connections) serves every request until EOF."""
    replies = iter([(200, b"[1]"), (404, b""), (200, b"[]")])
    perform = Mock(side_effect=lambda session, value: next(replies))
    status, output, sessions = serve(
        monkeypatch, wire(), wire(), wire(), perform=perform
    )
    assert status == 0 and sessions == ["SYNTHETIC-PRIVATE-KEY"]
    assert output == b"200 3\n[1]404 0\n200 2\n[]"
    assert perform.call_args.args[1]["api_key"] == "SYNTHETIC-PRIVATE-KEY"


def test_helper_reports_deterministic_body_failure_without_private_text(monkeypatch):
    """Malformed bodies have a distinct closed IPC outcome, then the helper exits."""
    perform = Mock(side_effect=InvalidSourceResponse("PRIVATE"))
    status, output, _ = serve(monkeypatch, wire(), wire(), perform=perform)
    assert (status, output) == (2, b"INVALID\n")
    perform.assert_called_once()


def test_helper_rejects_oversized_request_frame(monkeypatch):
    """A request line longer than the bound is refused, never partially parsed."""
    perform = Mock()
    status, output, _ = serve(
        monkeypatch, wire(parameters={"large": "x" * 70000}), perform=perform
    )
    assert (status, output) == (1, b"ERROR\n")
    perform.assert_not_called()


def test_deadline_stop_is_reported_to_the_owner(launches):
    """The owner learns the limit and elapsed time once the helper is killed."""
    seen = []

    def observe(limit, elapsed):
        """Record the report and whether the helper was still running (it is not)."""
        seen.append((limit, elapsed, launches[0].poll() is None))

    current = session(on_timeout=observe)
    with pytest.raises(SourceTransportError, match="deadline"):
        fetch(current, "sleep", timeout=0.3)
    assert launches[0].poll() is not None
    # Reported after the kill, with the limit and elapsed time.
    assert len(seen) == 1 and seen[0][0] == 0.3 and 0.3 <= seen[0][1] < 5
    assert seen[0][2] is False
    fetch(current)
    assert len(seen) == 1
    current.close()
    with pytest.raises(TypeError):
        session(on_timeout="not callable")
