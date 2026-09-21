"""Administrator edits of login rules through previewed, versioned requests.

A change is proposed from the Portal users page as a native form, shown back
as exactly the roles that would change and for whom, and applied only when the
Administrator confirms the signed preview. The confirmation records one
`ConfigurationChangeRequest` bound to the applied digest; the installer, not
this process, activates it, and every guard the other Admin editors rely on
applies here: current session and capability, CSRF, a fresh preview, and the
policy validation that keeps at least one exact-address Administrator.
"""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .configuration_requests import policy_operation_id
from .limiting import LimiterUnavailable
from .policy import Capability
from .policy_models import PortalUser
from .request_patch import build_candidate
from .user_rows import ROLE_LABELS
from .user_rules import ROLE_ORDER, RuleRefused, rule_patch

SALT = "stewardship-user-rules-preview-v1"
PREVIEW_FIELDS = {"kind", "identity", "operation", "roles", "base_digest"}


class RuleForm(forms.Form):
    """Only a target, an operation, role ticks and the applied digest come in."""

    kind = forms.ChoiceField(choices=(("address", "address"), ("domain", "domain")))
    identity = forms.CharField(max_length=254)
    operation = forms.ChoiceField(choices=(("set", "set"), ("remove", "remove")))
    roles = forms.MultipleChoiceField(
        choices=[(role, role) for role in ROLE_ORDER], required=False
    )
    base_digest = forms.RegexField(regex=r"^[0-9a-f]{64}$")


def _error(code, *, status=400):
    """A closed explanation by reason code, never the submitted target."""
    response = HttpResponse(
        render_to_string("stewardship/user-rule-error.html", {"code": code}),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    return response


def _scope(service):
    """Rule changes pin YAML only; they do not depend on a source refresh."""
    return editable_configuration(service), None


def _labels(roles):
    """Role labels in one fixed order, for the preview's before and after."""
    return [ROLE_LABELS[role] for role in ROLE_ORDER if role in (roles or ())]


def _preview(request, service, actor):
    """Show exactly what would change, signed for one confirmation."""
    form = RuleForm(request.POST)
    if not form.is_valid():
        return _error("invalid")
    configuration = editable_configuration(service)
    data = form.cleaned_data
    if data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the rules before changing them.")
    records = configuration.active_configuration.canonical_document["sections"].get(
        "login_rules", []
    )
    # The request key is chosen now so the manual provenance written into the
    # patch names the request that will carry it; the installer verifies that.
    key = uuid4()
    try:
        change = rule_patch(
            records,
            kind=data["kind"],
            identity=data["identity"],
            roles=data["roles"],
            operation=data["operation"],
            operation_id=str(policy_operation_id(actor.identity, key)),
        )
    except RuleRefused as refused:
        return _error(refused.code)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        # The whole resulting policy is validated here, so a change that would
        # leave the parish without an exact-address Administrator is refused
        # before anything is signed, not discovered by the installer.
        build_candidate(base, change.patch, candidate_id=uuid4())
    except ConfigError:
        return _error("policy")
    field = "email" if change.kind == "address" else "hosted_domain"
    recorded = PortalUser.objects.filter(**{field: change.identity}).count()
    own = (
        PortalUser.objects.filter(pk=actor.identity)
        .values_list("email", flat=True)
        .first()
    )
    return render(
        request,
        "stewardship/user-rule-preview.html",
        {
            "kind": change.kind,
            "identity": change.identity,
            "created": change.before is None,
            "removed": change.after is None,
            "before": _labels(change.before),
            "after": _labels(change.after),
            "deny": change.after == [],
            "expansion": change.expansion,
            "recorded": recorded,
            # Losing one's own Administrator role is allowed when another
            # Administrator remains; it takes effect on the next request.
            "self_affected": change.kind == "address"
            and own == change.identity
            and "administrator" in (change.before or ())
            and "administrator" not in (change.after or ()),
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=change.patch,
                salt=SALT,
                key=key,
            ),
        },
    )


@require_POST
def user_rules(request):
    """Preview one rule change, or confirm a signed preview as a request."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        if request.GET:
            raise ValueError("Rule changes travel only in the form body.")
        action = form_action(
            request.POST, preview_fields=PREVIEW_FIELDS, multiple_fields={"roles"}
        )
        if action == "preview":
            response = _preview(request, service, actor)
        else:
            response = confirm(request, service, actor, salt=SALT, current_scope=_scope)
        principal(request, service, read_only=True, capability=Capability.MANAGE_USERS)
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
