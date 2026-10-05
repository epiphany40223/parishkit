"""Shared builders for the Admin automation PostgreSQL suites (ADM-11).

A signed-in browser supplies the real, freshly signed-in Admin session an
approval needs; the pairing request lives in the test's own Valkey
namespace; approval runs through the service layer (the approval page has
its own suite), and commands are admitted through
``AdminCaller.from_automation`` exactly as the command line admits them.
"""

import secrets

import pytest
from django.db import transaction

from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.admin_caller import AdminCaller
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationSession,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.policy_models import PortalUser

from ..policy_factory import address
from .auth_builders import auth_runtime, signed_in

HOST = "a" * 64
OTHER_HOST = "b" * 64


@pytest.fixture
def two_admins(tmp_path, settings, real_limiter):
    """An initial policy with two Administrators and one Staff member."""
    return auth_runtime(
        tmp_path,
        settings,
        real_limiter,
        records=[
            address(),
            address("other@example.org"),
            address("staff@example.org", roles=("staff",)),
        ],
    )


def pairing(service):
    """The pairing store in this test's own Valkey namespace."""
    return automation.PairingStore(service.limiter.client, service.limiter.namespace)


def start(service, *, secret=None, scope="full", days=30, email="admin@example.org"):
    """Start a pairing as ``login start`` does; returns (secret, code)."""
    secret = secret or secrets.token_urlsafe(32)
    request = automation.pairing_request(
        digest=automation.secret_digest(secret),
        host_digest=HOST,
        name="ops",
        label="launch <assistant>",
        expect_email=email,
        scope=scope,
        days=days,
    )
    return secret, pairing(service).start(request)


def post(browser, path, **fields):
    """A CSRF-protected form post from a signed-in browser."""
    return browser.post(
        path, {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value, **fields}
    )


def browser_session(email="admin@example.org"):
    """The Administrator's live browser session (never a command session)."""
    return (
        PortalSession.objects.filter(
            revoked_at__isnull=True,
            principal_id=PortalUser.objects.get(email=email).pk,
        )
        .exclude(pk__in=AutomationLogin.objects.values("portal_session_id"))
        .latest("created_at")
    )


def approve_now(service, code, *, scope="full", days=30, email="admin@example.org"):
    """Approve the pending pairing as that Administrator, through the service."""
    portal = browser_session(email)
    principal = current_principal(service.store, portal.principal_id)
    request = pairing(service).request(code)
    with transaction.atomic():
        return automation.approve(principal, portal, request, scope=scope, days=days)


def paired(service, **options):
    """A browser and an approved session; returns (browser, secret, row).

    ``scope`` is the command line's word (``full`` or ``read-only``).
    """
    browser, _ = signed_in()
    secret, code = start(service, **options)
    scope = {"read-only": "read_only"}.get(options.get("scope"), "full")
    approve_now(service, code, scope=scope, days=options.get("days", 30))
    row = AutomationSession.objects.get(secret_digest=automation.secret_digest(secret))
    return browser, secret, row


def command(service, secret, host=HOST):
    """Admit one command as the command line does."""
    return AdminCaller.from_automation(
        secret, host, store=service.store, pairing=pairing(service)
    )
