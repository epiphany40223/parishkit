"""Expired previews become a plain, recoverable refusal (#200)."""

from types import SimpleNamespace

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


def test_a_five_minute_preview_expires_on_its_own_lifetime(monkeypatch):
    """``max_age`` keeps each preview's lifetime; expiry is the same refusal."""
    token = signing.dumps({"a": 1}, salt="salt")
    now = signing.time.time()
    monkeypatch.setattr(signing.time, "time", lambda: now + 301)
    # Still inside the default fifteen minutes...
    assert refusals.load_preview(token, salt="salt") == {"a": 1}
    # ...but past the five-minute control lifetime.
    with pytest.raises(StaleRecordError) as caught:
        refusals.load_preview(
            token,
            salt="salt",
            link="/admin/go-live",
            max_age=refusals.CONTROL_PREVIEW_MAX_AGE,
        )
    refusal = caught.value.refusal.as_dict()
    assert refusal["message"] == "This preview is out of date."
    assert refusal["link"]["url"] == "/admin/go-live"


def test_a_tampered_five_minute_preview_stays_a_bad_signature():
    """A forged control token is never mistaken for an expired one."""
    with pytest.raises(signing.BadSignature) as caught:
        refusals.load_preview(
            "not-a-token", salt="salt", max_age=refusals.CONTROL_PREVIEW_MAX_AGE
        )
    assert not isinstance(caught.value, signing.SignatureExpired)


def test_return_link_is_the_posted_page_or_none():
    """A web request links back to its page; a command-line caller has none."""
    assert refusals.return_link(SimpleNamespace(path="/admin/go-live")) == (
        "/admin/go-live"
    )
    assert refusals.return_link(object()) is None
