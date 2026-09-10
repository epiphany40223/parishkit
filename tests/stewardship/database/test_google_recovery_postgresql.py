"""Actual Google HTTP boundaries around rename, deactivation and offline recovery."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db.models import F
from django.test import Client
from django.utils import timezone

from parishkit.stewardship.accounts.models import (
    PortalSession,
    PortalUser,
    SystemConfiguration,
)
from parishkit.stewardship.accounts.operator_recovery import recover_admin

from .auth_builders import signed_in, start
from .campaign_builders import restored_runtime

pytestmark = pytest.mark.django_db(transaction=True)


def recover(store):
    """No online process owns this disposable database; exercise the real ledger."""

    @contextmanager
    def offline():
        """The future OPS process interlock is outside this HTTP-boundary fixture."""
        yield

    return recover_admin(
        store,
        operation_id=uuid4(),
        operator_name="Synthetic operator",
        reason="Synthetic lost administration access",
        deployment_id=SystemConfiguration.objects.get().pk,
        target_email="replacement@example.org",
        confirmed_email="replacement@example.org",
        correlation_id=uuid4(),
        offline_interlock=offline,
    )


def test_verified_rename_rechecks_rules_without_rebinding_subject(auth_service, google):
    """The same Google subject cannot retain a grant attached to its former email."""
    browser, _ = signed_in()
    original = PortalUser.objects.get()
    google[0]["email"] = "renamed@example.org"
    _, denied = signed_in()
    assert denied.status_code == 403
    original.refresh_from_db()
    assert original.email == "renamed@example.org"
    assert original.google_subject == "synthetic-google-subject"
    assert PortalUser.objects.count() == 1
    assert browser.get("/admin/").status_code == 302
    assert not PortalSession.objects.filter(revoked_at__isnull=True).exists()


def test_recovery_revokes_old_sessions_and_oauth_but_mints_no_new_session(
    auth_service, google
):
    """An operator repairs a rule, not a Google identity, token or web session."""
    browser, _ = signed_in()
    prior_user = PortalUser.objects.get()
    pending = Client(enforce_csrf_checks=True)
    query = start(pending)
    calls = len(google[1])
    assert recover(auth_service.store).state == "applied"
    assert not PortalSession.objects.filter(revoked_at__isnull=True).exists()
    assert PortalUser.objects.count() == 1
    assert browser.get("/admin/").status_code == 302
    rejected = pending.get(
        "/admin/oauth/callback", {"state": query["state"][0], "code": "synthetic"}
    )
    assert rejected.status_code == 403
    assert len(google[1]) == calls
    google[0].update(
        email="replacement@example.org", sub="synthetic-replacement-google-subject"
    )
    replacement, response = signed_in()
    assert response.status_code == 302
    assert replacement.get("/admin/").status_code == 200
    assert PortalUser.objects.count() == 2
    prior_user.refresh_from_db()
    assert prior_user.google_subject == "synthetic-google-subject"
    assert prior_user.email == "admin@example.org"


def test_disabled_sole_admin_requires_an_independently_authenticated_replacement(
    auth_service, google
):
    """Even fresh signed claims cannot revive a locally disabled portal identity."""
    signed_in()
    original = PortalUser.objects.get()
    PortalUser.objects.filter(pk=original.pk).update(
        disabled=True, version=F("version") + 1
    )
    assert signed_in()[1].status_code == 403
    assert recover(auth_service.store).state == "applied"
    assert signed_in()[1].status_code == 403
    google[0].update(
        email="replacement@example.org", sub="synthetic-replacement-google-subject"
    )
    assert signed_in()[1].status_code == 302
    original.refresh_from_db()
    assert original.disabled


def test_google_after_recovery_does_not_release_restore_maintenance(
    auth_service, google
):
    """Credential recovery must not clear the independent restored-data safety gate."""
    with restored_runtime(timezone.now() - timedelta(hours=1)):
        assert recover(auth_service.store).state == "applied"
        google[0].update(
            email="replacement@example.org", sub="synthetic-replacement-google-subject"
        )
        browser, response = signed_in()
        assert response.status_code == 302
        response = browser.get("/admin/")
        assert response.status_code == 302
        assert response["Location"] == "/admin/maintenance"
        denied = browser.get("/admin/maintenance")
        assert denied.status_code == 503
        assert b"/admin/login" in denied.content
        assert SystemConfiguration.objects.get().restore_review_required
