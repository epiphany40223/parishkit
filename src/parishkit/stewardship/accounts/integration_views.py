"""Admin integration settings and sealed replacement intake; no provider IO in web."""

from datetime import timedelta
from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS, ROTATING_TARGETS
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)
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
from .integration_credentials import dismiss, save_credential, summary
from .integration_forms import (
    LABELS,
    CredentialForm,
    InlineCredentialForm,
    IntegrationForm,
)
from .integration_selection import authentication_scope
from .limiting import LimiterUnavailable
from .metrics_credentials import credential_receipt
from .policy import Capability, allows
from .privileged_actions import sealed_secret_request
from .request_patch import OPTIONAL_INTEGRATIONS, build_candidate
from .secret_models import SECRET_PENDING
from .secret_requests import SecretRequestConflict, secret_request_status
from .sessions import FreshAuthenticationRequired, authenticated_admin, require_fresh

SALT = "stewardship-integration-settings-v1"
CREDENTIAL_SALT = "stewardship-integration-credential-v1"
# A settings page may stay open while the Administrator finds the new key.
INLINE_INTENT_SECONDS = 3600


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


def _optional(configuration, target):
    """Return the record, or None for an optional integration not yet set up."""
    if target in OPTIONAL_INTEGRATIONS and target not in _records(configuration):
        return None
    return _selected(configuration, target)


# A stand-in record so an optional integration's page renders before setup.
def _unset(target):
    """Describe an optional integration with no settings and no key."""
    return {
        "id": None,
        "values": {"kind": target, "settings": {}, "credential_fingerprint": None},
    }


def _checked(request, service, response):
    """Repeat authorization before private settings or progress leave the process."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Integration access was revoked.")
    response["Cache-Control"] = "no-store"
    return response


def _page(request, configuration, target, *, form=None, credential=None, status=200):
    """Show editable settings, the optional new-key field and one status line."""
    record = _optional(configuration, target)
    configured = record is not None
    record = record or _unset(target)
    form = (
        form
        if form is not None
        else IntegrationForm(
            target,
            initial=record["values"]["settings"]
            | {"base_digest": configuration.active_configuration.digest},
        )
    )
    latest = summary(target, record) if target in ROTATING_TARGETS else None
    pending = latest is not None and latest.kind == "pending"
    unavailable = False
    if target in ROTATING_TARGETS and credential is None and not pending:
        try:
            credential = _inline_credential_form(configuration, request, target)
        except IntegrationUnavailable:
            # Settings stay editable while the key installer is unavailable.
            unavailable = True
    try:
        require_fresh(request)
        fresh = True
    except FreshAuthenticationRequired:
        fresh = False
    response = render(
        request,
        "stewardship/integration-settings.html",
        {
            "form": form,
            "credential": None if pending else credential,
            "credential_unavailable": unavailable,
            "fresh": fresh,
            "target": target,
            "label": LABELS[target],
            # Names the integration in the Admin breadcrumb trail.
            "breadcrumb_label": LABELS[target],
            "summary": latest,
            "pending": pending,
            "configured": configured,
            "removable": configured and target in OPTIONAL_INTEGRATIONS,
            "configuration": configuration,
            "full_refresh": (
                full_refresh_status(refresh_schedule(configuration), timezone.now())
                if target == "parishsoft"
                else None
            ),
            "status_url": reverse("admin:integration_status", args=[target]),
            # The ParishSoft page offers the manual full refresh directly.
            "refresh_key": uuid4() if target == "parishsoft" else None,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _inline_credential_form(configuration, request, target):
    """Sign a fresh request identity for the page's optional new-key field."""
    _handoff(target)
    intent = signing.dumps(
        {
            "actor": str(request.portal_session.principal_id),
            "target": target,
            "base": configuration.active_configuration.digest,
            "request": str(uuid4()),
            "staging": str(uuid4()),
        },
        salt=CREDENTIAL_SALT,
    )
    return InlineCredentialForm(target, initial={"intent": intent})


def _retain_unused_time(target, settings, before):
    """Keep the stored daily time when the refresh is hourly or every 15 minutes.

    The page hides (and the browser does not send) the daily time for those
    frequencies, so an empty or stale value there is never a change to review.
    """
    if target == "parishsoft" and settings.get("full_refresh", "daily") != "daily":
        settings = settings | {"nightly_time": before.get("nightly_time", "02:00")}
    return settings


def _save(request, service, configuration, actor, target):
    """Replace the key (and any changed settings) from the settings page.

    Fresh Google authentication is checked before the key is read, so a
    stale session gets the step-up page instead of a sealed request.
    """
    require_fresh(request)
    form = IntegrationForm(target, request.POST)
    credential = InlineCredentialForm(target, request.POST)
    if not (form.is_valid() and credential.is_valid()):
        credential = InlineCredentialForm(
            target, initial={"intent": request.POST.get("intent", "")}
        )
        return _page(
            request,
            configuration,
            target,
            form=form,
            credential=credential,
            status=400,
        )
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload integration settings.")
    intent = signing.loads(
        credential.cleaned_data["intent"],
        salt=CREDENTIAL_SALT,
        max_age=INLINE_INTENT_SECONDS,
    )
    if intent["actor"] != str(actor.identity) or intent["target"] != target:
        raise PermissionError("Credential intent does not belong to this session.")
    if intent["base"] != configuration.active_configuration.digest:
        raise StaleRecordError("Integration settings changed.")
    record = _optional(configuration, target)
    settings = form.public_settings()
    before = record["values"]["settings"] if record is not None else settings
    settings = _retain_unused_time(target, settings, before)
    if target == "parishsoft":
        # Refresh defaults are implied, not stored, in older settings. A key
        # save never upgrades the settings schema: change the schedule first.
        for name, default in (("nightly_time", "02:00"), ("full_refresh", "daily")):
            if name in before:
                continue
            if settings.get(name) != default:
                raise ValueError("Save the refresh schedule separately from a new key.")
            settings.pop(name)
    from .key_files import MAX_FILE_BYTES

    value = credential.cleaned_data.pop("candidate").encode("utf-8")
    if len(value) > MAX_FILE_BYTES:
        raise ValueError("Credential exceeds its byte bound.")
    save_credential(
        request,
        service,
        configuration,
        actor,
        target=target,
        intent=intent,
        value=value,
        record=record,
        settings=settings,
    )
    del value
    return HttpResponseRedirect(request.path)


def _preview(request, service, actor, target):
    """Prepare public settings while preserving the applied credential receipt."""
    configuration = editable_configuration(service)
    if _optional(configuration, target) is None:
        raise ValueError("Paste the key to set up this integration.")
    record = _selected(configuration, target)
    form = IntegrationForm(target, request.POST)
    if not form.is_valid():
        return _page(request, configuration, target, form=form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload integration settings.")
    before = record["values"]["settings"]
    if target == "parishsoft":
        before = {"full_refresh": "daily", "nightly_time": "02:00"} | before
    settings = _retain_unused_time(target, form.public_settings(), before)
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
            "breadcrumb_label": LABELS[target],
            "configuration": configuration,
            "changes": [
                {
                    "label": form.fields[name].label,
                    # Show a choice's label ("Once an hour"), not its stored
                    # value. Optional settings (the From name) may be absent.
                    "before": dict(getattr(form.fields[name], "choices", ())).get(
                        before.get(name, ""), before.get(name, "")
                    ),
                    "after": dict(getattr(form.fields[name], "choices", ())).get(
                        settings.get(name, ""), settings.get(name, "")
                    ),
                }
                for name in form.fields
                if name != "base_digest"
                and before.get(name, "") != settings.get(name, "")
            ],
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT + target,
            ),
        },
    )


def _remove(request, service, configuration, actor, target):
    """Preview removing an optional integration; confirmation uses the usual path.

    Removing Slack stops new Slack alerts at once. Its key file stays on the
    server until it is replaced; setting Slack up again installs a new key.
    """
    if set(request.POST) - {"action", "csrfmiddlewaretoken"} or any(
        len(values) != 1 for _, values in request.POST.lists()
    ):
        raise ValueError("Invalid configuration action or fields.")
    if target not in OPTIONAL_INTEGRATIONS:
        raise LookupError("Integration is unavailable.")
    record = _selected(configuration, target)
    patch = [{"operation": "remove", "section": "integrations", "id": record["id"]}]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    # Refuse now, not at confirmation, if the schema would reject the removal.
    build_candidate(base, patch, candidate_id=uuid4())
    return render(
        request,
        "stewardship/integration-preview.html",
        {
            "target": target,
            "label": LABELS[target],
            "breadcrumb_label": LABELS[target],
            "configuration": configuration,
            "removing": True,
            "changes": [
                {"label": LABELS[target], "before": _("On"), "after": _("Removed")}
            ],
            "preview": sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT + target,
            ),
        },
    )


@sensitive_post_parameters("candidate")
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
        elif request.method == "POST" and request.POST.get("action") == "remove":
            response = _remove(request, service, configuration, actor, target)
        elif request.method == "POST":
            _optional(configuration, target)
            fields = set(IntegrationForm(target).fields)
            if target in ROTATING_TARGETS:
                fields |= {"candidate", "intent"}
            action = form_action(request.POST, preview_fields=fields)
            if action == "preview" and request.POST.get("candidate", "").strip():
                response = _save(request, service, configuration, actor, target)
            else:
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
            if target not in LABELS:
                raise LookupError("Integration is unavailable.")
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
                "breadcrumb_label": _("Replace %(label)s credential")
                % {"label": LABELS[target]},
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
def integration_status(request, target):
    """Passive credential status line that a settings page follows while pending.

    live-status-v1.js re-reads this fragment instead of reloading the settings
    page, whose ordinary view counts as activity: an open page must never keep
    an otherwise idle login alive. When the change finishes, the fragment
    points the page back at its settings view once.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        principal(request, service, passive=True)
        if target not in ROTATING_TARGETS or target not in LABELS:
            raise LookupError("Integration is unavailable.")
        # One consistent snapshot without the writers' work lock, so polling
        # never waits behind an installer or a source promotion.
        with read_transaction():
            configuration = editable_configuration(service)
            record = _optional(configuration, target) or _unset(target)
            latest = summary(target, record)
        pending = latest is not None and latest.kind == "pending"
        response = render(
            request,
            "stewardship/integration-status.html",
            {
                "target": target,
                "summary": latest,
                "pending": pending,
                "follow_url": reverse("admin:integration_settings", args=[target]),
            },
        )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["POST"])
def dismiss_credential_result(request, target):
    """Hide a finished key change's status line for every Administrator.

    One CSRF-protected POST under the Configure capability; the dismissal is
    recorded as an audit event in its own transaction, then the settings page
    is shown again. An out-of-date request dismisses nothing.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        actor = principal(request, service)
        if target not in ROTATING_TARGETS or target not in LABELS:
            raise LookupError("Integration is unavailable.")
        request_id = UUID(request.POST.get("request_id", ""))
        configuration = editable_configuration(service)
        dismiss(
            target,
            _optional(configuration, target) or _unset(target),
            request_id,
            actor_id=actor.identity,
            parish_id=configuration.active_configuration.parish.pk,
        )
        response = HttpResponseRedirect(
            reverse("admin:integration_settings", args=[target])
        )
        return _checked(request, service, response)
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
