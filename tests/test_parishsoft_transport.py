"""Finite isolated source reads using fake HTTP and disposable local processes."""

import io
import json
import subprocess
import sys
import time
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


def fake_http(monkeypatch, response):
    """Replace network at the child boundary, keeping real worker validation."""
    instance = Mock(headers={})
    instance.request.return_value = response
    context = Mock()
    context.__enter__ = Mock(return_value=instance)
    context.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(helper.requests, "Session", lambda: context)
    return instance


def test_helper_streams_with_closed_http_options_and_no_environment_authority(
    monkeypatch,
):
    """A fresh Session cannot inherit netrc/proxy settings or follow a redirect."""
    response = HTTPResponse(chunks=(b"[", b"]"))
    http = fake_http(monkeypatch, response)
    assert helper.perform(request()) == (200, b"[]")
    assert http.trust_env is False
    assert http.headers == {"x-api-key": "SYNTHETIC-PRIVATE-KEY"}
    assert http.request.call_args.kwargs == dict(
        timeout=30,
        stream=True,
        allow_redirects=False,
        params={"StartDate": "2026-09-10"},
    )
    assert response.closed


@pytest.mark.parametrize("status", [301, 302, 400, 401, 429, 500, 503])
def test_helper_never_reads_or_returns_provider_error_body(monkeypatch, status):
    """Non-success status alone is sufficient for the shared retry/error policy."""
    response = HTTPResponse(status, chunks=(b"PRIVATE PROVIDER BODY",))
    fake_http(monkeypatch, response)
    assert helper.perform(request()) == (status, b"")
    assert response.closed and not response.read


@pytest.mark.parametrize("chunks", [(b"123", b"4"), ()])
def test_helper_rejects_oversized_or_empty_decoded_body(monkeypatch, chunks):
    """Apply the bound after decompression, without trusting length headers."""
    response = HTTPResponse(chunks=chunks)
    fake_http(monkeypatch, response)
    monkeypatch.setattr(helper, "MAX_RESPONSE_BYTES", 3)
    with pytest.raises(ValueError):
        helper.perform(request())
    assert response.closed


def test_session_fences_before_exchange_and_returns_lossless_json(monkeypatch):
    """Private material travels only in the bounded pipe payload, not diagnostics."""
    calls = []
    current = session(before_request=lambda seconds: calls.append(("fence", seconds)))

    def exchange(payload, **options):
        """Record the trusted transport boundary without starting any process."""
        calls.append(("exchange", options["seconds"]))
        assert json.loads(payload)["api_key"] == "SYNTHETIC-PRIVATE-KEY"
        return b'200\n{"amount":123456789.0123456789}'

    monkeypatch.setattr(transport, "_exchange", exchange)
    response = current.get(request()["url"], timeout=30)
    assert calls == [("fence", 35), ("exchange", 30)]
    assert response.json() == {"amount": Decimal("123456789.0123456789")}
    assert response.request is None and response.cookies.get_dict() == {}
    assert "PRIVATE" not in repr(current)
    current.close()
    assert not current.headers


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


def test_exchange_uses_private_pipes_and_isolated_installed_python(monkeypatch):
    """The actual process call has neither secrets in argv/env nor inherited SQL FDs."""
    process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=0)
    process.communicate.return_value = (b"200\n[]", None)
    process.poll.return_value = 0
    factory = Mock(return_value=process)
    monkeypatch.setattr(transport.subprocess, "Popen", factory)
    assert transport._exchange(b"PRIVATE", seconds=30, check=lambda: None) == b"200\n[]"
    assert factory.call_args.args == (
        [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"],
    )
    assert factory.call_args.kwargs == dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env={},
    )
    assert process.communicate.call_args.kwargs["input"] == b"PRIVATE"
    assert process.stdin.closed and process.stdout.closed


def test_real_helper_rejects_invalid_input_without_network_or_traceback():
    """Installed-module startup and constant malformed-input protocol are executable."""
    process = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.parishsoft_http_worker"],
        input=b'{"private":"NEVER-DISCLOSE"}',
        capture_output=True,
        timeout=10,
        env={},
    )
    assert process.returncode == 1
    assert process.stdout == b"ERROR\n" and process.stderr == b""


def test_real_process_deadline_kills_and_reaps_only_the_read_helper(monkeypatch):
    """A stuck process cannot outlive the reserved read/drain interval."""
    original = subprocess.Popen
    started = []

    def fake_provider(args, **options):
        """Substitute a local sleeping interpreter, never an external server."""
        process = original(
            [sys.executable, "-I", "-c", "import time; time.sleep(20)"], **options
        )
        started.append(process)
        return process

    monkeypatch.setattr(transport.subprocess, "Popen", fake_provider)
    beginning = time.monotonic()
    with pytest.raises(SourceTransportError, match="deadline"):
        transport._exchange(b"PRIVATE", seconds=0.1, check=lambda: None)
    assert time.monotonic() - beginning < 5
    assert started[0].poll() is not None
    assert started[0].stdin.closed and started[0].stdout.closed


def test_ownership_loss_during_wait_kills_helper_without_consuming_result(monkeypatch):
    """Timeout polling gives ownership loss and SIGTERM a finite cancellation point."""
    process = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=None)
    process.communicate.side_effect = subprocess.TimeoutExpired("safe-command", 0.25)
    process.poll.return_value = None
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: process)
    checks = 0

    def check():
        """Only the first wait remains admitted; the next observes fence loss."""
        nonlocal checks
        checks += 1
        if checks > 1:
            raise PermissionError("Lost source ownership")

    with pytest.raises(PermissionError):
        transport._exchange(b"PRIVATE", seconds=30, check=check)
    process.kill.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)


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


@pytest.mark.parametrize("error", [PermissionError, SourceTransportDrainFailure])
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
    with pytest.raises(SourceTransportError, match="no JSON body"):
        session().get(request()["url"], timeout=30)
