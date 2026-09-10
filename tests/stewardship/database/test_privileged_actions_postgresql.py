"""Intent services share current-role/CSRF/freshness admission and private audit."""

import json
from datetime import timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.accounts.models import (
    ConfigurationChangeRequest,
    PortalSession,
    SecretReplacementRequest,
)
from parishkit.stewardship.accounts.privileged_actions import (
    admit_admin_action,
    configuration_request,
    sealed_secret_request,
)
from parishkit.stewardship.audit.models import AuditContext, AuditEvent, OperationalLog
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.storage import StorageInvariantError

from ..policy_factory import address
from ..test_request_patch import parish_patch
from .auth_builders import signed_in
from .campaign_builders import change

pytestmark = pytest.mark.django_db(transaction=True)


def admitted_request(browser):
    """Model CSRF middleware's server-owned marker, with real Google-issued session.

    End-to-end CSRF rejection is tested on the actual authentication/Family HTTP
    routes. Future owner forms must use normal middleware, never client flags.
    """
    request = RequestFactory().post("/admin/configuration")
    request.session = import_module(settings.SESSION_ENGINE).SessionStore(
        browser.cookies["pk_admin"].value
    )
    request.csrf_processing_done = True
    return request


def config_intent(service):
    """A harmless real parish patch, not an identity selected by posted input."""
    base = service.store.active()
    return dict(
        base_digest=base.digest,
        patch=parish_patch(base, name="Updated parish"),
        request_key=uuid4(),
        correlation_id=uuid4(),
    )


def test_configuration_intent_uses_live_actor_and_atomic_audit(auth_service, google):
    """An authenticated retry retains the original attributed durable receipt."""
    browser, _ = signed_in()
    request = admitted_request(browser)
    intent = config_intent(auth_service)
    status = configuration_request(request, **intent)
    row = ConfigurationChangeRequest.objects.get(pk=status.request_id)
    session = PortalSession.objects.get(session_id=request.session.session_key)
    assert row.actor_id == session.principal_id
    assert AuditEvent.objects.filter(
        subject_id=row.pk, actor_id=row.actor_id, event_type="config_request_staged"
    ).exists()
    assert configuration_request(request, **intent) == status


@pytest.mark.parametrize("failure", ["csrf", "bypass", "family", "get", "revoked"])
def test_admission_rejects_before_durable_intent(auth_service, google, failure):
    """Wrong namespace, method, CSRF processing or revoked identity writes nothing."""
    browser, _ = signed_in()
    request = admitted_request(browser)
    if failure == "csrf":
        request.csrf_processing_done = False
    elif failure == "bypass":
        request._dont_enforce_csrf_checks = True
    elif failure == "family":
        request.path = request.path_info = "/family/"
    elif failure == "get":
        request.method = "GET"
    else:
        browser.post(
            "/admin/logout",
            {
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            },
        )
    with pytest.raises(PermissionError):
        configuration_request(request, **config_intent(auth_service))
    assert not ConfigurationChangeRequest.objects.exists()


def test_current_staff_role_denies_even_with_old_admin_cookie(auth_service, google):
    """A denied intent cannot retain privilege or leave a rolled-back cookie."""
    browser, _ = signed_in()
    root = auth_service.store.active()
    replacement = address("staff@example.org", roles=("staff",))
    change(
        auth_service.store,
        root,
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **replacement},
        ],
    )
    # The same verified Google subject now has an address with Staff-only rules.
    # Its earlier cookie must not retain the old address's Admin authority.
    google[0]["email"] = "staff@example.org"
    _, response = signed_in()
    assert response.status_code == 302
    before = ConfigurationChangeRequest.objects.count()
    request = admitted_request(browser)
    old_session = request.session.session_key
    with pytest.raises(PermissionError):
        configuration_request(request, **config_intent(auth_service))
    assert ConfigurationChangeRequest.objects.count() == before
    assert request.session.session_key == old_session
    assert PortalSession.objects.filter(session_id=old_session).exists()


def secret_intent():
    """Synthetic private marker must never enter receipts, audit or support output."""
    identifier = uuid4()
    marker = b"seeded-private-credential-unique-317"
    handoff = PrivateHandoff("slack", Key("handoff", "active", b"h" * 32))
    return marker, dict(
        request_id=identifier,
        target="slack",
        staging_reference=uuid4(),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint=None,
        correlation_id=uuid4(),
        required_consumers=("worker",),
        sealed_candidate=handoff.public().seal(identifier, marker),
        candidate_fingerprint=file_fingerprint(marker),
    )


def test_sealed_intake_privacy_and_audit_share_real_authentication(
    auth_service, google
):
    """Only isolated staging retains ciphertext; receipts and audit stay non-secret."""
    browser, response = signed_in()
    marker, intent = secret_intent()
    receipt = sealed_secret_request(admitted_request(browser), **intent)
    row = SecretReplacementRequest.objects.get(pk=receipt.request_id)
    assert (
        row.requested_by_id
        == PortalSession.objects.get(
            session_id=browser.cookies["pk_admin"].value
        ).principal_id
    )
    artifacts = json.dumps(
        {
            "receipt": repr(receipt),
            "response": response.content.decode(),
            "audit": list(AuditEvent.objects.values()),
            "contexts": list(AuditContext.objects.values()),
            "operations": list(OperationalLog.objects.values()),
        },
        default=str,
    )
    for private in (
        marker.decode(),
        intent["sealed_candidate"],
        browser.cookies["pk_admin"].value,
    ):
        assert private not in artifacts


def test_secret_intake_rejects_stale_auth_and_client_evidence(
    auth_service,
    google,
    monkeypatch,
):
    """A browser timestamp cannot replace a recent completed Google round trip."""
    browser, _ = signed_in()
    request = admitted_request(browser)
    _, intent = secret_intent()
    with pytest.raises(ValueError, match="caller"):
        sealed_secret_request(request, **intent, reauthenticated_at=timezone.now())
    from parishkit.stewardship.accounts import sessions

    real_now = sessions.database_now
    monkeypatch.setattr(
        sessions, "database_now", lambda: real_now() + timedelta(minutes=6)
    )
    with pytest.raises(PermissionError, match="Google"):
        sealed_secret_request(request, **intent)
    assert not SecretReplacementRequest.objects.exists()


def test_admission_requires_owning_transaction_and_closed_action(auth_service, google):
    """No point-in-time permission token escapes the intent's transaction."""
    browser, _ = signed_in()
    request = admitted_request(browser)
    actor = PortalSession.objects.get(
        session_id=request.session.session_key
    ).principal_id
    with pytest.raises(StorageInvariantError):
        admit_admin_action(request, actor_id=actor, action=Action.CONFIGURATION_REQUEST)
    with transaction.atomic(), pytest.raises(ValueError):
        admit_admin_action(request, actor_id=actor, action="configuration_requested")
