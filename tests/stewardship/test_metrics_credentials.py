"""Metrics receipts are independent non-secret versions, not token hashes."""

import hashlib
import json

import pytest

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.metrics_credentials import (
    MetricsCredential,
    credential_receipt,
)


def test_metrics_version_round_trip_and_private_representation():
    credential = MetricsCredential.generate()
    value = credential.serialize()
    assert MetricsCredential.parse(value) == credential
    assert credential_receipt(value, "metrics") == credential.receipt
    assert credential.receipt != hashlib.sha256(credential.token).hexdigest()
    assert credential.receipt != hashlib.sha256(value).hexdigest()
    assert credential.token.decode() not in repr(credential)
    assert credential_receipt(value, "slack") == hashlib.sha256(value).hexdigest()
    assert MetricsCredential.generate() != credential


@pytest.mark.parametrize("value", [None, b"", b"private-token", b"[]", b"x" * 1025])
def test_invalid_metrics_document_has_no_raw_token_fallback(value):
    with pytest.raises(CryptographicError) as error:
        MetricsCredential.parse(value)
    assert "private-token" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"kind": "other"},
        {"receipt": "x" * 64},
        {"receipt": True},
        {"token": 1},
        {"token": "x" * 42},
        {"token": "é" * 43},
        {"extra": "private-token"},
    ],
)
def test_metrics_schema_rejects_ambiguous_or_malformed_fields(change):
    value = json.loads(MetricsCredential.generate().serialize()) | change
    with pytest.raises(CryptographicError):
        MetricsCredential.parse(json.dumps(value).encode())


def test_metrics_schema_rejects_duplicate_fields():
    value = MetricsCredential.generate().serialize()
    with pytest.raises(CryptographicError):
        MetricsCredential.parse(value[:-1] + b',"version":1}')
