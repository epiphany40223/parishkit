"""Shared authenticated, exact-preview admission for non-secret Admin editors.

Only server-built patches are signed. A signature binds intent, not permission:
confirmation rechecks current session, role, configuration and optional source
scope inside the request owner's transaction. The web process never installs
YAML or claims that an accepted request has already applied.
"""

from uuid import UUID, uuid4

from django.core import signing
from django.core.paginator import InvalidPage
from django.http import HttpResponseRedirect, JsonResponse

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)

from .configuration_installation import coherent_configuration
from .configuration_requests import record_request
from .policy import Capability, allows
from .request_models import ConfigurationChangeRequest
from .sessions import authenticated_admin


def principal(request, service, *, passive=False):
    """Read current authorization; passive progress pages never renew idle time."""
    value = authenticated_admin(request, store=service.store, activity=not passive)
    if not allows(value, Capability.CONFIGURE):
        raise PermissionError("Configuration requires an Administrator.")
    return value


def editable_configuration(service):
    """No ordinary edit is possible before setup or during restore/mismatch."""
    value = coherent_configuration(service.store)
    if value.restore_review_required or not service.configured():
        raise ConfigError("Configuration is unavailable.")
    return value


def form_action(parameters, *, preview_fields, multiple_fields=frozenset()):
    """Reject hidden, repeated and cross-action fields before constructing intent."""
    fields = {
        "preview": {"action", "csrfmiddlewaretoken", *preview_fields},
        "confirm": {"action", "csrfmiddlewaretoken", "preview"},
    }
    action = parameters.get("action")
    if (
        action not in fields
        or set(parameters) - fields[action]
        or any(
            len(values) != 1 and not (action == "preview" and name in multiple_fields)
            for name, values in parameters.lists()
        )
    ):
        raise ValueError("Invalid configuration action or fields.")
    return action


def sign_preview(*, actor, configuration, patch, salt, snapshot=None):
    """A preview has one request key, one base and a fifteen-minute validity window."""
    return signing.dumps(
        {
            "actor": str(actor.identity),
            "key": str(uuid4()),
            "base": configuration.active_configuration.digest,
            "snapshot": str(snapshot) if snapshot is not None else None,
            "patch": patch,
        },
        salt=salt,
    )


def confirm(request, service, actor, *, salt, current_scope, request_schema=None):
    """Admit one exact intent; identical retries return the original receipt."""
    token = request.POST.get("preview", "")
    if len(token) > 256_000:
        raise ValueError("Invalid configuration preview.")
    intent = signing.loads(token, salt=salt, max_age=900)
    if intent["actor"] != str(actor.identity):
        raise PermissionError("Preview belongs to another Administrator.")
    key = UUID(intent["key"])

    def admit():
        """The owning work lock persists until intake's durable transaction commits."""
        with work_transaction():
            fresh = authenticated_admin(request, store=service.store, read_only=True)
            if (
                not allows(fresh, Capability.CONFIGURE)
                or fresh.identity != actor.identity
            ):
                return False
            configuration, snapshot = current_scope(service)
            existing = ConfigurationChangeRequest.objects.filter(
                actor_id=actor.identity, request_key=key
            ).exists()
            if not existing and (
                configuration.active_configuration.digest != intent["base"]
                or (str(snapshot) if snapshot is not None else None)
                != intent["snapshot"]
            ):
                raise StaleRecordError(
                    "Review a fresh preview before applying changes."
                )
            return True

    receipt = record_request(
        base_digest=intent["base"],
        patch=intent["patch"],
        actor_id=actor.identity,
        request_key=key,
        correlation_id=current_correlation(),
        admit=admit,
        request_schema=request_schema,
    )
    return HttpResponseRedirect(f"/admin/configuration/requests/{receipt.request_id}")


def error_response(error):
    """Expose only closed, static error messages, never exception or submitted text."""
    if isinstance(error, PermissionError):
        return validation_response([FieldError(ErrorCode.DENIED)], status=403)
    if isinstance(error, StaleRecordError):
        return validation_response([FieldError(ErrorCode.STALE)], status=409)
    if isinstance(error, ConfigError):
        return validation_response([FieldError(ErrorCode.UNAVAILABLE)], status=503)
    if isinstance(error, (ValueError, InvalidPage, signing.BadSignature)):
        return validation_response([FieldError(ErrorCode.INVALID)], status=400)
    if isinstance(error, LookupError):
        response = JsonResponse(
            {"errors": [FieldError(ErrorCode.UNAVAILABLE).as_dict()]}, status=404
        )
        response.stewardship_safe_error = True
        response["Cache-Control"] = "no-store"
        return response
    return validation_response([FieldError(ErrorCode.UNAVAILABLE)], status=503)
