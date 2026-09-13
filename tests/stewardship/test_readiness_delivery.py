"""SMTP acceptance, explicit refusal and uncertainty using a credential-free adapter."""

import smtplib
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.integration_candidates import GOOGLE_TOKEN_URI
from parishkit.stewardship.readiness_delivery import (
    DeliveryOutcome,
    _credentials,
    deliver_sample,
)
from parishkit.stewardship.readiness_mail import ReadinessMail

SETTINGS = {
    "delegated_email": "mail@example.org",
    "sender": "office@example.org",
    "reply_to": "reply@example.org",
    "recipient": "test@example.org",
}


def sample():
    """A rendered fictional message has no live credentials or real Family lookup."""
    return ReadinessMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        SETTINGS["recipient"],
        "Campaign preview",
        "<p>Hello Sample Family.</p>",
        "Hello Sample Family.",
    )


def test_readiness_message_roundtrip_mime_and_mandatory_routing():
    """Subject, both body alternatives and envelope scope remain explicitly Testing."""
    value = sample()
    assert ReadinessMail.from_payload(value.payload()) == value
    message = value.message()
    assert message["To"] == SETTINGS["recipient"]
    assert message["From"] == SETTINGS["sender"]
    assert message["Reply-To"] == SETTINGS["reply_to"]
    assert message["Subject"] == "[TEST] Campaign preview"
    assert message["Message-ID"] == value.message()["Message-ID"]
    assert not message["Cc"] and not message["Bcc"]
    for part in message.iter_parts():
        assert "TEST" in part.get_content() and "fictional" in part.get_content()
    assert SETTINGS["recipient"] not in repr(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("recipient", "injected@example.org\r\nBcc: private@example.org"),
        ("sender", "UPPER@example.org"),
        ("subject", ""),
        ("subject", "x" * 255),
        ("subject", "bad\nheader"),
        ("html", '<p onclick="unsafe()">Hello</p>'),
        ("text", None),
    ],
)
def test_invalid_readiness_message_is_rejected(field, value):
    """Provider workers cannot receive unchecked headers or executable HTML."""
    with pytest.raises((ValueError, ConfigError, TypeError)):
        replace(sample(), **{field: value})


@pytest.mark.parametrize("invalid", ["extra", "id", "shape", "type"])
def test_ipc_payload_has_a_closed_shape(invalid):
    """A queued message cannot import attachments, headers or alternate identities."""
    values = sample().payload()
    if invalid == "extra":
        values["attachments"] = []
    elif invalid == "id":
        values["delivery_id"] = uuid4().hex
    elif invalid == "type":
        values["text"] = 42
    else:
        values = []
    with pytest.raises(ValueError):
        ReadinessMail.from_payload(values)


def delivery(monkeypatch, *, outcome=None, exit_error=None, auth=235, greeting=250):
    """Run the actual protocol adapter with a small deterministic SMTP peer."""
    seen = []
    monkeypatch.setattr(
        "parishkit.stewardship.readiness_delivery._credentials",
        lambda *args: SimpleNamespace(token="synthetic-private-token"),
    )

    class SMTP:
        def __init__(self, host, port, **kwargs):
            """Only the fixed Gmail TLS endpoint and finite timeout are selected."""
            assert (host, port) == ("smtp.gmail.com", 465)
            assert kwargs["timeout"] == 10 and kwargs["context"].check_hostname

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if exit_error:
                raise exit_error

        def ehlo(self):
            return greeting, b"private server greeting"

        def docmd(self, name, value):
            """Inspect command type without printing private bearer material."""
            assert name == "AUTH" and value.startswith("XOAUTH2 ")
            return auth, b"private auth response"

        def send_message(self, message, *, from_addr, to_addrs):
            """Use one explicit recipient, without header recipient expansion."""
            seen.append(message)
            assert from_addr == SETTINGS["sender"] and to_addrs == [
                SETTINGS["recipient"]
            ]
            if isinstance(outcome, Exception):
                raise outcome
            return {} if outcome is None else outcome

    result = deliver_sample(
        b"synthetic-private-key",
        SETTINGS,
        sample(),
        smtp_factory=SMTP,
        session_factory=nullcontext,
    )
    return result, seen


@pytest.mark.parametrize(
    "exit_error",
    [None, OSError("private disconnect"), smtplib.SMTPException("private QUIT")],
)
def test_positive_acceptance_survives_connection_shutdown(monkeypatch, exit_error):
    """A 250 DATA acceptance is not undone by a subsequent connection failure."""
    result, seen = delivery(monkeypatch, exit_error=exit_error)
    assert result is DeliveryOutcome.ACCEPTED and len(seen) == 1


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPRecipientsRefused({"test@example.org": (550, b"private")}),
        smtplib.SMTPSenderRefused(550, b"private", "sender@example.org"),
        smtplib.SMTPDataError(451, b"private"),
        smtplib.SMTPDataError(554, b"private"),
        smtplib.SMTPNotSupportedError("private"),
        {"test@example.org": (550, b"private")},
    ],
)
def test_explicit_nonacceptance_is_not_unknown(monkeypatch, failure):
    """Address/data refusal is a definitive outcome, not a retained uncertainty."""
    result, seen = delivery(monkeypatch, outcome=failure)
    assert result is DeliveryOutcome.NOT_SENT and len(seen) == 1
    result, _ = delivery(
        monkeypatch, outcome=failure, exit_error=OSError("private QUIT")
    )
    assert result is DeliveryOutcome.NOT_SENT


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private"),
        smtplib.SMTPServerDisconnected("private"),
        smtplib.SMTPDataError(-1, b"private"),
        smtplib.SMTPDataError(251, b"private"),
        RuntimeError("private"),
        "malformed",
        {"unexpected@example.org": (500, b"private")},
    ],
)
def test_uncertain_submission_never_becomes_retryable_failure(monkeypatch, failure):
    """Unexpected outcomes after submission begins cannot justify another send."""
    result, seen = delivery(monkeypatch, outcome=failure)
    assert result is DeliveryOutcome.UNKNOWN and len(seen) == 1


@pytest.mark.parametrize("kwargs", [{"auth": 535}, {"greeting": 500}])
def test_handshake_failure_never_submits_message(monkeypatch, kwargs):
    """Provider authentication and mail acceptance are separate outcomes."""
    result, seen = delivery(monkeypatch, **kwargs)
    assert result is DeliveryOutcome.NOT_SENT and seen == []


def test_scope_changes_reject_before_provider_or_token_calls():
    """Message headers cannot override the server-owned credential context."""
    with pytest.raises(ValueError, match="admitted mail context"):
        deliver_sample(
            b"unused", SETTINGS | {"recipient": "other@example.org"}, sample()
        )


def test_private_token_errors_before_submission_remain_unsent(monkeypatch):
    """Credentials never need a plaintext temporary file or logged exception."""

    def fail(*args):
        raise RuntimeError("private token request")

    monkeypatch.setattr("parishkit.stewardship.readiness_delivery._credentials", fail)
    assert (
        deliver_sample(b"synthetic", SETTINGS, sample(), session_factory=nullcontext)
        is DeliveryOutcome.NOT_SENT
    )


def test_token_exchange_reuses_closed_context_and_transport(monkeypatch):
    """A candidate cannot redirect its delegated JWT to an arbitrary URL."""
    calls = []

    class Credentials:
        def refresh(self, request):
            """Exercise the real credential transport adapter with synthetic bytes."""
            response = request(
                GOOGLE_TOKEN_URI, method="POST", body=b"synthetic", headers={}
            )
            assert response.status == 200 and response.data == b"synthetic"
            with pytest.raises(ValueError):
                request("https://unrelated.invalid/", method="POST")

    def candidate(value, *, delegated_email):
        calls.append((value, delegated_email))
        return Credentials()

    monkeypatch.setattr(
        "parishkit.stewardship.readiness_delivery.workspace_candidate", candidate
    )
    session = SimpleNamespace(
        request=lambda *args, **kwargs: SimpleNamespace(
            status_code=200, content=b"synthetic", headers={}
        )
    )
    assert isinstance(_credentials(b"synthetic", SETTINGS, session), Credentials)
    assert calls == [(b"synthetic", SETTINGS["delegated_email"])]
