"""Shared web admission binds durable intents to current, CSRF-protected identity.

The receiving feature owns form validation/confirmation and response rendering.
These adapters never authorize with a submitted actor ID, retained role list, or
client timestamp; internal storage primitives remain available to offline owners.
"""

from django.db import connection

from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.storage import StorageInvariantError

from .authentication import runtime
from .configuration_requests import record_request
from .models import PortalSession, PortalUser, SystemConfiguration
from .secret_requests import stage_secret_request
from .sessions import authenticated_admin, require_fresh


def admit_admin_action(request, *, actor_id, action):
    """Freeze configuration/user/session admission until the intent commits.

    The receiving view must run normal Django CSRF middleware/decorators. Setup
    and restore workflow owners require their own specialized admission; this
    normal-operation helper deliberately cannot bypass those gates.
    """
    if not connection.in_atomic_block:
        raise StorageInvariantError(
            "Privileged admission requires its owning transaction."
        )
    if action not in {
        Action.CONFIGURATION_REQUEST,
        Action.SECRET_REPLACEMENT,
        Action.DESTRUCTIVE_CONFIRMATION,
    } or not isinstance(action, Action):
        raise ValueError("Unknown privileged operation.")
    if (
        request.method != "POST"
        or not request.path.startswith("/admin/")
        or getattr(request, "csrf_processing_done", False) is not True
        or getattr(request, "_dont_enforce_csrf_checks", False)
    ):
        raise PermissionError("Access is unavailable.")
    service = runtime()
    # Configuration activation/offline recovery takes the conflicting root lock.
    # Pin it before user/session locks; an allow decision cannot survive a
    # concurrent policy application while the durable request is being inserted.
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM stewardship_system_configuration FOR SHARE")
    root = SystemConfiguration.objects.first()
    if root is None or root.restore_review_required or not service.configured():
        raise PermissionError("Access is unavailable.")
    if (
        not PortalUser.objects.select_for_update()
        .filter(pk=actor_id, disabled=False)
        .exists()
    ):
        raise PermissionError("Access is unavailable.")
    if (
        not PortalSession.objects.select_for_update()
        .filter(
            session_id=request.session.session_key,
            principal_id=actor_id,
            revoked_at__isnull=True,
        )
        .exists()
    ):
        raise PermissionError("Access is unavailable.")
    # Authorization cannot rotate a cookie inside an intent transaction that may
    # subsequently roll back. Normal page admission owns that session mutation;
    # a stale privilege fingerprint is denied here without dangling session state.
    principal = authenticated_admin(request, store=service.store, read_only=True)
    if (
        principal is None
        or principal.identity != actor_id
        or "administrator" not in principal.roles
    ):
        raise PermissionError("Access is unavailable.")
    if action in {Action.SECRET_REPLACEMENT, Action.DESTRUCTIVE_CONFIRMATION}:
        require_fresh(request)
    return True


def _actor(request):
    """Identify the session's actor without accepting an identifier from form data."""
    actor = (
        PortalSession.objects.filter(
            session_id=request.session.session_key, revoked_at__isnull=True
        )
        .values_list("principal_id", flat=True)
        .first()
    )
    if actor is None:
        raise PermissionError("Access is unavailable.")
    return actor


def configuration_request(request, *, base_digest, patch, request_key, correlation_id):
    """Intake and its SQL-owned audit commit only after locked live Admin admission."""
    actor = _actor(request)
    return record_request(
        base_digest=base_digest,
        patch=patch,
        request_key=request_key,
        correlation_id=correlation_id,
        actor_id=actor,
        admit=lambda: admit_admin_action(
            request, actor_id=actor, action=Action.CONFIGURATION_REQUEST
        ),
    )


def sealed_secret_request(request, **intent):
    """Stage an already target-sealed candidate, deriving freshness server-side.

    ARC-06's public handoff seals the value immediately in the receiving view.
    No plaintext parameter is accepted here. Explicit storage parameters reject
    unknown form fields, and the SQL trigger commits secret audit atomically.
    """
    if {"actor_id", "reauthenticated_at", "admit"} & intent.keys():
        raise ValueError("Authentication evidence cannot be supplied by the caller.")
    if not intent.get("sealed_candidate") or not intent.get("required_consumers"):
        raise ValueError(
            "Online secret requests require sealed consumer-bound staging."
        )
    actor = _actor(request)
    authenticated = PortalSession.objects.get(
        session_id=request.session.session_key, principal_id=actor
    ).authenticated_at
    return stage_secret_request(
        **intent,
        actor_id=actor,
        reauthenticated_at=authenticated,
        admit=lambda: admit_admin_action(
            request, actor_id=actor, action=Action.SECRET_REPLACEMENT
        ),
    )
