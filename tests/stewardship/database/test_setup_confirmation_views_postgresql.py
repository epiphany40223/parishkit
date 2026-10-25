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
        assert response["Location"] == "/admin/setup/finishing"
        assert post(browser, PATH, values).status_code == 302
        assert SetupAttempt.objects.get().state == "frozen"
        before = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        response = browser.get("/admin/setup/finishing")
        assert response.status_code == 200, response.content
        assert b"Finishing setup" in response.content
        assert b"Install the ParishSoft credential" in response.content
        assert b"Apply the configuration" in response.content
        # The operator's steps are in the runtime guide, not here (#2145):
        # one line says what happens next.
        assert b"actual consumer" not in response.content
        assert b"data-finishing-next" in response.content
        assert b"installing credentials needs a" not in response.content
        assert response.context["attempt"].attempt_id == attempt.attempt_id
        assert response.context["overall"] == "working"
        # The page's poll is a passive read of the same status.
        polled = browser.get("/admin/setup/finishing?format=json")
        assert polled.status_code == 200, polled.content
        assert polled.json() == {
            "overall": "working",
            "signature": response.context["signature"],
        }
        # The page's live status region is the same passive read (#1354).
        region = browser.get("/admin/setup/finishing?format=status")
        assert region.status_code == 200, region.content
        assert region["Cache-Control"] == "no-store"
        assert b'data-live-status="setup-finishing"' in region.content
        assert b"data-live-pending" in region.content
        assert b"Install the ParishSoft credential" in region.content
        assert b'name="csrfmiddlewaretoken" value=""' in region.content
        assert (
            b'data-live-url="/admin/setup/finishing?format=status"' in response.content
        )
        assert browser.get("/admin/setup/finishing?format=xml").status_code == 400
        assert browser.get("/admin/setup/finishing?other=1").status_code == 400
        # The page's old address is gone, with no redirect (#1903, #864).
        assert browser.get("/admin/setup/cancel").status_code == 404
        # The setup overview sends a confirmed setup's owner to this page.
        overview = browser.get("/admin/setup")
        assert overview.status_code == 302
        assert overview["Location"] == "/admin/setup/finishing"
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == before
        )
    assert not setup_http.configured()


def test_finishing_page_keeps_installing_after_an_old_sign_in(
    setup_http, monkeypatch, tmp_path
):
    """An old original sign-in asks for nothing: installation goes on (#1547)."""
    from datetime import timedelta

    request, _, token = prepared(setup_http, monkeypatch, tmp_path)
    browser = client_for(request)
    with web_login():
        browser.get(PATH)
        values = {"preview_token": token, "confirmed": "on"}
        assert post(browser, PATH, values).status_code == 302
    # SQL only lets a sign-in instant advance, so let ten minutes pass on
    # the status reader's clock instead of ageing the session.
    from parishkit.stewardship.accounts import setup_finalization_status

    later = setup_finalization_status.database_now() + timedelta(minutes=10)
    monkeypatch.setattr(setup_finalization_status, "database_now", lambda: later)
    with web_login():
        response = browser.get("/admin/setup/finishing")
        assert response.status_code == 200, response.content
        body = response.content.decode()
        assert "needs a Google sign-in" not in body
        assert "reauthenticate" not in response.context
        # The polled region still follows installation, with no sign-in notice.
        region = browser.get("/admin/setup/finishing?format=status").content.decode()
        assert "Install the ParishSoft credential" in region
        assert "setup-step-up" not in region


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
