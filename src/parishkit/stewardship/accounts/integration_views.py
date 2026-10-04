"""Admin integration settings and sealed replacement intake; no provider IO in web."""

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
from parishkit.stewardship.service_boundaries import ROTATING_TARGETS
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingError

from . import admin_navigation
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
    InlineCredentialForm,
    IntegrationForm,
)
from .integration_selection import loaded_organization
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_admission import historical_record_id
from .request_patch import OPTIONAL_INTEGRATIONS, build_candidate
from .secret_models import SECRET_PENDING
from .secret_requests import SecretRequestConflict, secret_request_status
from .sessions import authenticated_admin, require_fresh

SALT = "stewardship-integration-settings-v1"
CREDENTIAL_SALT = "stewardship-integration-credential-v1"
# A settings page may stay open while the Administrator finds the new key.
INLINE_INTENT_SECONDS = 3600


# Integrations an Administrator may add or remove from the settings page:
# Slack with its first key, and the off-site backup folder, which has no key
# of its own (it uses the Google Workspace key).
REMOVABLE = OPTIONAL_INTEGRATIONS | {"backup"}


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
    if target in REMOVABLE and target not in _records(configuration):
        return None
    return _selected(configuration, target)


# A stand-in record so an optional integration's page renders before setup.
def _unset(target):
    """Describe an optional integration with no settings and no key."""
    return {
        "id": None,
        "values": {"kind": target, "settings": {}, "credential_fingerprint": None},
    }


def _form(target, *args, **kwargs):
    """The settings form, with ParishSoft's organization ID fixed once loaded."""
    loaded = loaded_organization() if target == "parishsoft" else None
    return IntegrationForm(target, *args, loaded_organization=loaded, **kwargs)


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
    backup = _backup_context(request, configuration) if target == "backup" else {}
    initial = record["values"]["settings"] | {
        "base_digest": configuration.active_configuration.digest
    }
    probe = backup.get("probe")
    if (
        probe is not None
        and not probe.saved_after
        and probe.folder_id != _saved_folder_id(initial.get("target"))
    ):
        # Keep the tested folder in the field while its result is on the page
        # (latest_probe's window), so Save applies it without pasting the link
        # again. The saved link stays as written when it is that same folder,
        # and once any backup folder has been saved since the test, the saved
        # folder shows instead.
        initial["target"] = probe.folder_url
    form = form if form is not None else _form(target, initial=initial)
    latest = summary(target, record) if target in ROTATING_TARGETS else None
    pending = latest is not None and latest.kind == "pending"
    unavailable = False
    if target in ROTATING_TARGETS and credential is None and not pending:
        try:
            credential = _inline_credential_form(configuration, request, target)
        except IntegrationUnavailable:
            # Settings stay editable while the key installer is unavailable.
            unavailable = True
    # The step-up hint is always shown (a sign-in can age while the page is
    # open), so the page no longer needs to know whether this one is fresh.
    # The settings form is the first step of edit, review, apply (#196).
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/integration-settings.html",
        {
            "form": form,
            "credential": None if pending else credential,
            "credential_unavailable": unavailable,
            "target": target,
            "label": LABELS[target],
            # Names the integration in the Admin breadcrumb trail.
            "breadcrumb_label": LABELS[target],
            "summary": latest,
            "pending": pending,
            "configured": configured,
            "removable": configured and target in REMOVABLE,
            "configuration": configuration,
            "full_refresh": (
                full_refresh_status(refresh_schedule(configuration), timezone.now())
                if target == "parishsoft"
                else None
            ),
            "status_url": reverse("admin:integration_status", args=[target]),
            # The ParishSoft page offers the manual full refresh directly.
            "refresh_key": uuid4() if target == "parishsoft" else None,
            **backup,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _saved_folder_id(link):
    """The Drive folder ID in a saved folder link, or None."""
    from parishkit.stewardship.backup_drive import folder_id_from_url

    try:
        return folder_id_from_url(link or "")
    except ValueError:
        return None


def _backup_context(request, configuration):
    """Off-site copy status and the viewer's latest access check."""
    from django.db import transaction

    from parishkit.stewardship.jobs.ownership import database_now

    from .backup_destination import latest_probe, offsite_status

    # The window is judged on the database's clock, which also stamped the
    # check, so clock skew between servers can't hide a fresh result.
    with transaction.atomic():
        probe = latest_probe(request.portal_session.principal_id, database_now())
    return {
        "offsite": offsite_status(),
        "probe": probe,
        "pending": probe is not None and probe.kind == "pending",
        "workspace_configured": "google_workspace" in _records(configuration),
    }


def _refuse_drive_in_local():
    """LOCAL (#476) never reaches Google Drive: no off-site folder is set or tested.

    The Workspace credential in LOCAL is the mail-catcher document, which the
    Drive session refuses anyway; this refusal says so before anything is
    queued or saved.
    """
    from parishkit.stewardship.deployment import DeploymentProfile, recorded_profile

    if recorded_profile() is DeploymentProfile.LOCAL:
        raise UserFacingError(
            _(
                "Off-site Google Drive copies are not available in the local "
                "environment."
            ),
            fix=_(
                "The local laptop environment never reaches Google Drive; its "
                "backups stay inside the VM."
            ),
        )


def _test_access(request, configuration, actor):
    """Queue a "Test access" check of the entered (or saved) Drive folder.

    The check runs as the Google Workspace delegated mailbox user, which is
    the identity the backup copies use, so it proves exactly what they need.
    """
    from parishkit.stewardship.backup_drive import folder_id_from_url

    from .backup_destination import request_probe

    if set(request.POST) - {
        "action",
        "csrfmiddlewaretoken",
        "target",
        "base_digest",
    } or any(len(values) != 1 for _, values in request.POST.lists()):
        raise ValueError("Invalid configuration action or fields.")
    _refuse_drive_in_local()
    records = _records(configuration)
    # Each refusal below says what to do next; a plain ValueError would show
    # only the generic "Check your entries" page.
    if "google_workspace" not in records:
        raise UserFacingError(
            _("Test access needs Google Workspace mail to be set up first."),
            fix=_(
                "Off-site copies are saved as the Google Workspace mailbox user, "
                "so the check signs in as that user. Set up Google Workspace "
                "mail, then come back and choose Test access."
            ),
            link=reverse("admin:integration_settings", args=["google_workspace"]),
            link_label=LABELS["google_workspace"],
        )
    entered = request.POST.get("target", "").strip()
    if not entered and "backup" not in records:
        raise UserFacingError(
            _("Enter the Google Drive folder link first."),
            fix=_(
                "Paste the link of the Google Drive folder that should hold the "
                "off-site copies into Google Drive folder link, then choose Test "
                "access."
            ),
        )
    try:
        folder = folder_id_from_url(
            entered or records["backup"]["values"]["settings"]["target"]
        )
    except ValueError:
        raise UserFacingError(
            _("That is not a link to a Google Drive folder."),
            fix=_(
                "Open the folder in Google Drive and copy its link from the "
                "browser address bar or from Share, then Copy link. It starts "
                "with https://drive.google.com/drive/folders/."
            ),
        ) from None
    request_probe(
        actor.identity,
        folder,
        records["google_workspace"]["values"]["settings"]["delegated_email"],
    )
    return HttpResponseRedirect(request.path)


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
    form = _form(target, request.POST)
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
                raise UserFacingError(
                    _(
                        "Nothing was saved: a new key and a refresh schedule "
                        "change cannot be saved together here."
                    ),
                    fix=_(
                        "Leave the key field empty and save the refresh "
                        "schedule change first. Then paste the new key and save "
                        "again."
                    ),
                )
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
    if target == "backup":
        _refuse_drive_in_local()
    if _optional(configuration, target) is None and target != "backup":
        raise ValueError("Paste the key to set up this integration.")
    record = _optional(configuration, target) or _unset(target)
    form = _form(target, request.POST)
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
        if record["id"] is not None
        else {
            # The off-site folder is added by settings alone; it has no key.
            # Adding it again after removal reuses its stable record ID.
            "operation": "add",
            "section": "integrations",
            "id": historical_record_id(configuration.active_configuration.pk, target)
            or str(uuid4()),
            "values": {
                "kind": target,
                "settings": settings,
                "credential_fingerprint": None,
            },
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    build_candidate(base, patch, candidate_id=uuid4())
    admin_navigation.place(request, flow="change", step="review")
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
    if target not in REMOVABLE:
        raise LookupError("Integration is unavailable.")
    record = _selected(configuration, target)
    patch = [{"operation": "remove", "section": "integrations", "id": record["id"]}]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    # Refuse now, not at confirmation, if the schema would reject the removal.
    build_candidate(base, patch, candidate_id=uuid4())
    admin_navigation.place(request, flow="change", step="review")
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


# A private key pasted by mistake into the backup key field must not reach an
# error report either.
@sensitive_post_parameters("candidate", "public_key", "intent", "code")
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
        elif target == "backup_key":
            # The backup encryption key has its own proof-of-possession steps.
            from .backup_key import backup_key_page

            filters(request.GET, allowed=set())
            response = backup_key_page(request, service, actor)
        elif request.method == "POST" and request.POST.get("action") == "remove":
            response = _remove(request, service, configuration, actor, target)
        elif (
            request.method == "POST"
            and request.POST.get("action") == "test"
            and target == "backup"
        ):
            response = _test_access(request, configuration, actor)
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
    except SecretRequestConflict:
        # A reused page carrying a different key: correctable input, not an
        # outage. The key itself is never echoed.
        return error_response(ValueError("Credential intake identity has changed."))
    except ERRORS as error:
        return error_response(error)


def _handoff(target):
    """Map unavailable encryption discovery to a retryable, value-free response."""
    try:
        return public_handoff(target)
    except ConfigError:
        raise IntegrationUnavailable() from None


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
        if target == "backup":
            with read_transaction():
                configuration = editable_configuration(service)
                context = _backup_context(request, configuration)
            response = render(
                request,
                "stewardship/backup-status.html",
                context
                | {
                    "follow_url": reverse("admin:integration_settings", args=[target]),
                },
            )
            return _checked(request, service, response)
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


def place_key_page(request, target, **flow):
    """Place a key's status or switch page under its integration (#196).

    The routes name only the key's request, so the view supplies the
    integration's route argument and name for the trail and Return link;
    ``flow`` optionally names a step indicator. An unknown target leaves
    the trail at Integrations.
    """
    if target in LABELS:
        admin_navigation.place(
            request,
            arguments={"target": target},
            labels={"integration_settings": LABELS[target]},
            **flow,
        )
    elif flow:
        admin_navigation.place(request, **flow)


def integration_origin(name, arguments):
    """``(arguments, labels)`` naming the integration a change came from (#196).

    A change confirmed on an integration's settings page, or on Finish
    switching for one of its keys, stands under that integration on its
    status page. Finish switching's route names only the key's request, so
    its integration is read from the request. Anything else adds nothing.
    """
    target = arguments.get("target")
    if name == "select_credential":
        from .secret_models import SecretReplacementRequest

        target = (
            SecretReplacementRequest.objects.filter(pk=arguments.get("request_id"))
            .values_list("target", flat=True)
            .first()
        )
    if target not in LABELS:
        return {}, {}
    return {"target": target}, {"integration_settings": LABELS[target]}


def _selectable(configuration, receipt, target):
    """Offer Finish switching only for the key the settings page says needs it.

    An older key, or one whose integration was removed after it was in use,
    is history: selecting it again would bring back a key nobody pasted or
    checked just now (#338 review).
    """
    if receipt.state != "applied":
        return False
    if target not in ROTATING_TARGETS:
        return False
    latest = summary(target, _optional(configuration, target) or _unset(target))
    return (
        latest is not None
        and latest.kind == "unselected"
        and latest.request_id == receipt.request_id
    )


@require_http_methods(["GET", "HEAD"])
def credential_status(request, request_id):
    """Passive actor-scoped progress does not extend an abandoned Admin session."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        actor = principal(request, service, passive=True)
        configuration = editable_configuration(service)
        receipt = secret_request_status(request_id=request_id, actor_id=actor.identity)
        from .secret_models import SecretReplacementRequest

        target = (
            SecretReplacementRequest.objects.filter(pk=receipt.request_id)
            .values_list("target", flat=True)
            .first()
        )
        place_key_page(request, target)
        response = render(
            request,
            "stewardship/credential-status.html",
            {
                "receipt": receipt,
                "pending": receipt.state in SECRET_PENDING,
                "selectable": _selectable(configuration, receipt, target),
            },
        )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)
