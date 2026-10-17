"""Admin integration settings and sealed replacement intake; no provider IO in web."""

from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
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
from parishkit.stewardship.web.error_pages import page_request
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    annotate,
    unexpected_fields,
    with_default_code,
)

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
from .refresh_schedule_forms import (
    ScheduleEditor,
    basic_context,
    differences,
    editor_view,
    posted_names,
)
from .request_admission import historical_record_id
from .request_patch import OPTIONAL_INTEGRATIONS, build_candidate
from .secret_models import SECRET_PENDING
from .secret_requests import SecretRequestConflict, secret_request_status
from .sessions import FreshAuthenticationRequired, authenticated_admin, require_fresh

SALT = "stewardship-integration-settings-v1"
CREDENTIAL_SALT = "stewardship-integration-credential-v1"
# A settings page may stay open while the Administrator finds the new key.
INLINE_INTENT_SECONDS = 3600
# The refusal of a Save with nothing changed (``review_settings``).
UNCHANGED = _("No settings have changed.")


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


def key_summary(configuration, target):
    """The latest key change line for ``target``, or None (request-free).

    The settings page's status line, Finish switching's offer and ``pk-admin
    integration show`` and ``status`` read it here, against the applied
    record (or the stand-in of an optional integration not set up).
    """
    return summary(target, _optional(configuration, target) or _unset(target))


def integration_list(configuration):
    """The Integrations page's rows: each target, its label and whether set up."""
    records = _records(configuration)
    return [
        {"target": name, "label": label, "configured": name in records}
        for name, label in LABELS.items()
    ]


def _form(target, *args, stored=None, **kwargs):
    """The settings form, with ParishSoft's organization ID fixed once loaded.

    ``stored`` is the integration's stored settings record, if any; the
    ParishSoft refresh schedule editor (#632) compares against its schedule.
    """
    loaded = loaded_organization() if target == "parishsoft" else None
    return IntegrationForm(
        target,
        *args,
        loaded_organization=loaded,
        stored=stored["values"]["settings"] if stored is not None else None,
        **kwargs,
    )


def _schedule_zone(configuration):
    """The zone refresh times resolve in (the scheduler's rule, #632).

    The current campaign's zone, else the parish's, as
    ``refresh_status.refresh_schedule`` and ``data_age.source_timezone``
    read it.
    """
    schedule = refresh_schedule(configuration)
    if schedule is not None:
        return schedule["timezone"]
    return configuration.active_configuration.canonical_document["sections"]["parish"][
        0
    ]["values"]["timezone"]


def _schedule_context(configuration, editor):
    """The refresh schedule editor's preview, summary and line beside Save."""
    from parishkit.stewardship.source.schedule_preview import schedule_preview

    zone = _schedule_zone(configuration)
    return {
        "schedule": editor_view(
            editor,
            timezone=zone,
            now=timezone.now(),
            preview_reader=schedule_preview,
        ),
    } | basic_context()


def _checked(request, service, response):
    """Repeat authorization before private settings or progress leave the process."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Integration access was revoked.")
    response["Cache-Control"] = "no-store"
    return response


def _summary(*forms):
    """The error summary of a refused settings page, linking each field (#563).

    The page script focuses the summary on load, so a refusal at a field far
    down the page (the key field) is announced; each item links to its field.
    """
    return [
        {
            "message": message,
            "field_id": None if name == "__all__" else form[name].auto_id,
        }
        for form in forms
        if form is not None and form.is_bound
        for name, messages in form.errors.items()
        for message in messages
    ]


def _page(request, configuration, target, *, form=None, credential=None, status=200):
    """Show editable settings, the optional new-key field and one status line."""
    record = _optional(configuration, target)
    configured = record is not None
    record = record or _unset(target)
    backup = _backup_context(request, configuration) if target == "backup" else {}
    settings = record["values"]["settings"]
    initial = settings | {"base_digest": configuration.active_configuration.digest}
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
    if form is None:
        form = _form(target, initial=initial, stored=record if configured else None)
        if initial.get("target") != settings.get("target"):
            # The tested folder is drawn but not saved: Save's change gate
            # (#1330) compares with the saved link, so Save stays available.
            form.fields["target"].widget.attrs["data-saved-value"] = settings.get(
                "target", ""
            )
    latest = summary(target, record) if target in ROTATING_TARGETS else None
    pending = latest is not None and latest.kind == "pending"
    unavailable = False
    if target in ROTATING_TARGETS and credential is None and not pending:
        try:
            credential = _inline_credential_form(
                configuration, request, target, required=not configured
            )
        except IntegrationUnavailable:
            # Settings stay editable while the key installer is unavailable.
            unavailable = True
    # The step-up hint is always shown (a sign-in can age while the page is
    # open), so the page no longer needs to know whether this one is fresh.
    # The settings form is the first step of edit, review, apply (#196).
    admin_navigation.place(request, flow="change", step="edit")
    page_url = reverse("admin:integration_settings", args=[target])
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
            "errors": _summary(form, credential) if status == 400 else [],
            # Save waits for every required field (#563). ParishSoft's
            # refresh schedule editor gates Save itself (refresh-schedule-v1.js,
            # whose gate does not share the page script's), so it keeps that.
            "complete_gate": form.schedule is None,
            # Save waits for a change (#1330) on the first draw; a refused
            # Save drawn again with what was posted does not, as its fields'
            # drawn values are the posted ones, not the saved ones.
            "change_gate": not form.is_bound,
            "removable": configured and target in REMOVABLE,
            "configuration": configuration,
            "full_refresh": (
                full_refresh_status(refresh_schedule(configuration), timezone.now())
                if target == "parishsoft"
                else None
            ),
            "status_url": reverse("admin:integration_status", args=[target]),
            # Save, Remove and the review dialog's Apply post here (#750).
            "page_url": page_url,
            # Once a change is applied, the dialog redraws the settings
            # form's region from this page's own address.
            "refresh_url": page_url,
            # The ParishSoft page offers the manual full refresh directly.
            "refresh_key": uuid4() if target == "parishsoft" else None,
            **backup,
            **(
                _schedule_context(configuration, form.schedule)
                if form.schedule is not None
                else {}
            ),
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _saved_folder_id(link):
    """The Drive folder ID in a saved folder link, or None."""
    from parishkit.stewardship.backup_drive import folder_id_from_url

    try:
        return folder_id_from_url(link or "")
    except ValueError:
        return None


def _backup_context(request, configuration):
    """Off-site copy status, the viewer's latest access check, and LOCAL.

    ``drive_in_local`` draws Test access unavailable, with why, in LOCAL
    (#563), where the view would only refuse it (``_refuse_drive_in_local``).
    """
    return backup_state(configuration, request.portal_session.principal_id) | {
        "drive_in_local": _drive_in_local()
    }


def backup_state(configuration, actor_id):
    """The backup page's off-site copy status and ``actor_id``'s latest check.

    Request-free, so ``pk-admin integration show backup`` and ``integration
    status backup`` read what the page and its status line show.
    """
    from django.db import transaction

    from parishkit.stewardship.jobs.ownership import database_now

    from .backup_destination import latest_probe, offsite_status

    # The window is judged on the database's clock, which also stamped the
    # check, so clock skew between servers can't hide a fresh result.
    with transaction.atomic():
        probe = latest_probe(actor_id, database_now())
    return {
        "offsite": offsite_status(),
        "probe": probe,
        "pending": probe is not None and probe.kind == "pending",
        "workspace_configured": "google_workspace" in _records(configuration),
    }


def _drive_in_local():
    """Whether this is LOCAL (#476), which never reaches Google Drive."""
    from parishkit.stewardship.deployment import DeploymentProfile, recorded_profile

    return recorded_profile() is DeploymentProfile.LOCAL


def _refuse_drive_in_local():
    """LOCAL (#476) never reaches Google Drive: no off-site folder is set or tested.

    The Workspace credential in LOCAL is the mail-catcher document, which the
    Drive session refuses anyway; this refusal says so before anything is
    queued or saved. The page draws Test access unavailable there already;
    this is the server's own check.
    """
    if _drive_in_local():
        raise UserFacingError(
            _(
                "Off-site Google Drive copies are not available in the local "
                "environment."
            ),
            fix=_(
                "The local laptop environment never reaches Google Drive; its "
                "backups stay inside the VM."
            ),
            reason="drive_not_in_local",
        )


def _test_access(request, configuration, actor):
    """Queue a "Test access" check of the entered (or saved) Drive folder.

    Only the page's own fields may be posted; ``queue_probe`` does the rest.
    An entered link that isn't a Drive folder is refused at the field, in
    Save's own words, with the page drawn again (400, #1330); an empty one
    tests the saved folder.
    """
    allowed = {"action", "csrfmiddlewaretoken", "target", "base_digest"}
    if set(request.POST) - allowed or any(
        len(values) != 1 for _, values in request.POST.lists()
    ):
        raise unexpected_fields(request.POST, allowed=allowed)
    entered = request.POST.get("target", "")
    if entered.strip():
        form = _form("backup", request.POST, stored=_optional(configuration, "backup"))
        if not form.is_valid() and form.has_error("target"):
            return _page(request, configuration, "backup", form=form, status=400)
    queue_probe(configuration, actor, entered)
    return HttpResponseRedirect(request.path)


def queue_probe(configuration, actor, entered):
    """Queue an access check of the ``entered`` (or saved) Drive folder link.

    Request-free, so ``pk-admin integration backup-probe`` queues exactly
    what the page's Test access does. The check runs as the Google Workspace
    delegated mailbox user, which is the identity the backup copies use, so
    it proves exactly what they need; the Workspace installer answers it.
    Returns the queued ``BackupDriveProbe`` row.
    """
    from parishkit.stewardship.backup_drive import folder_id_from_url

    from .backup_destination import request_probe

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
            reason="workspace_mail_missing",
        )
    entered = entered.strip()
    if not entered and "backup" not in records:
        raise UserFacingError(
            _("Enter the Google Drive folder link first."),
            fix=_(
                "Paste the link of the Google Drive folder that should hold the "
                "off-site copies into Google Drive folder link, then choose Test "
                "access."
            ),
            reason="drive_folder_missing",
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
            reason="drive_folder_link_invalid",
        ) from None
    return request_probe(
        actor.identity,
        folder,
        records["google_workspace"]["values"]["settings"]["delegated_email"],
    )


def _inline_credential_form(configuration, request, target, *, required=False):
    """Sign a fresh request identity for the page's new-key field.

    ``required`` is true for an optional integration that is not set up yet,
    whose key is what sets it up (#563).
    """
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
    return InlineCredentialForm(target, initial={"intent": intent}, required=required)


def _refused_key(configuration, request, target, message=None, *, code=None):
    """The new-key field redrawn with a refusal at the field (#563).

    The field is bound empty, with a freshly signed intent, so the page shows
    the error beside the key field (``aria-invalid``, described by the
    message) and never shows the pasted key again. Without ``message``, the
    refusal is the missing key of an integration not set up yet; ``message``
    is any other reason the key could not be saved, and ``code`` its closed
    error code, which the refusal log records (#1437): required with a
    ``message``, so no caller can build a code-less error.
    """
    if message is not None and not code:
        raise TypeError("A refused key's message needs its closed code.")
    blank = _inline_credential_form(
        configuration, request, target, required=message is None
    )
    credential = InlineCredentialForm(
        target,
        {"intent": blank.initial["intent"], "candidate": ""},
        required=message is None,
    )
    if credential.is_valid():
        credential.add_error("candidate", ValidationError(message, code=code))
    return credential


def _save(request, service, configuration, actor, target):
    """Replace the key (and any changed settings) from the settings page.

    Fresh Google authentication is checked before the key is read, so a
    stale session gets the step-up page instead of a sealed request.
    """
    require_fresh(request)
    form = _form(target, request.POST, stored=_optional(configuration, target))
    credential = InlineCredentialForm(target, request.POST)
    if not (form.is_valid() and credential.is_valid()):
        # Redrawn as the first draw does: an optional integration not set up
        # yet keeps its key required, with its hint and the complete gate.
        credential = InlineCredentialForm(
            target,
            initial={"intent": request.POST.get("intent", "")},
            required=_optional(configuration, target) is None,
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
    if form.schedule is not None and form.schedule.changed:
        # A key save never changes the refresh schedule: change it first.
        # The refusal is shown at the key field, with the schedule edits
        # kept on the redrawn page (#563); the key itself is never shown.
        return _page(
            request,
            configuration,
            target,
            form=form,
            credential=_refused_key(
                configuration,
                request,
                target,
                _(
                    "Nothing was saved: a new key and a refresh schedule change "
                    "can't be saved together. Leave this empty and save the "
                    "schedule change first, then paste the new key again."
                ),
                code="key_with_schedule_change",
            ),
            status=400,
        )
    settings = form.public_settings()
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


def review_settings(service, actor, configuration, target, form):
    """The settings page's review of a bound ``form``, or None with its refusal.

    Request-free, so the page's Save and ``pk-admin integration preview``
    share it. A form that does not validate, or whose settings are the
    applied ones ("No settings have changed."), carries its refusal and
    returns None; a form whose version is not the applied one raises
    ``StaleRecordError``; a resulting document the configuration refuses
    raises ``ConfigError`` (from ``build_candidate``). Otherwise it returns
    the stored settings ``before`` and ``after``, the ``patch`` and the
    signed ``preview`` the page's Apply (and ``integration confirm``) takes.
    """
    if not form.is_valid():
        return None
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload integration settings.")
    record = _optional(configuration, target) or _unset(target)
    before = record["values"]["settings"]
    # An unchanged refresh schedule keeps its stored keys exactly (#632), so
    # a page saved as shown is no change at all.
    settings = form.public_settings()
    if settings == before:
        form.add_error(None, ValidationError(UNCHANGED, code="unchanged"))
        return None
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
    _check_candidate(service, configuration, patch)
    return {
        "before": before,
        "after": settings,
        "patch": patch,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=SALT + target,
        ),
    }


def _check_candidate(service, configuration, patch):
    """Refuse now, not at confirmation, a patch the schema would reject."""
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    build_candidate(base, patch, candidate_id=uuid4())


def _preview(request, service, actor, target):
    """The review dialog's answer to Save without a new key (#750).

    A refused value (or the missing key of an optional integration that its
    key sets up, #563) is the settings page again with status 400, so it
    stays on the form. A stale page raises ``StaleRecordError``, and no
    change at all raises its refusal, both shown in the dialog. Otherwise
    the answer is ``integration-review.html``: a compact review of the
    changed settings, the full review of a refresh schedule change, or the
    confirmation of setting Off-site backups up by its folder. The applied
    credential receipt is never shown or changed.
    """
    configuration = editable_configuration(service)
    if target == "backup":
        _refuse_drive_in_local()
    record = _optional(configuration, target) or _unset(target)
    form = _form(target, request.POST, stored=_optional(configuration, target))
    if record["id"] is None and target != "backup":
        # An optional integration is set up by its key (#563): the page
        # requires it, and a request without one is redrawn with the
        # refusal at the key field and the other entries kept.
        form.is_valid()
        return _page(
            request,
            configuration,
            target,
            form=form,
            credential=_refused_key(configuration, request, target),
            status=400,
        )
    review = review_settings(service, actor, configuration, target, form)
    if review is None:
        if form.has_error(NON_FIELD_ERRORS, "unchanged"):
            # The page's change gate keeps this out of reach; it is the
            # server's safeguard for a request that bypasses it.
            raise UserFacingError(UNCHANGED, fix=_("Nothing was changed."))
        return _page(request, configuration, target, form=form, status=400)
    before, settings = review["before"], review["after"]
    schedule_changed = form.schedule is not None and form.schedule.changed
    return render(
        request,
        "stewardship/integration-review.html",
        {
            "kind": (
                "schedule"
                if schedule_changed
                else "setup"
                if record["id"] is None
                else "compact"
            ),
            "target": target,
            "label": LABELS[target],
            # Public settings only (form.fields never holds the key field).
            "changes": [
                {
                    "label": form.fields[name].label,
                    "before": _shown(form.fields[name], before.get(name, "")),
                    "after": _shown(form.fields[name], settings.get(name, "")),
                }
                for name in form.fields
                if name != "base_digest"
                and before.get(name, "") != settings.get(name, "")
            ],
            # The schedule's differences in words, never its stored keys.
            "schedule_changes": (
                differences(
                    before,
                    form.schedule.document(),
                    timezone=_schedule_zone(configuration),
                )
                if schedule_changed
                else []
            ),
            "preview": review["preview"],
        },
    )


def _shown(field, value):
    """A setting's value as the review shows it.

    A choice shows its label ("Once an hour"), not its stored value; a time
    list shows as typed ("02:00, 12:00"). Optional settings (the From name)
    may be absent or empty, shown as "Not set" (as Parish settings' compact
    review shows an unset value).
    """
    if isinstance(value, (list, tuple)):
        value = ", ".join(value)
    return dict(getattr(field, "choices", ())).get(value, value) or _("Not set")


def review_removal(service, actor, configuration, target):
    """The settings page's review of removing ``target``: its signed preview.

    Request-free, so the page's Remove and ``pk-admin integration preview
    --remove`` share it. An integration that cannot be removed, or is not
    set up, raises ``LookupError`` (the page's 404); a removal the schema
    would refuse raises ``ConfigError``, now rather than at confirmation.
    """
    if target not in REMOVABLE:
        raise LookupError("Integration is unavailable.")
    record = _selected(configuration, target)
    patch = [{"operation": "remove", "section": "integrations", "id": record["id"]}]
    _check_candidate(service, configuration, patch)
    return sign_preview(
        actor=actor,
        configuration=configuration,
        patch=patch,
        salt=SALT + target,
    )


def _remove(request, service, configuration, actor, target):
    """The review dialog's confirmation of removing an optional integration.

    Remove Slack and Turn off off-site copies (#750) answer with
    ``integration-review.html``'s confirmation; Apply uses the usual
    confirmation path. Removing Slack stops new Slack alerts at once. Its
    key file stays on the server until it is replaced; setting Slack up
    again installs a new key.
    """
    allowed = {"action", "csrfmiddlewaretoken"}
    if set(request.POST) - allowed or any(
        len(values) != 1 for _, values in request.POST.lists()
    ):
        raise unexpected_fields(request.POST, allowed=allowed)
    preview = review_removal(service, actor, configuration, target)
    return render(
        request,
        "stewardship/integration-review.html",
        {"kind": "remove", "target": target, "preview": preview},
    )


def _step_up_refusal(request, target, error):
    """A stale sign-in's refusal of a review or Apply, for the review dialog.

    Status 403 with the shared step-up notice (``integration-review.html``,
    marked data-review-refusal), whose Confirm with Google returns to the
    settings page (#547, #750). Nothing was reviewed or queued. The refusal
    log records the error's reason, as ``error_response`` would.
    """
    response = render(
        request,
        "stewardship/integration-review.html",
        {"refused": True, "target": target},
        status=403,
    )
    # The answer carries its own explanation; keep it as rendered.
    response.stewardship_safe_error = True
    # The closed code error_response would give it, for the refusal log.
    response.stewardship_codes = ("denied",)
    return annotate(response, error)


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
            response = render(
                request,
                "stewardship/integrations.html",
                {"integrations": integration_list(configuration)},
            )
        elif target == "backup_key":
            # The backup encryption key has its own proof-of-possession steps.
            from .backup_key import backup_key_page

            filters(request.GET, allowed=set())
            response = backup_key_page(request, service, actor)
        elif request.method == "POST" and request.POST.get("action") == "remove":
            require_fresh(request)
            response = _remove(request, service, configuration, actor, target)
        elif (
            request.method == "POST"
            and request.POST.get("action") == "test"
            and target == "backup"
        ):
            response = _test_access(request, configuration, actor)
        elif request.method == "POST":
            # Changing where mail, backups, alerts or ParishSoft reads go, or
            # removing an integration, needs a Google sign-in within five
            # minutes (#547). It is asked before the review page as well as
            # at Confirm, so a stale sign-in is caught before the change is
            # reviewed; the step-up returns to this settings page.
            require_fresh(request)
            _optional(configuration, target)
            fields = set(IntegrationForm(target).fields)
            if target == "parishsoft":
                fields |= posted_names()
            if target in ROTATING_TARGETS:
                fields |= {"candidate", "intent"}
            action = form_action(request.POST, preview_fields=fields)
            # A key was pasted unless the field is empty after str.strip(),
            # the key field's own cleaning; the page script's SERVER_SPACE
            # (ui-v1.js) reads it the same way, so the page submits such a
            # Save natively, never through the review dialog (#750).
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
                        # Checked again under the work lock (#547).
                        fresh=True,
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
    except FreshAuthenticationRequired as error:
        # The review dialog's Save, Remove and Apply (#750) are a script's
        # requests: their step-up is drawn in the dialog. A page's own post
        # (Save with a new key, which submits natively) gets the step-up page,
        # and the backup encryption key's steps keep their own.
        if (
            request.method == "POST"
            and target in LABELS
            and target != "backup_key"
            and not page_request(request)
        ):
            return _step_up_refusal(request, target, error)
        return error_response(error)
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
            latest = key_summary(configuration, target)
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
def refresh_schedule_check(request, target):
    """The refresh schedule editor's read-only live check (#632).

    The page posts the editor's unsaved rows each time typing pauses or a
    row, preset or switch changes. They are read with the same form the save
    uses, checked by the shared validator and previewed; the answer is the
    problems at each row, the preview, the summary and the line beside Save,
    which the page script puts in place. It saves nothing, makes no
    configuration request, writes no audit entry and is never cached; it
    needs the page's own Configure capability. Only the editor's fields (and
    the CSRF token) may be posted: never a pasted key.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        principal(request, service)
        if target != "parishsoft":
            raise LookupError("Integration is unavailable.")
        allowed = posted_names() | {"csrfmiddlewaretoken"}
        if set(request.POST) - allowed or any(
            len(values) != 1 for _, values in request.POST.lists()
        ):
            raise unexpected_fields(request.POST, allowed=allowed)
        with read_transaction():
            configuration = editable_configuration(service)
            record = _selected(configuration, target)
            editor = ScheduleEditor(request.POST, stored=record["values"]["settings"])
            if not editor.posted:
                # No sources: the fields were allowed; none of the editor's was sent.
                raise unexpected_fields()
            context = _schedule_context(configuration, editor)
        response = render(request, "stewardship/refresh-schedule-check.html", context)
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
    latest = key_summary(configuration, target)
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
