"""Review regressions exercise real SQL, signed sessions and pre-header admission."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.secret_requests import stage_secret_request
from parishkit.stewardship.campaigns.credential_models import (
    RehearsalCodeFingerprint,
    RehearsalCodeReservation,
    RehearsalCredential,
)

from .auth_builders import signed_in
from .campaign_builders import add_draft
from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admin_browser(auth_service, google):
    """Provide a real signed-in Admin with a draft campaign."""
    add_draft(auth_service.store, auth_service.store.active(), uuid4())
    browser, response = signed_in()
    assert response.status_code == 302
    return browser


def test_admin_messages_stay_in_the_admin_database_session(admin_browser, monkeypatch):
    """Django messages never create a root-scoped identity-bearing message cookie."""
    from parishkit.stewardship.accounts import authentication

    original = authentication.render

    def notify(request, *args, **kwargs):
        messages.info(request, "Synthetic private Admin message")
        return original(request, *args, **kwargs)

    monkeypatch.setattr(authentication, "render", notify)
    response = admin_browser.get("/admin/")
    assert response.status_code == 200
    assert "messages" not in response.cookies and "pk_family" not in response.cookies
    assert (
        "Synthetic private"
        in PortalSession.objects.get().session.get_decoded()["_messages"]
    )


@pytest.mark.parametrize("consumers", [("web",), ("worker",), ("web", "worker")])
def test_sealed_credential_intake_requires_complete_consumers(consumers):
    """Neither the typed intake nor raw SQL can omit a mounting service."""
    identifier, actor = uuid4(), uuid4()
    intent = dict(
        request_id=identifier,
        target="general_encryption",
        staging_reference=uuid4(),
        actor_id=actor,
        reauthenticated_at=timezone.now() - timedelta(seconds=5),
        expires_at=timezone.now() + timedelta(minutes=5),
        expected_fingerprint=None,
        correlation_id=uuid4(),
        required_consumers=consumers,
        sealed_candidate=PrivateHandoff(
            "general_encryption", Key("handoff", "active", b"h" * 32)
        )
        .public()
        .seal(identifier, b"synthetic"),
        candidate_fingerprint="a" * 64,
    )
    if len(consumers) == 2:
        assert stage_secret_request(**intent).state == "staged"
        return
    with pytest.raises(ConfigError, match="inventory"):
        stage_secret_request(**intent)
    with pytest.raises(IntegrityError, match="must be complete"), transaction.atomic():
        SecretReplacementRequest.objects.create(
            target=intent["target"],
            actor_id=actor,
            requested_by_id=actor,
            staging_reference=intent["staging_reference"],
            reauthenticated_at=intent["reauthenticated_at"],
            expires_at=intent["expires_at"],
            required_consumers=list(consumers),
        )


@pytest.mark.parametrize("values", [{"algorithm": "other"}, {"digest": "A" * 64}])
def test_rehearsal_mac_format_is_enforced_by_sql(family_service, values):  # noqa: F811
    """Raw inserts cannot create unusable lookups or collision reservations."""
    credential = RehearsalCredential.objects.get()
    common = {"key_id": "other", "digest": "a" * 64, **values}
    with (
        pytest.raises(IntegrityError, match="rehearsal_code_mac_format"),
        transaction.atomic(),
    ):
        RehearsalCodeFingerprint.objects.create(
            credential=credential, epoch=credential.epoch, **common
        )
    with (
        pytest.raises(IntegrityError, match="rehearsal_reservation_format"),
        transaction.atomic(),
    ):
        RehearsalCodeReservation.objects.create(
            campaign=family_service.campaign, **common
        )
