"""Admin integration settings and sealed replacement intake; no provider IO in web."""

from datetime import timedelta
from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS
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
from .handoff_discovery import public_handoff
from .integration_forms import LABELS, CredentialForm, IntegrationForm
from .integration_selection import authentication_scope
from .limiting import LimiterUnavailable
from .metrics_credentials import credential_receipt
from .policy import Capability, allows
from .privileged_actions import sealed_secret_request
from .request_patch import build_candidate
from .secret_models import SECRET_PENDING, SecretReplacementRequest
from .secret_requests import SecretRequestConflict, secret_request_status
from .sessions import authenticated_admin, require_fresh

SALT = "stewardship-integration-settings-v1"
CREDENTIAL_SALT = "stewardship-integration-credential-v1"


class IntegrationUnavailable(Exception):
    """Missing installer public discovery is availability, not invalid form input."""


ERRORS = (
    IntegrationUnavailable,
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    StaleRecordError,
    signing.BadSignature,
    LookupError,
)


def _records(configuration):
    """Expose canonical public settings only, never staging ciphertext or key files."""
    return {
        record["values"]["kind"]: record
        for record in configuration.active_configuration.canonical_document[
            "sections"
        ].get("integrations", [])
    }


def _selected(configuration, target):
    """Only existing supported records belong to this ordinary settings editor."""
    record = _records(configuration).get(target)
    if target not in LABELS or record is None:
        raise LookupError("Integration is unavailable.")
    return record


def _checked(request, service, response):
    """Repeat authorization before private settings or progress leave the process."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Integration access was revoked.")
    response["Cache-Control"] = "no-store"
    return response


def _page(request, configuration, target, *, form=None, status=200):
    """Show applied values and honest request progress, not inferred readiness."""
    record = _selected(configuration, target)
    latest = (
        SecretReplacementRequest.objects.filter(target=target)
        .order_by("-created_at", "pk")
        .first()
    )
    form = (
        form
        if form is not None
        else IntegrationForm(
            target,
            initial=record["values"]["settings"]
            | {"base_digest": configuration.active_configuration.digest},
        )
    )
    response = render(
        request,
        "stewardship/integration-settings.html",
        {
            "form": form,
            "target": target,
            "label": LABELS[target],
            "fingerprint": record["values"]["credential_fingerprint"],
            "latest": latest,
            "replacement_allowed": target != "email",
            "configuration": configuration,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor, target):
    """Prepare public settings while preserving the applied credential receipt."""
    configuration = editable_configuration(service)
    record = _selected(configuration, target)
    form = IntegrationForm(target, request.POST)
    if not form.is_valid():
        return _page(request, configuration, target, form=form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload integration settings.")
    settings = form.public_settings()
    before = record["values"]["settings"]
    if target == "parishsoft":
        before = {"nightly_time": "02:00"} | before
    if settings == before:
        form.add_error(None, _("No settings have changed."))
        return _page(request, configuration, target, form=form, status=400)
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": record["id"],
            "values": {"settings": settings},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    build_candidate(base, patch, candidate_id=uuid4())
    return render(
        request,
        "stewardship/integration-preview.html",
        {
            "target": target,
            "label": LABELS[target],
            "configuration": configuration,
            "changes": [
                {
                    "label": form.fields[name].label,
                    "before": before[name],
                    "after": value,
                }
                for name, value in settings.items()
                if before[name] != value
            ],
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT + target,
            ),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def integration_settings(request, target=None):
    """List configured integrations or submit an exact non-secret YAML preview."""
    try:
        service = runtime()
        actor = principal(request, service)
        configuration = editable_configuration(service)
        if target is None:
            filters(request.GET, allowed=set())
            if request.method == "POST":
                raise ValueError("Choose an integration.")
            records = _records(configuration)
            response = render(
                request,
                "stewardship/integrations.html",
                {
                    "integrations": [
                        {"target": name, "label": label, "configured": name in records}
                        for name, label in LABELS.items()
                    ]
                },
            )
        elif request.method == "POST":
            _selected(configuration, target)
            action = form_action(
                request.POST, preview_fields=set(IntegrationForm(target).fields)
            )
            response = (
                _preview(request, service, actor, target)
                if action == "preview"
                else confirm(
                    request,
                    service,
                    actor,
                    salt=SALT + target,
                    current_scope=lambda service: (
                        editable_configuration(service),
                        None,
                    ),
                )
            )
        else:
            filters(request.GET, allowed=set())
            response = _page(request, configuration, target)
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)


def _context(configuration, target):
    """Bind a candidate to the exact currently applied public provider settings."""
    records = _records(configuration)
    _selected(configuration, target)
    return authentication_scope(
        target, records, recipient=configuration.testing_recipient
    )


def _credential_form(configuration, actor, target):
    """One short-lived server-selected identity permits safe network retry."""
    _handoff(target)
    intent = signing.dumps(
        {
            "actor": str(actor.identity),
            "target": target,
            "base": configuration.active_configuration.digest,
            "request": str(uuid4()),
            "staging": str(uuid4()),
        },
        salt=CREDENTIAL_SALT,
    )
    return CredentialForm(initial={"intent": intent})


def _handoff(target):
    """Map unavailable encryption discovery to a retryable, value-free response."""
    try:
        return public_handoff(target)
    except ConfigError:
        raise IntegrationUnavailable() from None


def _stage(request, configuration, actor, target, form):
    """Seal immediately; only ciphertext, fingerprint and public scope persist."""
    intent = signing.loads(
        form.cleaned_data["intent"], salt=CREDENTIAL_SALT, max_age=300
    )
    if intent["actor"] != str(actor.identity) or intent["target"] != target:
        raise PermissionError("Credential intent does not belong to this session.")
    if intent["base"] != configuration.active_configuration.digest:
        raise StaleRecordError("Integration settings changed.")
    identifier = UUID(intent["request"])
    value = form.cleaned_data.pop("candidate").encode("utf-8")
    from .key_files import MAX_FILE_BYTES

    if len(value) > MAX_FILE_BYTES:
        raise ValueError("Credential exceeds its byte bound.")
    sealed = _handoff(target).seal(identifier, value)
    fingerprint = credential_receipt(value, target)
    del value
    receipt = sealed_secret_request(
        request,
        configuration_digest=intent["base"],
        request_id=identifier,
        target=target,
        staging_reference=UUID(intent["staging"]),
        staging_lifetime=timedelta(hours=1),
        expected_fingerprint=_selected(configuration, target)["values"][
            "credential_fingerprint"
        ],
        correlation_id=current_correlation(),
        sealed_candidate=sealed,
        candidate_fingerprint=fingerprint,
        required_consumers=tuple(
            role.value for role, names in ALLOWED_SECRETS.items() if target in names
        ),
        provider_settings=_context(configuration, target),
    )
    return HttpResponseRedirect(
        f"/admin/configuration/credentials/{receipt.request_id}"
    )


@sensitive_post_parameters("candidate")
@require_http_methods(["GET", "HEAD", "POST"])
def replace_credential(request, target):
    """Fresh Google authentication and CSRF precede private credential submission."""
    try:
        service = runtime()
        actor = principal(request, service)
        configuration = editable_configuration(service)
        if target not in {"parishsoft", "google_workspace", "slack"}:
            raise LookupError("Integration is unavailable.")
        _context(configuration, target)
        require_fresh(request)
        if request.method == "POST":
            if (
                request.FILES
                or set(request.POST) - {"candidate", "intent", "csrfmiddlewaretoken"}
                or any(len(values) != 1 for _, values in request.POST.lists())
            ):
                raise ValueError("Invalid credential fields.")
            form = CredentialForm(request.POST)
            if form.is_valid():
                return _checked(
                    request,
                    service,
                    _stage(request, configuration, actor, target, form),
                )
            status = 400
        else:
            filters(request.GET, allowed=set())
            form = _credential_form(configuration, actor, target)
            status = 200
        response = render(
            request,
            "stewardship/credential-replace.html",
            {
                "form": form,
                "target": target,
                "label": LABELS[target],
            },
            status=status,
        )
        if status == 400:
            response.stewardship_safe_error = True
        return _checked(request, service, response)
    except SecretRequestConflict:
        return error_response(ValueError("Credential intake identity has changed."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD"])
def credential_status(request, request_id):
    """Passive actor-scoped progress does not extend an abandoned Admin session."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        actor = principal(request, service, passive=True)
        editable_configuration(service)
        receipt = secret_request_status(request_id=request_id, actor_id=actor.identity)
        response = render(
            request,
            "stewardship/credential-status.html",
            {
                "receipt": receipt,
                "pending": receipt.state in SECRET_PENDING,
            },
        )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)
