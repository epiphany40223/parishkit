"""Private Family helper protocol rejects unbound input and ambiguous output."""

import base64
import json

import pytest

from parishkit.stewardship.family_delivery import (
    FamilyDeliveryMail,
    FamilyDeliveryResult,
)
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryStatus as Status,
)
from parishkit.stewardship.family_delivery_process import submit_family
from parishkit.stewardship.family_delivery_worker import decode_request
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_family_delivery import SETTINGS, sample


def request():
    """Serialize synthetic input exactly as the parent supplies its private pipe."""
    return {
        "candidate": base64.b64encode(b"synthetic-key").decode(),
        "settings": SETTINGS,
        "mail": sample().payload(),
    }


def test_private_request_roundtrip():
    """The helper preserves the closed envelope and has no arbitrary SMTP target."""
    value = request()
    key, settings, mail = decode_request(json.dumps(value).encode())
    assert key == b"synthetic-key" and settings == SETTINGS
    assert mail == FamilyDeliveryMail.from_payload(value["mail"])


@pytest.mark.parametrize(
    "mutation", ["extra", "candidate", "duplicate", "identity", "recipient", "sender"]
)
def test_invalid_request_never_reaches_network(mutation):
    """Duplicate JSON fields, other identities and foreign context fail closed."""
    value = request()
    if mutation == "extra":
        value["smtp_host"] = "untrusted.invalid"
    elif mutation == "candidate":
        value["candidate"] = "not-base64!"
    elif mutation == "identity":
        value["mail"]["semantic_key"] = value["mail"]["semantic_key"].replace("-", "")
    elif mutation == "recipient":
        value["mail"]["recipients"] = []
    elif mutation == "sender":
        value["mail"]["sender"] = "other@example.org"
    raw = json.dumps(value).encode()
    if mutation == "duplicate":
        raw = raw[:-1] + b', "candidate":"c3ludGhldGlj"}'
    with pytest.raises(ValueError):
        decode_request(raw)


@pytest.mark.parametrize("result", list(Status))
def test_parent_binds_closed_result_to_original_envelope(monkeypatch, result):
    """Return indices, never raw recipient addresses or provider error messages."""
    expected = FamilyDeliveryResult(result, 2, permanent=(0,))

    def exchange(payload, **kwargs):
        assert kwargs["helper"] == "family_delivery_worker"
        assert decode_request(payload)[2].recipients == sample().recipients
        return kwargs["decode"](json.dumps(expected.payload()).encode())

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", exchange
    )
    assert (
        submit_family(b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None)
        == expected
    )


@pytest.mark.parametrize(
    "output",
    [
        b"not-json",
        b"[]",
        b"{}",
        b"null",
        b"\xff",
        b'{"status":"accepted","recipient_count":1,"permanent":[],"transient":[]}',
        b'{"status":"accepted","recipient_count":2,"permanent":[0,1],"transient":[]}',
        b'{"status":"accepted","recipient_count":2,"permanent":[],"transient":[],"secret":"private"}',
        b'{"status":"accepted","status":"transient","recipient_count":2,"permanent":[],"transient":[]}',
    ],
)
def test_malformed_reply_is_unknown_not_retryable(monkeypatch, output):
    """A corrupt acknowledgement cannot authorize an automatic duplicate send."""
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private",
        lambda payload, **kwargs: kwargs["decode"](output),
    )
    result = submit_family(
        b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result == FamilyDeliveryResult(Status.UNKNOWN, 2)


@pytest.mark.parametrize(
    "transport,status",
    [
        (DeliveryOutcome.NOT_SENT, Status.TRANSIENT),
        (DeliveryOutcome.UNKNOWN, Status.UNKNOWN),
    ],
)
def test_missing_helper_and_lost_acknowledgement_differ(monkeypatch, transport, status):
    """Only a proven helper-launch failure can become a safe ordinary retry."""
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private",
        lambda *args, **kwargs: transport,
    )
    assert (
        submit_family(
            b"synthetic", SETTINGS, sample(), seconds=5, check=lambda: None
        ).status
        is status
    )


@pytest.mark.parametrize("mutation", ["settings", "size", "seconds", "candidate"])
def test_local_validation_failure_never_becomes_uncertain(monkeypatch, mutation):
    """All validation here precedes process launch and cannot imply acceptance."""

    def forbidden(*args, **kwargs):
        raise AssertionError("A private helper was launched")

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", forbidden
    )
    if mutation == "size":
        monkeypatch.setattr(
            "parishkit.stewardship.family_delivery_process.MAX_INPUT", 1
        )
    result = submit_family(
        b"" if mutation == "candidate" else b"synthetic",
        SETTINGS | ({"extra": "private"} if mutation == "settings" else {}),
        sample(),
        seconds=0 if mutation == "seconds" else 5,
        check=lambda: None,
    )
    assert result == FamilyDeliveryResult(
        Status.PERMANENT if mutation == "size" else Status.SYSTEMIC, 2
    )
