"""Offline Slack readiness transport, fixed routing and conservative outcomes."""

import base64
import json
import subprocess
import sys
from unittest.mock import Mock
from uuid import UUID

import pytest
import requests

from parishkit.config import ConfigError
from parishkit.stewardship import readiness_delivery_process as transport
from parishkit.stewardship import readiness_notification as adapter
from parishkit.stewardship import readiness_notification_process as parent
from parishkit.stewardship import readiness_notification_worker as worker
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_provider_checks import Process


def notification():
    """Use stable fictional correlation and channel identifiers."""
    return adapter.ReadinessNotification(
        UUID("12345678-1234-1234-1234-123456789abc"), "CFIXTURE"
    )


def payload():
    """No private fixture value can authenticate with the real Slack service."""
    return json.dumps(
        {
            "candidate": base64.b64encode(b"synthetic-token").decode("ascii"),
            "notification": notification().payload(),
        }
    ).encode()


@pytest.fixture
def http(monkeypatch):
    """Replace the entire HTTP session before any private adapter is invoked."""
    session, response = Mock(), Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session.post.return_value = response
    response.status_code = 200
    factory = Mock(return_value=session)
    monkeypatch.setattr(adapter.requests, "Session", factory)
    return factory, session, response


def test_fixed_message_has_no_arbitrary_text_mentions_or_link_previews(http):
    """The one explicit POST has no redirects, retries or environment proxies."""
    factory, session, response = http
    response.iter_content.return_value = [
        b'{"ok":true,"channel":"CFIXTURE","ts":"1234.5678"}'
    ]
    assert adapter.deliver_notification(b"synthetic-token", notification()) is (
        DeliveryOutcome.ACCEPTED
    )
    factory.assert_called_once_with()
    assert session.trust_env is False
    session.post.assert_called_once_with(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": "Bearer synthetic-token"},
        json=notification().message(),
        timeout=(5, 10),
        allow_redirects=False,
        stream=True,
    )
    message = notification().message()
    assert message["text"].startswith("TEST — ")
    assert not message["mrkdwn"] and not message["unfurl_links"]
    assert message["parse"] == "none" and not message["unfurl_media"]
    assert "synthetic-token" not in repr(notification())
    assert adapter.ReadinessNotification.from_payload(notification().payload()) == (
        notification()
    )


@pytest.mark.parametrize("error", sorted(adapter.REFUSALS))
def test_documented_refusal_is_definitely_unsent(http, error):
    """Only a closed, explicit provider refusal can permit an unsent outcome."""
    _, session, response = http
    response.iter_content.return_value = [
        json.dumps({"ok": False, "error": error}).encode()
    ]
    assert adapter.deliver_notification(b"synthetic-token", notification()) is (
        DeliveryOutcome.NOT_SENT
    )
    session.post.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [
        b"garbage",
        b"[]",
        b"{}",
        b'{"ok":true,"ok":false}',
        b'{"ok":false,"error":"internal_error"}',
        b'{"ok":false,"error":"fatal_error"}',
        b'{"ok":true,"channel":"OTHER","ts":"123.456"}',
        b'{"ok":true,"channel":"CFIXTURE","ts":123}',
        b'{"ok":true,"channel":"CFIXTURE","ts":"wrong"}',
        b"x" * (adapter.MAX_RESPONSE + 1),
    ],
)
def test_malformed_or_ambiguous_provider_response_is_never_retried(http, body):
    """Even a success-looking response needs exact channel and timestamp evidence."""
    _, session, response = http
    response.iter_content.return_value = [body]
    assert adapter.deliver_notification(b"synthetic-token", notification()) is (
        DeliveryOutcome.UNKNOWN
    )
    session.post.assert_called_once()


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_failures_preserve_uncertainty_except_explicit_rate_limit(http, status):
    """No redirect is followed or retry scheduled, including Retry-After responses."""
    _, session, response = http
    response.status_code = status
    assert adapter.deliver_notification(b"synthetic-token", notification()) is (
        DeliveryOutcome.NOT_SENT if status == 429 else DeliveryOutcome.UNKNOWN
    )
    session.post.assert_called_once()


def test_network_exception_does_not_echo_private_text_or_retry(http):
    """Connection loss after POST entry cannot prove no server-side effect."""
    _, session, _ = http
    session.post.side_effect = requests.Timeout("private token")
    assert adapter.deliver_notification(b"synthetic-token", notification()) is (
        DeliveryOutcome.UNKNOWN
    )
    session.post.assert_called_once()


@pytest.mark.parametrize("value", [b"", b"x\ny", b"x" * 4099, None])
def test_invalid_candidate_never_opens_network(http, value):
    """Local private parsing happens before HTTP submission."""
    factory, _, _ = http
    assert (
        adapter.deliver_notification(value, notification()) is DeliveryOutcome.NOT_SENT
    )
    factory.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"delivery_id": 1, "channel_id": "CFIXTURE"},
        {"delivery_id": "12345678123412341234123456789abc", "channel_id": "CFIXTURE"},
        {
            "delivery_id": "12345678-1234-1234-1234-123456789abc",
            "channel_id": "@everyone",
        },
    ],
)
def test_notification_payload_is_closed(value):
    """A channel mention, alternate UUID spelling or unknown shape is not routing."""
    with pytest.raises((ValueError, ConfigError)):
        adapter.ReadinessNotification.from_payload(value)


@pytest.mark.parametrize("raw", [b"", b"[]", b"{}", b"x" * (worker.MAX_INPUT + 1)])
def test_malformed_private_pipe_never_invokes_adapter(monkeypatch, raw):
    """Invalid requests are rejected without attempting a provider effect."""
    send = Mock()
    monkeypatch.setattr(worker, "deliver_notification", send)
    assert worker.submit_request(raw) is DeliveryOutcome.NOT_SENT
    send.assert_not_called()


@pytest.mark.parametrize("result", [*DeliveryOutcome, None, "private garbage"])
def test_worker_emits_only_closed_outcomes(monkeypatch, result):
    """Neither provider return values nor exception text become helper output."""
    send = Mock(return_value=result)
    monkeypatch.setattr(worker, "deliver_notification", send)
    assert worker.submit_request(payload()) is (
        result if isinstance(result, DeliveryOutcome) else DeliveryOutcome.UNKNOWN
    )
    send.side_effect = RuntimeError("private")
    assert worker.submit_request(payload()) is DeliveryOutcome.UNKNOWN


def test_parent_uses_fixed_helper_and_only_private_stdin(monkeypatch):
    """The common finite transport receives only the compiled Slack helper name."""
    process = Process(b"accepted\n", 0)
    launch = Mock(return_value=process)
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    assert (
        parent.submit_notification(
            b"synthetic-token", notification(), seconds=30, check=lambda: None
        )
        is DeliveryOutcome.ACCEPTED
    )
    assert launch.call_args.args[0] == [
        sys.executable,
        "-I",
        "-m",
        "parishkit.stewardship.readiness_notification_worker",
    ]
    assert launch.call_args.kwargs["env"] == {}
    assert worker.decode_request(process.inputs[0]) == (
        b"synthetic-token",
        notification(),
    )
    assert len(process.inputs) == 1


def test_real_helper_rejects_invalid_private_input_without_network():
    """Exercise the installed isolated helper with an unmistakably invalid token."""
    data = json.loads(payload())
    data["candidate"] = base64.b64encode(b"invalid\ntoken").decode()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "parishkit.stewardship.readiness_notification_worker",
        ],
        input=json.dumps(data).encode(),
        capture_output=True,
        timeout=10,
        env={},
    )
    assert result.returncode == 0
    assert result.stdout == b"not_sent\n" and result.stderr == b""


@pytest.mark.parametrize("seconds", [0, -1, 31, True, float("inf"), float("nan")])
def test_parent_limits_cannot_launch_an_unbounded_helper(monkeypatch, seconds):
    """Slack shares the same absolute, finite total deadline as readiness mail."""
    launch = Mock()
    monkeypatch.setattr(transport.subprocess, "Popen", launch)
    with pytest.raises(ValueError):
        parent.submit_notification(
            b"synthetic-token", notification(), seconds=seconds, check=lambda: None
        )
    launch.assert_not_called()
