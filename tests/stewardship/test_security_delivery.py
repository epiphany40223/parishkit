"""Security alert transports carry only the event's facts and keep send certainty."""

import base64
import json
import subprocess
import sys
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship import readiness_delivery_process as pipe
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.security_content import SecurityAlert
from parishkit.stewardship.operational_delivery import OperationalMail
from parishkit.stewardship.operational_delivery_process import (
    submit_operational_mail,
    submit_security_mail,
)
from parishkit.stewardship.security_delivery import (
    SecurityMail,
    alert_payload,
    decode_alert,
    deliver_security_mail,
)
from parishkit.stewardship.security_mail_worker import decode_request

from .test_family_delivery import SETTINGS
from .test_operational_delivery import mail as operational_mail
from .test_provider_checks import Process


def alert(**values):
    """Entirely synthetic fixed facts need no database, clock sleep or provider."""
    return SecurityAlert(
        **{
            "event_id": UUID(int=9),
            "kind": "administrator_granted",
            "target": "new@example.org",
            "actor": "admin@example.org",
            "mode": SystemMode.TESTING,
            "occurred_at": datetime(2026, 9, 21, 14, 30, 5, tzinfo=UTC),
            "before_roles": (),
            "after_roles": ("administrator",),
        }
        | values
    )


def mail():
    """A security alert addresses one recorded Administrator."""
    return SecurityMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("second@example.org",),
        alert(),
    )


def test_closed_facts_roundtrip_and_generated_mime():
    """The helper recompiles content and never receives arbitrary HTML/plaintext."""
    sample = mail()
    assert SecurityMail.from_payload(sample.payload()) == sample
    assert decode_alert(alert_payload(sample.alert)) == sample.alert
    mime = sample.message()
    assert mime["To"] == "second@example.org"
    assert mime["Cc"] is mime["Bcc"] is None
    assert mime["Subject"] == (
        "[TESTING] SECURITY: Administrator added to an exact address"
    )
    assert mime["Message-ID"] == sample.message()["Message-ID"]
    assert not list(mime.iter_attachments())
    body = mime.get_body(preferencelist=("plain",)).get_content()
    assert "Target: new@example.org" in body and "By: admin@example.org" in body
    assert "second@example.org" not in repr(sample)
    assert "html" not in sample.payload() and "text" not in sample.payload()


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "task_failed"),
        ("target", ""),
        ("actor", ["admin@example.org"]),
        ("mode", "staging"),
        ("occurred_at", "2026-09-21"),
        ("before_roles", "staff"),
        ("extra", "x"),
    ],
)
def test_invalid_fact_payloads_are_rejected_without_echo(field, value):
    """Neither the pipe nor the helper accepts a fact outside the closed vocabulary."""
    payload = alert_payload(alert())
    payload[field] = value
    with pytest.raises(ValueError) as caught:
        decode_alert(payload)
    assert "example.org" not in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "operational-mail-v1"),
        ("recipients", ["a@example.org", "b@example.org"]),
        ("recipients", ["not an address"]),
        ("sender", "sender@example.org\nBcc: x@example.org"),
    ],
)
def test_security_mail_cannot_be_used_for_arbitrary_content(field, value):
    """The envelope refuses another schema, a second recipient or a header injection."""
    payload = mail().payload()
    payload[field] = value
    with pytest.raises(ValueError):
        SecurityMail.from_payload(payload)


def test_operational_and_security_envelopes_are_not_interchangeable():
    """Each private helper decodes only its own closed envelope."""
    with pytest.raises(ValueError):
        SecurityMail.from_payload(operational_mail().payload())
    with pytest.raises(ValueError):
        OperationalMail.from_payload(mail().payload())
    with pytest.raises(ValueError):
        submit_security_mail(
            b"synthetic", SETTINGS, operational_mail(), seconds=5, check=lambda: None
        )
    with pytest.raises(ValueError):
        submit_operational_mail(
            b"synthetic", SETTINGS, mail(), seconds=5, check=lambda: None
        )
    with pytest.raises(ValueError):
        deliver_security_mail(b"synthetic", SETTINGS, operational_mail())


def test_fixed_private_helper_and_validated_pipe_roundtrip(monkeypatch):
    """Use the real pipe owner with a fake process, not a real service or token."""
    response = json.dumps(
        FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1).payload()
    ).encode()
    process = Process(response, 0)
    launch = Mock(return_value=process)
    monkeypatch.setattr(pipe.subprocess, "Popen", launch)
    sample = mail()
    result = submit_security_mail(
        b"synthetic", SETTINGS, sample, seconds=5, check=lambda: None
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    assert decode_request(process.inputs[0])[2] == sample
    assert launch.call_args.args[0] == [
        sys.executable,
        "-I",
        "-m",
        "parishkit.stewardship.security_mail_worker",
    ]
    assert launch.call_args.kwargs["env"] == {}
    assert launch.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert launch.call_args.kwargs["close_fds"] is True
    assert len(process.inputs) == 1


def test_real_security_helper_rejects_invalid_credentials_without_network():
    """Exercise the installed isolated entry point with no service bootstrap."""
    request = json.dumps(
        {
            "candidate": base64.b64encode(b"synthetic").decode(),
            "settings": SETTINGS,
            "mail": mail().payload(),
        }
    ).encode()
    result = subprocess.run(
        [sys.executable, "-I", "-m", "parishkit.stewardship.security_mail_worker"],
        input=request,
        capture_output=True,
        timeout=10,
        env={},
    )
    assert result.returncode == 0 and result.stderr == b""
    assert (
        FamilyDeliveryResult.from_payload(
            json.loads(result.stdout), recipient_count=1
        ).status
        is FamilyDeliveryStatus.SYSTEMIC
    )
