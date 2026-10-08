"""Shared authenticated, exact-preview admission for non-secret Admin editors.

Only server-built patches are signed. A signature binds intent, not permission:
confirmation rechecks current session, role, configuration and optional source
scope inside the request owner's transaction. The web process never installs
YAML or claims that an accepted request has already applied.
"""

import logging
from uuid import UUID, uuid4

from django.core import signing
from django.core.paginator import InvalidPage
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.single_campaign import refuse_campaign_creation
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import (
    current_correlation,
    debug_logging_enabled,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingGone,
    expired_preview,
    gone_response,
    load_preview,
)

from . import admin_navigation
from .configuration_installation import coherent_configuration
from .configuration_requests import record_request
from .policy import Capability, allows
from .request_models import ConfigurationChangeRequest
from .sessions import FreshAuthenticationRequired, authenticated_admin, require_fresh


def principal(
    caller, service, *, passive=False, read_only=False, capability=Capability.CONFIGURE
):
    """Read current authorization; passive progress pages never renew idle time.

    A read-only recheck after rendering renews nothing either. Pages that need
    a different Administrator capability name it, so one admission function
    serves every Admin page. ``caller`` is an ``AdminCaller``, or (until the
    final ADM-11 PR) a Django request that ``authenticated_admin`` converts.
    """
    value = authenticated_admin(
        caller,
        store=service.store,
        activity=not passive and not read_only,
        read_only=read_only,
    )
    if value is None:
        # No current session (idle timeout, sign-out, revocation): say so,
        # rather than implying the signed-in account lacks a capability.
        raise signed_out()
    if not allows(value, capability):
        raise PermissionError(f"This page requires the {capability} capability.")
    return value


def signed_out():
    """The refusal for a request whose Admin session has ended."""
    return UserFacingDenied(
        _(
            "Your sign-in has ended, for example after an hour without "
            "activity or after signing out in another tab."
        ),
        fix=_("Sign in again to continue."),
        link="/admin/login",
        link_label=_("Sign in"),
    )


def editable_configuration(service):
    """No ordinary edit is possible before setup or during restore/mismatch."""
    value = coherent_configuration(service.store)
    if value.restore_review_required or not service.configured():
        raise ConfigError("Configuration is unavailable.")
    return value


def form_action(
    parameters,
    *,
    preview_fields,
    multiple_fields=frozenset(),
    confirm_fields=frozenset(),
):
    """Reject hidden, repeated and cross-action fields before constructing intent.

    ``confirm_fields`` names the few explicit confirmation inputs, such as an
    acknowledgement checkbox, that accompany the signed preview.
    """
    fields = {
        "preview": {"action", "csrfmiddlewaretoken", *preview_fields},
        "confirm": {"action", "csrfmiddlewaretoken", "preview", *confirm_fields},
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


def sign_preview(
    *, actor, configuration, patch, salt, snapshot=None, key=None, extra=None
):
    """A preview has one request key, one base and a fifteen-minute validity window.

    An editor whose patch must name its own request, as manual policy
    provenance does, chooses the key first and passes it; the rest take a
    fresh one. An editor whose request needs companion rows signs their
    content as `extra`, so confirmation records exactly what was reviewed.
    """
    return signing.dumps(
        {
            "actor": str(actor.identity),
            "key": str(key or uuid4()),
            "base": configuration.active_configuration.digest,
            "snapshot": str(snapshot) if snapshot is not None else None,
            "patch": patch,
            "extra": extra,
        },
        salt=salt,
    )


def confirm(
    request,
    service,
    actor,
    *,
    salt,
    current_scope,
    request_schema=None,
    capability=Capability.CONFIGURE,
    attach=None,
    fresh=False,
):
    """Admit one exact intent from the page; redirect to its request status.

    The page's form carries the signed preview; ``confirm_intent`` does the
    rest. The status page leads back to this editor (#196). ``fresh`` asks
    for a recent Google sign-in again under the work lock (#547).
    """
    receipt = confirm_intent(
        request,
        service,
        actor,
        token=request.POST.get("preview", ""),
        salt=salt,
        link=request.path,
        current_scope=current_scope,
        request_schema=request_schema,
        capability=capability,
        attach=attach,
        fresh=fresh,
    )
    admin_navigation.remember_origin(request, receipt.request_id)
    return HttpResponseRedirect(
        reverse("admin:configuration_request", args=[receipt.request_id])
    )


def confirm_intent(
    caller,
    service,
    actor,
    *,
    token,
    salt,
    link,
    current_scope,
    request_schema=None,
    capability=Capability.CONFIGURE,
    attach=None,
    fresh=False,
):
    """Admit one exact intent; identical retries return the original receipt.

    ``caller`` is the page's request or an ``AdminCaller`` (the command
    line's ``schedule confirm``); ``link`` is the page an expired preview
    sends the Administrator back to (None from the command line). The
    recheck under the work transaction tests the same capability the page
    admitted with, so the two cannot diverge if the capability matrix
    changes. `attach(request, extra)` records an editor's companion rows from
    the signed preview inside the request's own durable transaction, and is
    never called on a retry. With ``fresh``, the sign-in must also still be
    fresh (``require_fresh``) when rechecked under the work lock, so a
    sign-in that ages while waiting for the lock is refused. Returns the
    request's ``RequestStatus``.
    """
    if len(token) > 256_000:
        raise ValueError("Invalid configuration preview.")
    intent = load_preview(token, salt=salt, link=link)
    if intent["actor"] != str(actor.identity):
        raise PermissionError("Preview belongs to another Administrator.")
    # Until #145 no editor may add a campaign, even with a preview signed
    # before New campaign and Copy campaign were retired (rule 10).
    refuse_campaign_creation(intent["patch"])
    key = UUID(intent["key"])

    def admit():
        """The owning work lock persists until intake's durable transaction commits."""
        with work_transaction():
            current = authenticated_admin(caller, store=service.store, read_only=True)
            if not allows(current, capability) or current.identity != actor.identity:
                return False
            if fresh:
                require_fresh(caller)
            configuration, snapshot = current_scope(service)
            existing = ConfigurationChangeRequest.objects.filter(
                actor_id=actor.identity, request_key=key
            ).exists()
            if not existing and (
                configuration.active_configuration.digest != intent["base"]
                or (str(snapshot) if snapshot is not None else None)
                != intent["snapshot"]
            ):
                # Settings changed since the review; start a fresh one.
                raise expired_preview(link)
            return True

    return record_request(
        base_digest=intent["base"],
        patch=intent["patch"],
        actor_id=actor.identity,
        request_key=key,
        correlation_id=current_correlation(),
        admit=admit,
        request_schema=request_schema,
        attach=(
            None
            if attach is None
            else lambda created: attach(created, intent.get("extra"))
        ),
    )


def error_response(error):
    """Expose only closed, static error messages, never exception or submitted text.

    Scripts receive JSON. A browser page request is shown the same messages as
    an HTML page by the browser-error middleware, which, for a missing fresh
    authentication, offers the step-up "Confirm with Google" instead. A
    user-facing refusal (``web.refusals``) keeps its base type's status but
    adds its reviewed explanation and fix link.
    """
    if debug_logging_enabled():
        # The response names only a closed category; say what actually failed.
        logging.getLogger("parishkit.stewardship.debug").debug(
            "admin request refused", exc_info=error
        )
    if isinstance(error, FreshAuthenticationRequired):
        response = validation_response([FieldError(ErrorCode.DENIED)], status=403)
        response.stewardship_reauthenticate = True
        return response
    if isinstance(error, UserFacingGone):
        return gone_response(error)
    if isinstance(error, PermissionError):
        code, status = ErrorCode.DENIED, 403
    elif isinstance(error, StaleRecordError):
        code, status = ErrorCode.STALE, 409
    elif isinstance(error, ConfigError):
        code, status = ErrorCode.UNAVAILABLE, 503
    elif isinstance(error, (ValueError, InvalidPage, signing.BadSignature)):
        code, status = ErrorCode.INVALID, 400
    elif isinstance(error, LookupError):
        code, status = ErrorCode.UNAVAILABLE, 404
    else:
        code, status = ErrorCode.UNAVAILABLE, 503
    return validation_response(
        [FieldError(code)], status=status, refusal=getattr(error, "refusal", None)
    )


def step_up_response(return_path=None, return_label=None):
    """Refuse a stale sign-in with the step-up page; nothing was done.

    A browser sees "Confirm it's you" with the "Confirm with Google" step-up
    (``web.error_pages``); a script gets the closed JSON denial. A POST-only
    route names ``return_path``, the Admin page its form came from, so the
    step-up returns there with a GET rather than to the POST URL, and names it
    in ``return_label`` (the page's name) so the page says where it returns.
    The page revalidates the path with ``admin_return_path``.
    """
    response = error_response(
        FreshAuthenticationRequired("Please authenticate with Google again.")
    )
    response.stewardship_return_path = return_path
    response.stewardship_return_label = return_label
    return response
