"""The shared Admin inactivity dialog's passive status and explicit renewal."""

# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.

from datetime import datetime, timedelta

import pytest
from django.db import connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.session_policy import ADMIN_IDLE
from parishkit.stewardship.accounts.setup_models import SetupAttempt

from ..test_setup_forms import VALUES
from .auth_builders import signed_in, unguarded
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

STATUS = "/admin/session/status"
RENEW = "/admin/session/renew"


def backdate(delta):
    """Make the one Admin session look ``delta`` idle, as if time had passed.

    The session guard only lets sign-in and activity move forward, so the test
    briefly disables the table's triggers (as the test superuser, restoring any
    runtime login in use) and moves sign-in, activity and creation back
    together. The absolute limit stays put.
    """
    row = PortalSession.objects.get(revoked_at__isnull=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        (login,) = cursor.fetchone()
        cursor.execute("RESET SESSION AUTHORIZATION")
        with transaction.atomic():
            cursor.execute(
                "ALTER TABLE stewardship_portal_session DISABLE TRIGGER USER"
            )
            cursor.execute(
                "UPDATE stewardship_portal_session SET created_at=created_at-%s, "
                "authenticated_at=authenticated_at-%s, "
                "last_activity_at=last_activity_at-%s WHERE id=%s",
                [delta, delta, delta, row.pk],
            )
            cursor.execute("ALTER TABLE stewardship_portal_session ENABLE TRIGGER USER")
        cursor.execute("SELECT current_user")
        if cursor.fetchone()[0] != login:
            cursor.execute(f'SET SESSION AUTHORIZATION "{login}"')
    row.refresh_from_db()
    return row


def instant(value):
    """Parse one ISO instant from the JSON response."""
    return datetime.fromisoformat(value)


def test_status_is_passive_and_renewal_counts_as_activity(auth_service, google):
    """Reading deadlines renews nothing; the CSRF-checked POST renews idle only."""
    browser, _ = signed_in()
    row = backdate(timedelta(minutes=40))
    status = browser.get(STATUS)
    assert status.status_code == 200 and status["Cache-Control"] == "no-store"
    data = status.json()
    assert instant(data["idle_deadline"]) == row.last_activity_at + ADMIN_IDLE
    assert instant(data["absolute_deadline"]) == row.expires_at
    assert PortalSession.objects.get(pk=row.pk).last_activity_at == row.last_activity_at
    # Without the CSRF token the renewal is refused and nothing changes.
    assert browser.post(RENEW).status_code == 403
    assert PortalSession.objects.get(pk=row.pk).last_activity_at == row.last_activity_at
    renewed = post(browser, RENEW, {})
    assert renewed.status_code == 200, renewed.content
    after = PortalSession.objects.get(pk=row.pk)
    assert after.last_activity_at > row.last_activity_at
    assert after.expires_at == row.expires_at
    data = renewed.json()
    assert instant(data["idle_deadline"]) == after.last_activity_at + ADMIN_IDLE
    assert instant(data["absolute_deadline"]) == row.expires_at


def test_renewal_never_extends_the_absolute_limit(auth_service, google):
    """Near the absolute limit, renewal reports that limit as the idle deadline."""
    browser, _ = signed_in()
    row = PortalSession.objects.get(revoked_at__isnull=True)
    # The deadline is immutable in SQL (#306); only the owner can seed it.
    with unguarded():
        PortalSession.objects.filter(pk=row.pk).update(
            expires_at=F("last_activity_at") + timedelta(minutes=3),
            version=F("version") + 1,
        )
    data = post(browser, RENEW, {}).json()
    after = PortalSession.objects.get(pk=row.pk)
    assert instant(data["absolute_deadline"]) == after.expires_at
    assert instant(data["idle_deadline"]) == after.expires_at


def test_idle_session_cannot_be_renewed(auth_service, google):
    """After the idle deadline, status reports signed out and renewal revokes."""
    browser, _ = signed_in()
    row = backdate(ADMIN_IDLE + timedelta(seconds=1))
    assert browser.get(STATUS).status_code == 401
    assert PortalSession.objects.get(pk=row.pk).revoked_at is None  # Passive.
    assert post(browser, RENEW, {}).status_code == 401
    assert PortalSession.objects.get(pk=row.pk).revoked_at is not None


def test_renewal_keeps_the_setup_attempt_alive(setup_http, google):
    """During setup the endpoints pass the gate, and setup idles out at 60 minutes."""
    with web_login():
        browser = started()
        # Forty-five minutes idle would have ended setup under the old limit.
        backdate(timedelta(minutes=45))
        assert browser.get(STATUS).status_code == 200
        renewed = post(browser, RENEW, {})
        assert renewed.status_code == 200, renewed.content
        backdate(timedelta(minutes=45))
        version = str(SetupAttempt.objects.get().version)
        saved = post(
            browser, "/admin/setup/parish", VALUES["parish"] | {"version": version}
        )
        assert saved.status_code == 302, saved.content
        assert SetupAttempt.objects.get().state == "collecting"
