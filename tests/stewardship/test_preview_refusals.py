"""Expired previews become a plain, recoverable refusal (#200)."""

import pytest
from django.core import signing

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web import refusals


def test_expired_preview_is_a_stale_refusal_with_a_way_back(monkeypatch):
    """Expiry keeps its 409 type but explains itself and links back to review."""
    token = signing.dumps({"a": 1}, salt="salt")

    def expired(*args, **kwargs):
        raise signing.SignatureExpired("expired")

    monkeypatch.setattr(signing, "loads", expired)
    with pytest.raises(StaleRecordError) as caught:
        refusals.load_preview(token, salt="salt", link="/admin/parish")
    refusal = caught.value.refusal.as_dict()
    assert refusal["message"] == "This preview is out of date."
    assert refusal["link"] == {
        "url": "/admin/parish",
        "label": "Review the changes again",
    }


def test_tampered_preview_is_still_a_generic_bad_signature():
    """Only genuine expiry is recoverable; a forged token stays a plain 400."""
    with pytest.raises(signing.BadSignature) as caught:
        refusals.load_preview("not-a-token", salt="salt", link="/admin/parish")
    assert not isinstance(caught.value, signing.SignatureExpired)


def test_valid_preview_round_trips():
    """A fresh preview loads exactly as signed."""
    token = signing.dumps({"a": 1}, salt="salt")
    assert refusals.load_preview(token, salt="salt") == {"a": 1}


def test_expired_preview_without_link_still_explains_the_fix():
    """Callers without a page to return to still give the plain fix sentence."""
    refusal = refusals.expired_preview().refusal.as_dict()
    assert refusal["link"] is None
    assert refusal["fix"] == "Review the changes again, then confirm."
