"""An ended Admin session is reported as signed out, not as a missing capability."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts import admin_editing
from parishkit.stewardship.web.refusals import UserFacingDenied


def test_a_request_without_a_current_session_is_told_to_sign_in(monkeypatch):
    """Idle timeout or sign-out elsewhere yields a sign-in link, not a role error."""
    monkeypatch.setattr(admin_editing, "authenticated_admin", lambda *a, **k: None)
    with pytest.raises(UserFacingDenied) as raised:
        admin_editing.principal(object(), SimpleNamespace(store=None), passive=True)
    refusal = raised.value.refusal
    assert "sign-in has ended" in str(refusal.message)
    assert refusal.link == "/admin/login"
    # Still a PermissionError, so every existing handler keeps its 403.
    assert isinstance(raised.value, PermissionError)


def test_a_signed_in_account_without_the_capability_is_still_refused(monkeypatch):
    """A current Staff session keeps the ordinary capability refusal."""
    monkeypatch.setattr(admin_editing, "authenticated_admin", lambda *a, **k: object())
    monkeypatch.setattr(admin_editing, "allows", lambda *a, **k: False)
    with pytest.raises(PermissionError) as raised:
        admin_editing.principal(object(), SimpleNamespace(store=None))
    assert not isinstance(raised.value, UserFacingDenied)
