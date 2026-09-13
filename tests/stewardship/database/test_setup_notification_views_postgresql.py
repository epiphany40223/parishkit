"""Slack readiness HTTP with real CSRF, restricted-role reconnects and passive GET."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from functools import partial
from unittest.mock import Mock

import pytest
from django.test import Client

from parishkit.stewardship.accounts import setup_notifications as notifications
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_notification_models import SetupSlackDelivery
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_setup_exchange_postgresql import target_login
from .test_setup_notifications_postgresql import ready
from .test_setup_staging_postgresql import login
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
PATH = "/admin/setup/slack-test"
web_login = partial(task_login, ServiceRole.WEB, exact=True, reconnect=True)


def browser_for(service, monkeypatch, tmp_path):
    """Complete the actual wizard before entering its original-login HTTP flow."""
    request, attempt, _, _, _ = ready(service, monkeypatch, tmp_path, enqueue=False)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    return browser, request, attempt


def values(response):
    """Extract the real signed binding and idempotency key from rendered fields."""
    form = response.context["form"]
    return {name: form[name].value() for name in ("preview_token", "request_key")}


def test_explicit_csrf_send_and_passive_status_do_not_extend_idle_expiry(
    setup_http, monkeypatch, tmp_path
):
    """HTTP enqueues tests; it cannot choose private input or mark success."""
    browser, request, _ = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        response = browser.get(PATH)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        payload = values(response)
        assert browser.post(PATH, payload).status_code == 403
        assert post(browser, PATH, payload).status_code == 302
        assert post(browser, PATH, payload).status_code == 302
        assert SetupSlackDelivery.objects.count() == 1
        activity = PortalSession.objects.get(
            pk=request.portal_session.pk
        ).last_activity_at
        response = browser.get(PATH + "/status")
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        data = response.json()
        assert data["pending"] and not data["unknown"]
        assert data["items"][0]["state"] == "queued"
        assert "synthetic-private" not in response.content.decode()
        assert browser.get(PATH).status_code == 200
        assert (
            PortalSession.objects.get(pk=request.portal_session.pk).last_activity_at
            == activity
        )


def test_unowned_fields_bad_signatures_and_duplicate_keys_are_rejected(
    setup_http, monkeypatch, tmp_path
):
    """The UI does not accept an arbitrary channel, text or candidate."""
    browser, _, _ = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        payload = values(browser.get(PATH))
        for extra in (
            {"channel_id": "COTHER"},
            {"text": "private"},
            {"candidate": "private"},
            {"preview_token": "invalid"},
            {"request_key": [payload["request_key"], payload["request_key"]]},
        ):
            assert post(browser, PATH, payload | extra).status_code == 400
        assert not SetupSlackDelivery.objects.exists()
        assert browser.get(PATH + "/status?channel=other").status_code == 400


def test_uncertainty_is_visible_and_requires_new_acknowledged_command(
    setup_http, monkeypatch, tmp_path
):
    """Server-side acknowledgement remains mandatory even without JavaScript."""
    browser, _, _ = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        assert post(browser, PATH, values(browser.get(PATH))).status_code == 302
    monkeypatch.setattr(
        notifications, "submit_notification", Mock(return_value=DeliveryOutcome.UNKNOWN)
    )
    with target_login("slack", reconnect=True):
        notifications.run_pending(key("slack", material=b"n" * 32), check=lambda: None)
    with web_login():
        response = browser.get(PATH)
        assert response.context["unknown"] and not response.context["pending"]
        payload = values(response)
        assert post(browser, PATH, payload).status_code == 400
        assert (
            post(browser, PATH, payload | {"acknowledge_unknown": "on"}).status_code
            == 302
        )
        assert SetupSlackDelivery.objects.count() == 2


def test_different_login_and_cancelled_setup_cannot_read_original_status(
    setup_http, monkeypatch, tmp_path
):
    """An Admin account is not permission to resume a different login's wizard."""
    browser, request, attempt = browser_for(setup_http, monkeypatch, tmp_path)
    with web_login():
        other = Client()
        other.cookies["pk_admin"] = login(setup_http).session.session_key
        assert other.get(PATH + "/status").status_code == 404
        cancel_setup(request, setup_http, attempt.attempt_id)
        assert browser.get(PATH + "/status").status_code == 404
