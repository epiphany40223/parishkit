"""Manual Ministry assignments through previewed, versioned requests.

An assignment is proposed from the Portal users page as a native form, shown
back as exactly the record that changes and how it would take effect, and
applied only when the Administrator confirms the signed preview. The
confirmation records one ordinary policy request bound to the applied digest;
the installer, not this process, activates it, and every guard the rule editor
relies on applies here. The Ministry catalog comes from the promoted snapshot
with the applied activity, as the Ministry activity editor reads it; only an
active catalog Ministry may be assigned.
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
from .assignment_edits import (
    AssignmentRefused,
    add_assignment_patch,
    remove_assignment_patch,
)
from .authentication import runtime
from .configuration_requests import policy_operation_id
from .limiting import LimiterUnavailable
from .ministry_views import _state as ministry_state
from .policy import Capability
from .request_patch import build_candidate
from .user_rows import AppliedPolicy

SALT = "stewardship-assignments-preview-v1"
PREVIEW_FIELDS = {"operation", "identity", "ministry_duid", "base_digest"}


class AssignmentForm(forms.Form):
    """One address, one Ministry, one operation, against the applied digest."""

    operation = forms.ChoiceField(choices=(("add", "add"), ("remove", "remove")))
    identity = forms.CharField(max_length=254, strip=True)
    ministry_duid = forms.IntegerField(min_value=1, max_value=2**31 - 1)
    base_digest = forms.RegexField(regex=r"^[0-9a-f]{64}$")


def _error(request, code, *, status=400):
    """A closed explanation by reason code, never naming a person."""
    response = render(
        request, "stewardship/assignment-error.html", {"code": code}, status=status
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _scope(service):
    """An assignment pins the applied policy; the catalog is advisory."""
    return editable_configuration(service), None


def _preview(request, service, actor):
    """Show exactly what changes and how it takes effect, signed once."""
    form = AssignmentForm(request.POST)
    if not form.is_valid():
        return _error(request, "invalid")
    data = form.cleaned_data
    configuration, _, _, catalog = ministry_state(service)
    if data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the page before changing assignments.")
    ministry = next(
        (row for row in catalog if row["duid"] == data["ministry_duid"]), None
    )
    # Only an active catalog Ministry may be assigned. A removal is judged by
    # the applied policy alone: an assignment to a Ministry since deactivated
    # or dropped from the catalog is exactly the one an Administrator cleans
    # up, so the catalog serves a removal only for the Ministry's name.
    if data["operation"] == "add" and (ministry is None or not ministry["active"]):
        return _error(request, "inactive")
    if ministry is None:
        ministry = {"duid": data["ministry_duid"], "name": ""}
    records = configuration.active_configuration.canonical_document["sections"].get(
        "login_rules", []
    )
    # The request key is chosen now so the manual provenance written into the
    # patch names the request that will carry it; the installer verifies that.
    key = uuid4()
    operation = str(policy_operation_id(actor.identity, key))
    try:
        if data["operation"] == "add":
            change = add_assignment_patch(
                records, data["identity"], ministry["duid"], operation_id=operation
            )
        else:
            change = remove_assignment_patch(
                records, data["identity"], ministry["duid"]
            )
    except ConfigError:
        return _error(request, "target")
    except AssignmentRefused as refused:
        return _error(request, refused.code)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        candidate = build_candidate(base, change.patch, candidate_id=uuid4())
    except ConfigError:
        return _error(request, "policy")
    # How the assignment would take effect: through the address's exact rule,
    # through its domain rule and that rule's hosted-domain claim, or not at
    # all until a rule grants Ministry leader. The roles come from the one
    # evaluator over the resulting policy, so an Administrator, whom it makes
    # a leader of everything, and a seeded leader role the new assignment
    # itself brings back into force are stated as the sign-in would decide.
    policy = AppliedPolicy(
        candidate.candidate.document()["sections"].get("login_rules", []), []
    )
    exact = policy.addresses.get(change.email)
    domain = policy.domains.get(change.email.rsplit("@", 1)[1])
    if exact is not None:
        rule, (granted, _) = "address", policy.resolve(change.email, None)
    elif domain is not None:
        rule, (granted, _) = (
            "domain",
            policy.resolve(change.email, domain["values"]["domain"]),
        )
    else:
        rule, granted = None, frozenset()
    request._stewardship_display_configuration = configuration
    return render(
        request,
        "stewardship/assignment-preview.html",
        {
            "operation": change.operation,
            "identity": change.email,
            "ministry": ministry,
            "rule": rule,
            "leader": "ministry_leader" in granted,
            "administrator": "administrator" in granted,
            "domain": domain["values"]["domain"] if domain else None,
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
def assignments(request):
    """Preview one assignment change, or confirm a signed preview as a request."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        action = form_action(request.POST, preview_fields=PREVIEW_FIELDS)
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
            response = _error(request, "stale", status=409)
        fresh = principal(
            request, service, read_only=True, capability=Capability.MANAGE_USERS
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Assignment editor changed.")
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
