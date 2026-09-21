"""Administrator decisions on seeded assignments through previewed requests.

Keeping a seeded Ministry leader role independently, restoring a suspended
seed as a manual assignment and removing a seed are proposed from the Portal
users page as native forms, shown back as exactly the records that change,
and applied only when the Administrator confirms the signed preview. Each is
an ordinary policy request under the rule editor's schema and guards; the
entered reason for a restore or removal is recorded in the audit event
written in the request's own transaction, never in the applied YAML.
"""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
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
from .chair_review import (
    DECISIONS,
    ReviewRefused,
    keep_role_patch,
    remove_seed_patch,
    restore_manual_patch,
)
from .configuration_requests import policy_operation_id
from .limiting import LimiterUnavailable
from .policy import Capability
from .policy_schema import normalized_email
from .request_patch import build_candidate

SALT = "stewardship-chair-review-preview-v1"
PREVIEW_FIELDS = {"decision", "identity", "ministry_duid", "reason", "base_digest"}


class ReviewForm(forms.Form):
    """One decision for one address, with the Ministry and reason it needs."""

    decision = forms.ChoiceField(choices=[(name, name) for name in DECISIONS])
    identity = forms.CharField(max_length=254)
    ministry_duid = forms.IntegerField(required=False, min_value=1, max_value=2**31)
    reason = forms.CharField(required=False, max_length=500, strip=True)
    base_digest = forms.RegexField(regex=r"^[0-9a-f]{64}$")

    def clean(self):
        """A restore or removal names its Ministry and carries a reason."""
        data = super().clean()
        if data.get("decision") in ("restore", "remove") and (
            data.get("ministry_duid") is None or not data.get("reason")
        ):
            raise forms.ValidationError("A Ministry and a reason are required.")
        return data


def _error(request, code, *, status=400):
    """A closed explanation by reason code, never naming a person."""
    response = render(
        request, "stewardship/chair-review-error.html", {"code": code}, status=status
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _scope(service):
    """A decision pins the applied policy; it does not depend on a refresh."""
    return editable_configuration(service), None


def _preview(request, service, actor):
    """Show exactly what the decision changes, signed for one confirmation."""
    form = ReviewForm(request.POST)
    if not form.is_valid():
        return _error(request, "invalid")
    configuration = editable_configuration(service)
    data = form.cleaned_data
    if data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the page before deciding.")
    records = configuration.active_configuration.canonical_document["sections"].get(
        "login_rules", []
    )
    key = uuid4()
    operation = str(policy_operation_id(actor.identity, key))
    try:
        email = normalized_email(data["identity"])
        if data["decision"] == "keep_role":
            change = keep_role_patch(records, email, operation_id=operation)
        elif data["decision"] == "restore":
            change = restore_manual_patch(
                records, email, data["ministry_duid"], operation_id=operation
            )
        else:
            change = remove_seed_patch(records, email, data["ministry_duid"])
    except ConfigError:
        return _error(request, "invalid")
    except ReviewRefused as refused:
        return _error(request, refused.code)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        build_candidate(base, change.patch, candidate_id=uuid4())
    except ConfigError:
        return _error(request, "policy")
    request._stewardship_display_configuration = configuration
    return render(
        request,
        "stewardship/chair-review-preview.html",
        {
            "decision": change.decision,
            "identity": change.email,
            "ministry_duid": change.ministry_duid,
            "reason": data["reason"],
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=change.patch,
                salt=SALT,
                key=key,
                extra={
                    "decision": change.decision,
                    "ministry_duid": change.ministry_duid,
                    "reason": data["reason"],
                },
            ),
        },
    )


def _audit(request, extra):
    """Record the decision and its reason beside the request, in its transaction."""
    extra = extra or {}
    context = {"decision": extra.get("decision", "")}
    if extra.get("ministry_duid") is not None:
        context["ministry_duid"] = extra["ministry_duid"]
    if extra.get("reason"):
        context["review_reason"] = extra["reason"]
    record_action(
        Action.CHAIR_REVIEW_DECIDED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=request.actor_id,
        subject_id=request.pk,
        context=context,
    )


@require_POST
def chair_reviews(request):
    """Preview one decision, or confirm a signed preview as a request."""
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
                    attach=_audit,
                )
        except StaleRecordError:
            response = _error(request, "stale", status=409)
        fresh = principal(
            request, service, read_only=True, capability=Capability.MANAGE_USERS
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Chairperson review editor changed.")
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
