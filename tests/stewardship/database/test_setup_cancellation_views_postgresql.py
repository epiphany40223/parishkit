"""The exceptional cancellation page retains real sessions, CSRF and web grants."""

# Shared fixtures are deliberately injected by pytest under their imported names.
# ruff: noqa: F811

import pytest
from django.test import Client

from parishkit.stewardship.accounts.setup_models import (
    SetupAttempt,
    SetupConfigurationAbort,
)

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_configuration_postgresql import selected
from .test_setup_staging_postgresql import login
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def client_for(request):
    """Reuse a genuinely issued original session, never inject a principal UUID."""
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    return browser


def test_original_browser_can_cancel_selected_yaml_with_actual_web_grants(
    setup_http,
):
    """GET is passive, POST records expiry, and neither action restores files."""
    request, attempt, _, candidate = selected(setup_http)
    browser = client_for(request)
    with web_login():
        response = browser.get("/admin/setup/cancel")
        assert response.status_code == 200, response.content
        assert b"Cancel initial setup" in response.content
        assert b"Final setup must complete before these deadlines" in response.content
        assert response.context["deadlines"].session_id == attempt.session_id
        assert (
            response.context["deadlines"].idle_at.isoformat().encode()
            in response.content
        )
        assert b"Example Parish" not in response.content
        assert response["Cache-Control"] == "no-store"
        assert SetupAttempt.objects.get(pk=attempt.pk).state == "frozen"
        assert browser.get("/admin/setup/parish").status_code == 503
        assert (
            browser.post(
                "/admin/setup/cancel", {"attempt": str(attempt.pk)}
            ).status_code
            == 403
        )
        assert SetupAttempt.objects.get(pk=attempt.pk).state == "frozen"
        response = post(browser, "/admin/setup/cancel", {"attempt": str(attempt.pk)})
        assert response.status_code == 302, response.content
        assert response["Location"] == "/admin/setup/cancel"
        assert SetupAttempt.objects.get(pk=attempt.pk).state == "expired"
        assert b"Cancellation is recorded" in browser.get("/admin/setup/cancel").content
        assert not SetupConfigurationAbort.objects.exists()
    assert setup_http.store.active() == candidate


def test_anonymous_and_another_admin_session_cannot_use_cancellation_exception(
    setup_http,
):
    """The middleware exception does not itself authenticate or disclose staging."""
    another = login(setup_http)
    _, attempt, _, _ = selected(setup_http)
    with web_login():
        assert Client().get("/admin/setup/cancel").status_code == 403
        response = client_for(another).get("/admin/setup/cancel")
        assert response.status_code == 404
        assert str(attempt.pk).encode() not in response.content
        assert SetupAttempt.objects.get(pk=attempt.pk).state == "frozen"
