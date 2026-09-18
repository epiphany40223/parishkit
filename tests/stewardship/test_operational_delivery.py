"""Operational transports cannot carry campaign content or weaken send certainty."""

import base64
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import operational_slack_worker as slack_worker
from parishkit.stewardship import readiness_delivery_process as pipe
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.operational_content import (
    AlertPhase,
    IncidentKind,
    IncidentLevel,
    OperationalAlert,
)
from parishkit.stewardship.jobs.operational_payload import alert_payload, decode_alert
from parishkit.stewardship.operational_delivery import (
    OperationalMail,
    OperationalSlack,
    deliver_operational_mail,
    deliver_operational_slack,
)
from parishkit.stewardship.operational_delivery_process import (
    submit_operational_mail,
    submit_operational_slack,
)
from parishkit.stewardship.operational_mail_worker import decode_request as decode_mail
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.readiness_notification import (
    ReadinessNotification,
    deliver_notification,
)

from .test_family_delivery import SETTINGS, delivery
from .test_provider_checks import Process
from .test_readiness_notification import http as http_fixture

# Reuse the existing fake HTTP session without another real-service bootstrap.
http = http_fixture


def alert():
    """Entirely synthetic fixed facts need no database, clock sleep or provider."""
    instant = datetime(2026, 9, 18, tzinfo=UTC)
    return OperationalAlert(
        uuid4(),
        IncidentKind.LIMITER_UNAVAILABLE,
        IncidentLevel.CRITICAL,
        AlertPhase.OPENED,
        SystemMode.TESTING,
        instant,
        instant,
        1234,
    )


def mail():
    """Operational mail addresses one Admin, even when deployment mode is Testing."""
    return OperationalMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("admin@example.org",),
        alert(),
    )


def slack():
    """The channel grammar rejects mentions before private helper invocation."""
    return OperationalSlack(uuid4(), "CFIXTURE", alert())


def slack_request(notification=None):
    """Use bytes that are never passed to a real provider by this suite."""
    return json.dumps(
        {
            "candidate": base64.b64encode(b"synthetic-token").decode(),
            "notification": (notification or slack()).payload(),
        }
    ).encode()


def test_closed_facts_roundtrip_and_generated_mime():
    """The helper recompiles content and never receives arbitrary HTML/plaintext."""
    sample = mail()
    assert OperationalMail.from_payload(sample.payload()) == sample
    assert decode_alert(alert_payload(sample.alert)) == sample.alert
    mime = sample.message()
    assert mime["To"] == "admin@example.org"
    assert mime["Cc"] is mime["Bcc"] is None
    assert mime["Subject"].startswith("[TESTING] CRITICAL:")
    assert mime["Message-ID"] == sample.message()["Message-ID"]
    assert not list(mime.iter_attachments())
    assert "1,234" in mime.get_body(preferencelist=("plain",)).get_content()
    assert "admin@example.org" not in repr(sample)
    assert "html" not in sample.payload() and "text" not in sample.payload()


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "private@example.test"),
        ("phase", "unknown"),
        ("mode", "TESTING"),
        ("level", []),
        ("first_seen", "private@example.test"),
        ("observed_at", 1),
        ("occurrences", True),
        ("occurrences", "1"),
        ("incident_id", None),
        ("template", "private@example.test"),
        ("first_seen", "2026-09-18T00:00:00Z"),
    ],
)
def test_invalid_fact_payloads_are_rejected_without_echo(field, value):
    """Strict canonical decoding excludes free text and ambiguous wire encodings."""
    with pytest.raises(ValueError) as error:
        decode_alert(alert_payload(alert()) | {field: value})
    assert "private@example.test" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("html", "<p>Private answers</p>"),
        ("text", "private"),
        ("recipients", ["one@example.org", "two@example.org"]),
        ("recipients", "admin@example.org"),
        ("sender", "bad\nheader"),
        ("schema", "weekly-digest-mail-v1"),
        ("semantic_key", "not-a-uuid"),
    ],
)
def test_operational_mail_cannot_be_used_for_arbitrary_content(field, value):
    """Private helper schema, not a caller's purpose flag, excludes Family data."""
    with pytest.raises(ValueError):
        OperationalMail.from_payload(mail().payload() | {field: value})


@pytest.mark.parametrize(
    "failure,status",
    [
        (None, FamilyDeliveryStatus.ACCEPTED),
        ("auth", FamilyDeliveryStatus.UNAVAILABLE),
        ("data", FamilyDeliveryStatus.UNKNOWN),
    ],
)
def test_operational_mail_preserves_actual_smtp_acceptance_boundary(
    monkeypatch, failure, status
):
    """Test our new adapter path; exhaustive common SMTP outcomes remain shared."""
    result, seen = delivery(
        monkeypatch, mail=mail(), failure=failure, adapter=deliver_operational_mail
    )
    assert result.status is status
    assert seen.count("data") == (0 if failure == "auth" else 1)


def test_fixed_slack_message_and_delivery_reuse_safe_transport(http):
    """The new purpose keeps fixed endpoint, one POST, mode label and no previews."""
    factory, session, response = http
    response.iter_content.return_value = [
        b'{"ok":true,"channel":"CFIXTURE","ts":"123.456"}'
    ]
    sample = slack()
    assert OperationalSlack.from_payload(sample.payload()) == sample
    assert (
        deliver_operational_slack(b"synthetic-token", sample)
        is DeliveryOutcome.ACCEPTED
    )
    factory.assert_called_once()
    session.post.assert_called_once()
    message = session.post.call_args.kwargs["json"]
    assert message["text"].startswith("[TESTING] CRITICAL:")
    assert message["channel"] == "CFIXTURE"
    assert not message["mrkdwn"] and not message["unfurl_links"]
    assert not message["unfurl_media"] and message["parse"] == "none"
    assert session.trust_env is False
    assert session.post.call_args.kwargs["allow_redirects"] is False


def test_slack_ambiguity_does_not_retry_or_echo_private_failure(http):
    """Once POST entry occurs, an unexpected exception is not proof of no effect."""
    _, session, _ = http
    session.post.side_effect = RuntimeError("private provider failure")
    assert (
        deliver_operational_slack(b"synthetic-token", slack())
        is DeliveryOutcome.UNKNOWN
    )
    session.post.assert_called_once()


def test_operational_and_readiness_slack_types_are_not_interchangeable(http):
    """Sharing transport does not widen either public adapter's accepted payload."""
    factory, _, _ = http
    sample = ReadinessNotification(uuid4(), "CFIXTURE")
    assert (
        deliver_operational_slack(b"synthetic-token", sample)
        is DeliveryOutcome.NOT_SENT
    )
    assert deliver_notification(b"synthetic-token", slack()) is DeliveryOutcome.NOT_SENT
    for cls, payload in (
        (OperationalSlack, sample.payload()),
        (ReadinessNotification, slack().payload()),
    ):
        with pytest.raises(ValueError):
            cls.from_payload(payload)
    with pytest.raises(ConfigError):
        replace(slack(), channel_id="@everyone")
    factory.assert_not_called()


@pytest.mark.parametrize("purpose", ["mail", "slack"])
def test_fixed_private_helper_and_validated_pipe_roundtrip(monkeypatch, purpose):
    """Use the real pipe owner with a fake process, not a real service or token."""
    response = (
        json.dumps(
            FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1).payload()
        ).encode()
        if purpose == "mail"
        else b"accepted\n"
    )
    process = Process(response, 0)
    launch = Mock(return_value=process)
    monkeypatch.setattr(pipe.subprocess, "Popen", launch)
    if purpose == "mail":
        sample = mail()
        result = submit_operational_mail(
            b"synthetic", SETTINGS, sample, seconds=5, check=lambda: None
        )
        assert result.status is FamilyDeliveryStatus.ACCEPTED
        assert decode_mail(process.inputs[0])[2] == sample
    else:
        sample = slack()
        assert (
            submit_operational_slack(
                b"synthetic", sample, seconds=5, check=lambda: None
            )
            is DeliveryOutcome.ACCEPTED
        )
        assert slack_worker.decode_request(process.inputs[0])[1] == sample
    assert launch.call_args.args[0] == [
        sys.executable,
        "-I",
        "-m",
        f"parishkit.stewardship.operational_{purpose}_worker",
    ]
    assert launch.call_args.kwargs["env"] == {}
    assert launch.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert launch.call_args.kwargs["close_fds"] is True
    assert len(process.inputs) == 1


def test_malformed_slack_input_never_enters_transport(monkeypatch):
    """Rejected payloads remain unsent; bad post-entry results remain unknown."""
    send = Mock()
    monkeypatch.setattr(slack_worker, "deliver_operational_slack", send)
    for value in (b"", b"{}", b"[]", b"x" * (slack_worker.MAX_INPUT + 1)):
        assert slack_worker.submit_request(value) is DeliveryOutcome.NOT_SENT
    send.assert_not_called()
    send.return_value = "private garbage"
    assert slack_worker.submit_request(slack_request()) is DeliveryOutcome.UNKNOWN
    send.side_effect = RuntimeError("private")
    assert slack_worker.submit_request(slack_request()) is DeliveryOutcome.UNKNOWN


@pytest.mark.parametrize("purpose", ["mail", "slack"])
def test_real_operational_helpers_reject_invalid_credentials_without_network(purpose):
    """Exercise installed isolated entry points without repeating service bootstrap."""
    request = (
        json.dumps(
            {
                "candidate": base64.b64encode(b"synthetic").decode(),
                "settings": SETTINGS,
                "mail": mail().payload(),
            }
        ).encode()
        if purpose == "mail"
        else slack_request()
    )
    if purpose == "slack":
        value = json.loads(request)
        value["candidate"] = base64.b64encode(b"invalid\ntoken").decode()
        request = json.dumps(value).encode()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            f"parishkit.stewardship.operational_{purpose}_worker",
        ],
        input=request,
        capture_output=True,
        timeout=10,
        env={},
    )
    assert result.returncode == 0 and result.stderr == b""
    if purpose == "mail":
        assert (
            FamilyDeliveryResult.from_payload(
                json.loads(result.stdout), recipient_count=1
            ).status
            is FamilyDeliveryStatus.SYSTEMIC
        )
    else:
        assert result.stdout == b"not_sent\n"
