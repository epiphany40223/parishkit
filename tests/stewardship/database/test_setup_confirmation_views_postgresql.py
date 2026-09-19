"""Explicit final confirmation retains exact preview, original login and CSRF."""

# ruff: noqa: F811 -- imported pytest fixtures.

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_models import SetupAttempt

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_cancellation_views_postgresql import client_for
from .test_setup_confirmation_postgresql import prepared
from .test_setup_mail_views_postgresql import web_login
from .test_setup_preview_postgresql import complete_draft
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
PATH = "/admin/setup/confirm"


def test_final_confirmation_freezes_once_then_shows_passive_original_progress(
    setup_http, monkeypatch, tmp_path
):
    """Neither review nor progress renews the session or claims completed setup."""
    request, attempt, token = prepared(setup_http, monkeypatch, tmp_path)
    browser = client_for(request)
    with web_login():
        before = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        response = browser.get(PATH)
        assert response.status_code == 200, response.content
        assert response.context["readiness_problem"] is None
        assert response["Cache-Control"] == "no-store"
        assert b"synthetic-private" not in response.content
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == before
        )
        values = {"preview_token": token, "confirmed": "on"}
        assert browser.post(PATH, values).status_code == 403
        assert SetupAttempt.objects.get().state == "collecting"
        response = post(browser, PATH, values)
        assert response.status_code == 302, response.content
        assert response["Location"] == "/admin/setup/cancel"
        assert post(browser, PATH, values).status_code == 302
        assert SetupAttempt.objects.get().state == "frozen"
        before = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        response = browser.get("/admin/setup/cancel")
        assert response.status_code == 200, response.content
        assert b"Initial setup progress" in response.content
        assert b"actual consumer" in response.content
        assert response.context["attempt"].attempt_id == attempt.attempt_id
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == before
        )
    assert not setup_http.configured()


def test_missing_readiness_disables_ui_and_server_refuses_forced_confirmation(
    setup_http, monkeypatch, tmp_path
):
    """A signed valid configuration preview is not successful provider readiness."""
    request, _, _ = complete_draft(setup_http, monkeypatch, tmp_path)
    browser = client_for(request)
    with web_login():
        response = browser.get(PATH)
        assert response.status_code == 200, response.content
        assert response.context["readiness_problem"]
        assert b"disabled" in response.content
        token = response.context["form"]["preview_token"].value()
        response = post(browser, PATH, {"preview_token": token, "confirmed": "on"})
        assert response.status_code == 400, response.content
        assert SetupAttempt.objects.get().state == "collecting"


def test_confirmation_requires_explicit_exact_closed_input(
    setup_http, monkeypatch, tmp_path
):
    """One unchanged setup proves every rejected input leaves confirmation open."""
    request, _, token = prepared(setup_http, monkeypatch, tmp_path)
    browser = client_for(request)
    with web_login():
        assert browser.get(PATH).status_code == 200
        # All three requests must reject before any setup effect. Keep the real
        # owner, SQL and HTTP checks; only avoid bootstrapping them three times.
        for invalid in ("acknowledgement", "signature", "unexpected"):
            values = {"preview_token": token, "confirmed": "on"}
            if invalid == "acknowledgement":
                values.pop("confirmed")
            elif invalid == "signature":
                values["preview_token"] += "changed"
            else:
                values["target"] = "another-target"
            assert post(browser, PATH, values).status_code == 400, invalid
            assert SetupAttempt.objects.get().state == "collecting", invalid
        # Rejections must not poison the shared fixture or invalidate its token.
        assert (
            post(browser, PATH, {"preview_token": token, "confirmed": "on"}).status_code
            == 302
        )
        assert SetupAttempt.objects.get().state == "frozen"


def test_invalid_confirmation_renders_fresh_binding_then_accepts_explicit_retry(
    setup_http, monkeypatch, tmp_path
):
    """An old hidden token cannot trap a missing-checkbox response in a stale loop."""
    from django.core import signing

    from parishkit.stewardship.accounts.setup_preview import PREVIEW_SALT

    request, _, token = prepared(setup_http, monkeypatch, tmp_path)
    stale = signing.loads(token, salt=PREVIEW_SALT) | {"draft": "old-reviewed-draft"}
    browser = client_for(request)
    with web_login():
        assert browser.get(PATH).status_code == 200
        response = post(
            browser, PATH, {"preview_token": signing.dumps(stale, salt=PREVIEW_SALT)}
        )
        assert response.status_code == 400
        form = response.context["form"]
        assert "confirmed" in form.errors
        refreshed = form["preview_token"].value()
        assert signing.loads(refreshed, salt=PREVIEW_SALT) == signing.loads(
            token, salt=PREVIEW_SALT
        )
        assert (
            post(
                browser, PATH, {"preview_token": refreshed, "confirmed": "on"}
            ).status_code
            == 302
        )
    assert SetupAttempt.objects.get().state == "frozen"
