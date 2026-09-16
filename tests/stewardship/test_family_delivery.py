"""Credential-free multi-recipient SMTP boundaries and refusal classification."""

import smtplib
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
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


def delivery(monkeypatch, *, replies=None, failure=None, quit_error=False, mail=None):
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
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery._credentials",
        lambda *args: SimpleNamespace(token="synthetic-private-token"),
    )

    def response(step):
        seen.append(step)
        if failure == step:
            raise TimeoutError("private SMTP response")
        return replies[step], b"private SMTP response"

    class SMTP:
        def __init__(self, host, port, **kwargs):
            """TLS endpoint and timeout remain compiled, not provider input."""
            assert (host, port, kwargs["timeout"]) == ("smtp.gmail.com", 465, 10)
            assert kwargs["context"].check_hostname
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
            assert sender == SETTINGS["sender"] and options == []
            return response("mail")

        def rcpt(self, address):
            self.addresses.append(address)
            return response("rcpt" + str(len(self.addresses) - 1))

        def data(self, content):
            assert b"Synthetic private code" in content
            return response("data")

    return deliver_family(
        b"synthetic-key",
        SETTINGS,
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
        ("ehlo", Status.SYSTEMIC),
        ("auth", Status.SYSTEMIC),
        ("mail", Status.TRANSIENT),
        ("rcpt0", Status.TRANSIENT),
        ("rcpt1", Status.TRANSIENT),
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
    assert result == FamilyDeliveryResult(status, 2, permanent=(0,))
    assert "private" not in repr(result)


@pytest.mark.parametrize("stage", ["ehlo", "auth", "mail", "rcpt0"])
def test_invalid_handshake_stops_before_data(monkeypatch, stage):
    """Unexpected statuses are not fabricated as address-specific refusals."""
    result, seen = delivery(monkeypatch, replies={stage: 299})
    assert result.status is Status.SYSTEMIC and "data" not in seen


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
