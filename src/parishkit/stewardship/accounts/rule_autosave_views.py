"""Autosaved role changes as durable requests, answered for an enhanced page.

The users page's script sends each role checkbox change as one logical intent
with a client-chosen request key and the digest the page was drawn from; the
server builds the minimal rule patch and records it exactly as a confirmed
preview would, with the same admission, provenance and policy validation. The
answer is the request's committed state, and the page then reads the request
until an activation receipt says it applied. Nothing here activates
configuration, claims that an accepted request has applied, or repeats a
submitted value: failures are the closed codes every enhanced client
understands, and a lost answer is recovered by resubmitting the same key,
which answers with the original request's state whatever the rules are now,
never with a second grant.
"""

from uuid import UUID

from django import forms
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .configuration_requests import (
    policy_operation_id,
    record_request,
    request_status,
)
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_models import ConfigurationChangeRequest
from .rule_autosave import autosave_patch
from .sessions import authenticated_admin
from .user_rules import ROLE_ORDER, RuleRefused

FIELDS = frozenset(
    {"request_key", "base_digest", "kind", "identity", "role", "checked"}
)


class IntentForm(forms.Form):
    """One target, one role, one desired value, a key and the page's digest."""

    request_key = forms.UUIDField()
    base_digest = forms.RegexField(regex=r"^[0-9a-f]{64}$")
    kind = forms.ChoiceField(choices=(("address", "address"), ("domain", "domain")))
    identity = forms.CharField(max_length=254)
    role = forms.ChoiceField(choices=[(role, role) for role in ROLE_ORDER])
    checked = forms.ChoiceField(choices=(("0", "0"), ("1", "1")))


def _json(data, *, status=200):
    """A closed JSON answer, never cached."""
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _refused(code, field_id, *, status=400):
    """A closed refusal naming the control the page should mark."""
    return validation_response([FieldError(code, field_id)], status=status)


def _receipt(status):
    """The committed state the page reconciles by, never an inferred one."""
    return {
        "request_id": str(status.request_id),
        "state": status.state,
        "sequence": status.sequence,
        "failure_code": status.failure_code,
        "applied_digest": status.applied_digest,
    }


def _apply(request, service, actor):
    """Record one intent against the applied policy, or refuse by closed code."""
    form = IntentForm(request.POST)
    if not form.is_valid():
        return _refused(ErrorCode.INVALID, "intent")
    data = form.cleaned_data
    key = data["request_key"]
    if key.version != 4:
        return _refused(ErrorCode.INVALID, "request_key")
    # A key this Administrator already used names its request, whatever the
    # rules are now: the answer is that request's committed state, so a lost
    # answer is recovered after the installer has moved on, and the intent
    # is never rebuilt over a base it was not made against.
    existing = ConfigurationChangeRequest.objects.filter(
        actor_id=actor.identity, request_key=key
    ).first()
    if existing is not None:
        status = request_status(request_id=existing.pk, actor_id=actor.identity)
        return _json(_receipt(status), status=202)
    configuration = editable_configuration(service)
    if data["base_digest"] != configuration.active_configuration.digest:
        return _refused(ErrorCode.STALE, "base_digest", status=409)
    records = configuration.active_configuration.canonical_document["sections"].get(
        "login_rules", []
    )
    try:
        change = autosave_patch(
            records,
            kind=data["kind"],
            identity=data["identity"],
            role=data["role"],
            checked=data["checked"] == "1",
            operation_id=str(policy_operation_id(actor.identity, key)),
        )
    except RuleRefused as refused:
        # The page marks the one checkbox; the closed code says why in words
        # every enhanced client already has. A rule removed by another
        # Administrator reads as stale, since the page must be refreshed.
        if refused.code == "missing":
            return _refused(ErrorCode.STALE, "intent", status=409)
        return _refused(ErrorCode.INVALID, "intent")

    def admit():
        """Recheck actor and digest under the work lock, as a confirmation does."""
        with work_transaction():
            fresh = authenticated_admin(request, store=service.store, read_only=True)
            if (
                not allows(fresh, Capability.MANAGE_USERS)
                or fresh.identity != actor.identity
            ):
                return False
            current = editable_configuration(service)
            recorded = ConfigurationChangeRequest.objects.filter(
                actor_id=actor.identity, request_key=key
            ).exists()
            if (
                not recorded
                and current.active_configuration.digest != data["base_digest"]
            ):
                raise StaleRecordError("The applied login rules changed.")
            return True

    try:
        status = record_request(
            base_digest=data["base_digest"],
            patch=change.patch,
            actor_id=actor.identity,
            request_key=key,
            correlation_id=current_correlation(),
            admit=admit,
        )
    except ConfigError as error:
        # The whole resulting policy was validated: a change that would leave
        # the parish without an Administrator is refused, not an outage. A
        # key bound meanwhile to another intent, which only a concurrent
        # resubmission can produce, is refused as stale.
        if "already bound" in str(error):
            return _refused(ErrorCode.STALE, "request_key", status=409)
        return _refused(ErrorCode.INVALID, "intent")
    return _json(_receipt(status), status=202)


@require_POST
def rule_apply(request):
    """Record one autosaved role intent as a configuration request."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        filters(request.POST, allowed=FIELDS | {"csrfmiddlewaretoken"})
        if request.FILES:
            raise ValueError("Unexpected upload.")
        response = _apply(request, service, actor)
        fresh = principal(
            request, service, read_only=True, capability=Capability.MANAGE_USERS
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Login rule editor changed.")
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
        LookupError,
    ) as error:
        return error_response(error)


@require_safe
def rule_base(request):
    """The applied digest and configured roles, for the page's conflict view.

    Read for a genuine conflict only, so the Administrator sees the current
    rules beside the remaining intents and retries the chosen ones as new
    requests against the refreshed digest. It repeats what the page itself
    shows, under the same capability, and nothing more.
    """
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        with transaction.atomic():
            configuration = editable_configuration(service)
            fresh = authenticated_admin(request, store=service.store, read_only=True)
            if (
                not allows(fresh, Capability.MANAGE_USERS)
                or fresh.identity != actor.identity
            ):
                raise PermissionError("Login rule reader changed.")
        rules = {"address": {}, "domain": {}}
        for record in configuration.active_configuration.canonical_document[
            "sections"
        ].get("login_rules", []):
            values = record["values"]
            if values["kind"] == "address":
                rules["address"][values["email"]] = values["roles"]
            elif values["kind"] == "domain":
                rules["domain"][values["domain"]] = values["roles"]
        return _json(
            {"digest": configuration.active_configuration.digest, "rules": rules}
        )
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
    ) as error:
        return error_response(error)


@require_safe
def rule_request(request, request_id):
    """The committed state of the actor's own request; passive, never renewing."""
    try:
        service = runtime()
        actor = principal(
            request, service, passive=True, capability=Capability.MANAGE_USERS
        )
        filters(request.GET, allowed=set())
        with transaction.atomic():
            status = request_status(
                request_id=UUID(str(request_id)), actor_id=actor.identity
            )
            fresh = authenticated_admin(request, store=service.store, read_only=True)
            if (
                not allows(fresh, Capability.MANAGE_USERS)
                or fresh.identity != actor.identity
            ):
                raise PermissionError("Login rule reader changed.")
        return _json(_receipt(status))
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
    ) as error:
        return error_response(error)
