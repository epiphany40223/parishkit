"""Completion races never leak predecessor-only progress or bypass current policy."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts import setup_cancellation_views as views


@pytest.mark.parametrize("boundary", ["first", "second"])
@pytest.mark.parametrize("authorized", [False, True])
def test_progress_completion_race_rechecks_current_admin(
    monkeypatch, boundary, authorized
):
    """Either protected read may lose setup ownership to the completed transaction."""
    status = object()
    service = SimpleNamespace(configured=lambda: True, store=object())
    request = object()

    def completed(*args):
        """Model the existing owner refusing progress after atomic completion."""
        raise PermissionError("Completed setup cannot be cancelled.")

    monkeypatch.setattr(
        views,
        "finalization_status",
        completed if boundary == "first" else lambda *args: {"attempt": status},
    )
    monkeypatch.setattr(views, "render", lambda *args: object())
    monkeypatch.setattr(views, "cancellation_status", completed)
    principals = []

    def authenticate(actual, *, store):
        """The redirect must authenticate against the real current store."""
        assert actual is request and store is service.store
        principals.append(True)
        return object()

    monkeypatch.setattr(views, "authenticated_admin", authenticate)
    monkeypatch.setattr(views, "allows", lambda *args: authorized)
    if authorized:
        response = views._progress(request, service)
        assert response.status_code == 302
        assert response["Location"] == "/admin/"
        assert response["Cache-Control"] == "no-store"
    else:
        with pytest.raises(PermissionError, match="Administrator"):
            views._progress(request, service)
    assert principals == [True]


def test_unconfigured_progress_denial_is_not_a_completion_handoff(monkeypatch):
    """An expired or unrelated original login cannot be relabelled as success."""

    def denied(*args):
        """Preserve the original permission failure without a second admission."""
        raise PermissionError("Original login expired.")

    monkeypatch.setattr(views, "finalization_status", denied)
    with pytest.raises(PermissionError, match="Original login expired"):
        views._progress(object(), SimpleNamespace(configured=lambda: False))
