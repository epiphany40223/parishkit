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
from django.shortcuts import render
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

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
from .user_rows import AppliedPolicy, role_labels
from .user_rules import ROLE_ORDER, RuleRefused, rule_patch
from .user_views import policy_identities

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


def _error(request, code, *, status=400):
    """A closed explanation by reason code, with the Admin chrome, never the target.

    These refusals are ordinary editing mistakes, not outages, so the page keeps
    its navigation and session deadlines like any other Admin page.
    """
    response = render(
        request, "stewardship/user-rule-error.html", {"code": code}, status=status
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _scope(service):
    """Rule changes pin YAML only; they do not depend on a source refresh."""
    return editable_configuration(service), None


def _reach(records, change):
    """How many recorded Google accounts the rule reaches, as the page counts.

    A domain rule reaches the accounts the evaluator really authorizes through
    it, the number the page's column shows, so the page's own index decides it;
    a Chairperson confirmation cannot change a domain rule's count, so none is
    consulted. An address reaches its usable recorded identities, read for that
    one address directly: the page's index loads only identities the applied
    policy names, and a new rule's address, such as a consumer account that
    already tried to sign in, is not yet among them.
    """
    if change.kind == "domain":
        policy = AppliedPolicy(records, policy_identities(records))
        return len(policy.authorized.get(change.identity, []))
    return PortalUser.objects.filter(email=change.identity, disabled=False).count()


def _preview(request, service, actor):
    """Show exactly what would change, signed for one confirmation."""
    form = RuleForm(request.POST)
    if not form.is_valid():
        return _error(request, "invalid")
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
        return _error(request, refused.code)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        # The whole resulting policy is validated here, so a change that would
        # leave the parish without an exact-address Administrator is refused
        # before anything is signed, not discovered by the installer.
        build_candidate(base, change.patch, candidate_id=uuid4())
    except ConfigError:
        return _error(request, "policy")
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
            "before": role_labels(change.before or ()),
            "after": role_labels(change.after or ()),
            "expansion": change.expansion,
            "recorded": _reach(records, change),
            # Losing one's own Administrator role, by a role change or by
            # removing one's own exact rule, is allowed when another
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
        filters(request.GET, allowed=set())
        action = form_action(
            request.POST, preview_fields=PREVIEW_FIELDS, multiple_fields={"roles"}
        )
        try:
            if action == "preview":
                response = _preview(request, service, actor)
            else:
                response = confirm(
                    request,
                    service,
                    actor,
                    salt=SALT,
                    current_scope=_scope,
                    capability=Capability.MANAGE_USERS,
                )
        except StaleRecordError:
            # A form drawn from, or a review signed against, an older policy:
            # an ordinary case for a page left open, so it gets the page's
            # own explanation rather than the editors' JSON conflict, and the
            # same final recheck as every other response on this route.
            response = _error(request, "stale", status=409)
        principal(request, service, read_only=True, capability=Capability.MANAGE_USERS)
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
