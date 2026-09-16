"""Credential-free multi-recipient SMTP boundaries and refusal classification."""

import smtplib
import ssl
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
    ProviderHealth,
    deliver_family,
)
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryStatus as Status,
)

SETTINGS = {
    "delegated_email": "mail@example.org",
    "sender": "office@example.org",
    "reply_to": "reply@example.org",
}


def sample():
    """Synthetic private substitutions never require an actual Family or provider."""
    return FamilyDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("a@example.org", "b@example.org"),
        "Campaign",
        "<p>Synthetic private code</p>",
        "Synthetic private code",
    )


def delivery(
    monkeypatch,
    *,
    replies=None,
    failure=None,
    quit_error=False,
    mail=None,
    international=False,
    credential_error=None,
    smtp_error=None,
    stage_error=None,
):
    """Inject replies or failure at exact protocol boundaries, without network IO."""
    seen = []
    replies = {
        "ehlo": 250,
        "auth": 235,
        "mail": 250,
        "rcpt0": 250,
        "rcpt1": 250,
        "data": 250,
    } | (replies or {})

    def credentials(*args):
        if credential_error is not None:
            raise credential_error
        return SimpleNamespace(token="synthetic-private-token")

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery._credentials", credentials
    )

    def response(step):
        seen.append(step)
        if failure == step:
            raise stage_error or TimeoutError("private SMTP response")
        return replies[step], b"private SMTP response"

    class SMTP:
        def __init__(self, host, port, **kwargs):
            """TLS endpoint and timeout remain compiled, not provider input."""
            assert (host, port, kwargs["timeout"]) == ("smtp.gmail.com", 465, 10)
            assert kwargs["context"].check_hostname
            if smtp_error is not None:
                raise smtp_error
            self.addresses = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if quit_error:
                raise OSError("private QUIT response")

        def ehlo(self):
            return response("ehlo")

        def docmd(self, name, value):
            assert name == "AUTH" and value.startswith("XOAUTH2 ")
            return response("auth")

        def mail(self, sender, *, options):
            assert sender == SETTINGS["sender"]
            assert options == (["SMTPUTF8", "BODY=8BITMIME"] if international else [])
            return response("mail")

        def has_extn(self, name):
            assert name == "smtputf8"
            return international

        def rcpt(self, address):
            self.addresses.append(address)
            return response("rcpt" + str(len(self.addresses) - 1))

        def data(self, content):
            if not international:
                assert content.isascii()
            return response("data")

    return deliver_family(
        b"synthetic-key",
        SETTINGS | ({"reply_to": mail.reply_to} if mail else {}),
        mail or sample(),
        smtp_factory=SMTP,
        session_factory=nullcontext,
    ), seen


@pytest.mark.parametrize("quit_error", [False, True])
@pytest.mark.parametrize("code", [450, 550])
def test_partial_refusal_still_accepts_one_family_message(
    monkeypatch, code, quit_error
):
    """A refused address cannot cause a duplicate send to the accepted address."""
    result, seen = delivery(monkeypatch, replies={"rcpt0": code}, quit_error=quit_error)
    assert result == FamilyDeliveryResult(
        Status.ACCEPTED, 2, (0,) if code == 550 else (), (0,) if code == 450 else ()
    )
    assert seen.count("data") == 1


@pytest.mark.parametrize(
    "first,second,status",
    [
        (550, 550, Status.PERMANENT),
        (450, 550, Status.TRANSIENT),
        (550, 450, Status.TRANSIENT),
        (450, 450, Status.TRANSIENT),
    ],
)
def test_all_refused_never_submits_data(monkeypatch, first, second, status):
    """Mixed permanent/transient refusals remain retryable for usable addresses."""
    result, seen = delivery(monkeypatch, replies={"rcpt0": first, "rcpt1": second})
    assert result.status is status and "data" not in seen
    assert result.permanent == tuple(
        i for i, code in enumerate((first, second)) if code == 550
    )


@pytest.mark.parametrize(
    "step,status",
    [
        ("ehlo", Status.UNAVAILABLE),
        ("auth", Status.UNAVAILABLE),
        ("mail", Status.UNAVAILABLE),
        ("rcpt0", Status.UNAVAILABLE),
        ("rcpt1", Status.UNAVAILABLE),
        ("data", Status.UNKNOWN),
    ],
)
def test_timeout_classification_tracks_data_boundary(monkeypatch, step, status):
    """Neither RCPT acceptance nor a lost DATA acknowledgement proves delivery."""
    result, seen = delivery(monkeypatch, failure=step)
    assert result.status is status and seen[-1] == step


@pytest.mark.parametrize(
    "code,status",
    [
        (250, Status.ACCEPTED),
        (451, Status.TRANSIENT),
        (554, Status.PERMANENT),
        (251, Status.UNKNOWN),
        (-1, Status.UNKNOWN),
        (True, Status.UNKNOWN),
    ],
)
def test_data_reply_preserves_refusals_and_survives_quit(monkeypatch, code, status):
    """Only a complete accepted DATA response fulfills the Family semantic slot."""
    result, _ = delivery(
        monkeypatch, replies={"rcpt0": 550, "data": code}, quit_error=True
    )
    assert result == FamilyDeliveryResult(
        status,
        2,
        permanent=(0,),
        health=ProviderHealth.SYSTEMIC
        if status is Status.UNKNOWN
        else ProviderHealth.HEALTHY,
    )
    assert "private" not in repr(result)


@pytest.mark.parametrize("stage", ["ehlo", "auth", "mail", "rcpt0"])
def test_invalid_handshake_stops_before_data(monkeypatch, stage):
    """Unexpected statuses are not fabricated as address-specific refusals."""
    result, seen = delivery(monkeypatch, replies={stage: 299})
    assert result.status is (Status.TRANSIENT if stage == "rcpt0" else Status.SYSTEMIC)
    assert "data" not in seen


def test_mail_is_private_and_has_no_cross_family_headers():
    """MIME includes exactly the admitted envelope, with stable correlation only."""
    mail = sample()
    message = mail.message()
    assert str(message["To"]) == "a@example.org, b@example.org"
    assert message["Reply-To"] == SETTINGS["reply_to"]
    assert message["Message-ID"] == mail.message()["Message-ID"]
    assert not message["Cc"] and not message["Bcc"]
    assert "Synthetic private code" not in repr(mail)


@pytest.mark.parametrize(
    "changes",
    [
        {"recipient_count": True},
        {"recipient_count": 0},
        {"permanent": (True,)},
        {"permanent": (2,)},
        {"permanent": (0, 0)},
        {"permanent": (0,), "transient": (0,)},
        {"permanent": (0, 1)},
        {"status": "accepted"},
        {"transient": [0]},
        {"health": "healthy"},
        {"health": ProviderHealth.UNOBSERVED},
    ],
)
def test_result_rejects_contradictory_or_untyped_evidence(changes):
    """Private IPC must not fabricate accepted-all-refused or cross-envelope data."""
    with pytest.raises(ValueError):
        replace(FamilyDeliveryResult(Status.ACCEPTED, 2), **changes)


def test_unadmitted_headers_fail_before_provider_access():
    """The helper cannot change the sender selected by the owning configuration."""
    with pytest.raises(ValueError, match="admitted context"):
        deliver_family(
            b"unused", SETTINGS, replace(sample(), sender="other@example.org")
        )


@pytest.mark.parametrize(
    "code,status",
    [(451, Status.TRANSIENT), (554, Status.PERMANENT), (250, Status.UNKNOWN)],
)
def test_data_exception_with_definitive_code_is_not_unknown(monkeypatch, code, status):
    """The stdlib may raise instead of returning a rejected DATA command."""
    from parishkit.stewardship.family_delivery import _submit

    smtp = SimpleNamespace(
        mail=lambda *args, **kwargs: (250, b""),
        rcpt=lambda *args: (250, b""),
    )

    def refused(*args):
        raise smtplib.SMTPDataError(code, b"private")

    smtp.data = refused
    assert _submit(smtp, sample()).status is status


@pytest.mark.parametrize("stage", ["ehlo", "auth", "mail"])
@pytest.mark.parametrize("code", [421, 451, 454])
def test_temporary_handshake_reply_retries_without_halting(monkeypatch, stage, code):
    """No DATA was sent; short provider outages cannot permanently fail a Family."""
    result, seen = delivery(monkeypatch, replies={stage: code})
    assert result.status is Status.UNAVAILABLE and "data" not in seen


@pytest.mark.parametrize("retryable", [False, True])
def test_token_refusal_distinguishes_retryable_provider_failure(monkeypatch, retryable):
    """Credential refusal and temporary token service failure are separate."""
    from google.auth.exceptions import RefreshError

    result, seen = delivery(
        monkeypatch, credential_error=RefreshError("private", retryable=retryable)
    )
    assert not seen
    assert result.status is (Status.UNAVAILABLE if retryable else Status.SYSTEMIC)


def test_token_timeout_is_definitively_unsent(monkeypatch):
    """Token exchange precedes SMTP, so its timeout needs no duplicate-risk review."""
    result, seen = delivery(monkeypatch, credential_error=TimeoutError("private"))
    assert not seen and result.status is Status.UNAVAILABLE


@pytest.mark.parametrize("international", [False, True])
def test_international_family_does_not_halt_other_families(monkeypatch, international):
    """Lack of SMTPUTF8 is not evidence that an address was refused by RCPT."""
    mail = replace(sample(), recipients=("a@éxample.org", "b@example.org"))
    result, seen = delivery(monkeypatch, mail=mail, international=international)
    assert result.status is (Status.ACCEPTED if international else Status.PERMANENT)
    assert result.permanent == ()
    assert ("data" in seen) is international


def test_unicode_body_uses_seven_bit_transfer_encoding(monkeypatch):
    """ASCII envelopes do not silently send raw eight-bit bodies without negotiation."""
    mail = replace(sample(), html="<p>Église</p>", text="Église")
    result, seen = delivery(monkeypatch, mail=mail)
    assert result.status is Status.ACCEPTED and "data" in seen


def test_unicode_reply_to_participates_in_utf8_negotiation(monkeypatch):
    """Shared address headers use the same SMTPUTF8 decision as the envelope."""
    mail = replace(sample(), reply_to="reply@éxample.org")
    result, seen = delivery(monkeypatch, mail=mail, international=True)
    assert result.status is Status.ACCEPTED and "data" in seen


@pytest.mark.parametrize(
    "error,status",
    [
        (TimeoutError("private"), Status.UNAVAILABLE),
        (ssl.SSLCertVerificationError("private"), Status.SYSTEMIC),
        (smtplib.SMTPConnectError(421, b"private"), Status.UNAVAILABLE),
        (smtplib.SMTPConnectError(554, b"private"), Status.SYSTEMIC),
        (smtplib.SMTPServerDisconnected("private"), Status.UNAVAILABLE),
        (ValueError("private"), Status.SYSTEMIC),
    ],
)
def test_shared_connection_failures_have_explicit_classification(
    monkeypatch, error, status
):
    """A broken shared TLS/protocol configuration must stop the sending run."""
    result, seen = delivery(monkeypatch, smtp_error=error)
    assert result.status is status and not seen


@pytest.mark.parametrize("stage", ["ehlo", "auth"])
def test_malformed_handshake_reply_is_systemic(monkeypatch, stage):
    """A malformed shared reply is not a recipient-specific retryable refusal."""
    result, seen = delivery(monkeypatch, replies={stage: "invalid"})
    assert result.status is Status.SYSTEMIC and "data" not in seen


@pytest.mark.parametrize("stage", ["ehlo", "auth", "mail"])
def test_quit_cannot_hide_definitive_shared_refusal(monkeypatch, stage):
    """The observed shared refusal survives a subsequent broken connection."""
    result, seen = delivery(monkeypatch, replies={stage: 554}, quit_error=True)
    assert result.status is Status.SYSTEMIC and "data" not in seen


def test_unicode_shared_header_without_extension_is_systemic(monkeypatch):
    """Unlike a Family-specific address, the configured header affects all mail."""
    result, seen = delivery(
        monkeypatch, mail=replace(sample(), reply_to="reply@éxample.org")
    )
    assert result.status is Status.SYSTEMIC and not seen[2:]


def test_token_certificate_failure_is_not_a_temporary_outage(monkeypatch):
    """TLS validation is shared configuration evidence, never Family refusal."""
    import requests

    result, seen = delivery(
        monkeypatch, credential_error=requests.exceptions.SSLError("private")
    )
    assert result.status is Status.SYSTEMIC and not seen


@pytest.mark.parametrize("stage", ["mail", "rcpt1", "data"])
@pytest.mark.parametrize(
    "error,fault",
    [
        (ssl.SSLCertVerificationError("private"), Status.SYSTEMIC),
        (ssl.SSLEOFError("private"), Status.UNAVAILABLE),
        (ssl.SSLZeroReturnError("private"), Status.UNAVAILABLE),
        (ssl.SSLSyscallError("private"), Status.UNAVAILABLE),
        (smtplib.SMTPServerDisconnected("private"), Status.UNAVAILABLE),
        (TimeoutError("private"), Status.UNAVAILABLE),
        (ValueError("private"), Status.SYSTEMIC),
    ],
)
def test_shared_fault_preserves_protocol_certainty_and_earlier_refusals(
    monkeypatch, stage, error, fault
):
    """Connection health cannot erase prior RCPT refusal or invent DATA certainty."""
    result, seen = delivery(
        monkeypatch, failure=stage, stage_error=error, replies={"rcpt0": 550}
    )
    assert result.status is (Status.UNKNOWN if stage == "data" else fault)
    assert result.health is ProviderHealth(fault.value)
    assert result.permanent == (() if stage == "mail" else (0,))
    assert seen[-1] == stage


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLEOFError("private"),
        ssl.SSLZeroReturnError("private"),
        ssl.SSLSyscallError("private"),
        ssl.SSLCertVerificationError("private"),
    ],
)
def test_token_tls_wrappers_preserve_typed_root_fault(monkeypatch, error):
    """Requests/urllib3 wrapper text is private and irrelevant to classification."""
    import requests
    from urllib3.exceptions import MaxRetryError, SSLError

    wrapped = requests.exceptions.SSLError(
        MaxRetryError(None, "private", SSLError(error))
    )
    result, seen = delivery(monkeypatch, credential_error=wrapped)
    assert not seen
    assert result.status is (
        Status.SYSTEMIC
        if isinstance(error, ssl.SSLCertVerificationError)
        else Status.UNAVAILABLE
    )


def test_unobserved_result_cannot_claim_a_recipient_refusal():
    """The local-only result vocabulary cannot manufacture provider evidence."""
    with pytest.raises(ValueError, match="Unobserved"):
        FamilyDeliveryResult(
            Status.PERMANENT, 2, permanent=(0,), health=ProviderHealth.UNOBSERVED
        )
