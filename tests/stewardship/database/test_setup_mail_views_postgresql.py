"""Original-owner mail UI with real CSRF, restricted SQL and passive polling."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from functools import partial

import pytest
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_mail import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from ..test_setup_forms import VALUES
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_mail_postgresql import claim
from .test_setup_preview_postgresql import complete_draft
from .test_setup_staging_postgresql import login
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
PATH = "/admin/setup/mail-test"
# Error middleware can close its SQL connection; the replacement must retain
# the actual web role instead of silently reconnecting as the fixture owner.
web_login = partial(task_login, ServiceRole.WEB, exact=True, reconnect=True)


def browser_for(service, monkeypatch, tmp_path):
    """Prepare the complete wizard before using the real request/response paths."""
    request, attempt, _ = complete_draft(service, monkeypatch, tmp_path)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    return browser, request, attempt


def values(response):
    """Read actual signed form values instead of fabricating preview authority."""
    form = response.context["form"]
    return {
        name: form[name].value() for name in ("preview_token", "request_key", "slot")
    }


def test_explicit_send_redirects_and_passive_polling_does_not_renew_login(
    setup_http, monkeypatch, tmp_path
):
    """GET exposes no private key; CSRF POST queues one test without provider IO."""
    browser, request, _ = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        response = browser.get(PATH)
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        assert b"synthetic-private" not in response.content
        assert not SetupMailDelivery.objects.exists()
        payload = values(response)
        assert browser.post(PATH, payload).status_code == 403
        response = post(browser, PATH, payload)
        assert response.status_code == 302 and response["Location"] == PATH
        assert post(browser, PATH, payload).status_code == 302
        assert SetupMailDelivery.objects.count() == 1
        activity = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        status = browser.get(PATH + "/status")
        assert status.status_code == 200 and status["Cache-Control"] == "no-store"
        data = status.json()
        assert data["pending"] and not data["unknown"]
        assert data["items"][0]["state"] == "queued"
        assert set(data["items"][0]) == {
            "id",
            "state",
            "label",
            "created_at",
            "current",
        }
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == activity
        )
        assert browser.get(PATH).status_code == 200
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == activity
        )
    assert not setup_http.configured()


def test_modified_preview_and_unexpected_recipient_fields_are_rejected(
    setup_http, monkeypatch, tmp_path
):
    """Clients can choose only a named sample, not a credential, body or recipient."""
    browser, request, attempt = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        payload = values(browser.get(PATH))
        for extra in (
            {"recipient": "different@example.org"},
            {"candidate": "synthetic-private"},
            {"slot": ["initial", "reminder"]},
            {"preview_token": "invalid"},
            {"slot": "unknown"},
        ):
            response = post(browser, PATH, payload | extra)
            assert response.status_code == 400, response.content
            assert b"synthetic-private" not in response.content
        assert not SetupMailDelivery.objects.exists()
        save_section(
            request,
            setup_http,
            attempt.attempt_id,
            step="parish",
            values=VALUES["parish"] | {"name": "Updated Parish"},
            expected_version=attempt.version,
        )
        assert post(browser, PATH, payload).status_code == 409
        assert browser.get(PATH + "/status?recipient=other").status_code == 400


def test_unknown_delivery_requires_explicit_visible_resend_acknowledgement(
    setup_http, monkeypatch, tmp_path
):
    """A new request after uncertainty is deliberate; replaying an old POST is inert."""
    from parishkit.stewardship.accounts.setup_mail import _status

    browser, _, _ = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        payload = values(browser.get(PATH))
        assert post(browser, PATH, payload).status_code == 302
        delivery = _status(SetupMailDelivery.objects.get())
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        begin_submission(delivery.identifier, owner)
        finish_submission(delivery.identifier, owner, DeliveryOutcome.UNKNOWN)
    with web_login():
        response = browser.get(PATH)
        assert response.context["unknown"] and not response.context["pending"]
        next_payload = values(response)
        assert post(browser, PATH, next_payload).status_code == 400
        assert (
            post(
                browser, PATH, next_payload | {"acknowledge_unknown": "on"}
            ).status_code
            == 302
        )
        assert SetupMailDelivery.objects.count() == 2


def test_other_login_and_cancelled_attempt_cannot_view_original_mail_status(
    setup_http, monkeypatch, tmp_path
):
    """An Admin role alone does not let a new login resume another wizard attempt."""
    browser, request, attempt = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        assert post(browser, PATH, values(browser.get(PATH))).status_code == 302
        other = Client()
        other.cookies["pk_admin"] = login(setup_http).session.session_key
        assert other.get(PATH + "/status").status_code == 404
        cancel_setup(request, setup_http, attempt.attempt_id)
        assert browser.get(PATH + "/status").status_code == 404
        assert SetupMailDelivery.objects.get().mail == {}
