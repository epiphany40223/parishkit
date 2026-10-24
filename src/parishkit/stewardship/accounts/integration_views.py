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
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
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
from .secret_requests import SecretRequestConflict
from .sessions import authenticated_admin, automatic_read

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
    """The settings form for ``target``.

    ``stored`` is the integration's stored settings record, if any; the
    ParishSoft refresh schedule editor (#632) compares against its schedule,
    and ParishSoft's organization ID is carried over from it (#1554).
    """
    return IntegrationForm(
        target,
        *args,
        stored=stored["values"]["settings"] if stored is not None else None,
        **kwargs,
    )


_LOOKUP = object()  # `_page`: look the organization up itself


def _organization(configuration, target):
    """ParishSoft's organization named "Name (ID)" (#1554), else None.

    The configured organization, with the name the current snapshot's load
    recorded (``current_organization_label``): the page's organization
    line and its key's help name it. None for another integration or a
    ParishSoft record with no organization. One query for ParishSoft.
    """
    from parishkit.stewardship.source.organization_names import (
        current_organization_label,
    )

    if target != "parishsoft":
        return None
    record = _optional(configuration, target)
    settings = {} if record is None else record["values"]["settings"]
    if "organization_id" not in settings:
        return None
    return current_organization_label(settings["organization_id"])


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


def _page(
    request,
    configuration,
    target,
    *,
    form=None,
    credential=None,
    status=200,
    organization=_LOOKUP,
):
    """Show editable settings, the optional new-key field and one status line.

    ``organization`` is ParishSoft's named organization when the caller has
    already looked it up (a refused key does, #1554), so one request costs
    one lookup; left out, the page looks it up itself.
    """
    from parishkit.stewardship.source.loading import snapshot_organizations

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
    if organization is _LOOKUP:
        organization = _organization(configuration, target)
    if target in ROTATING_TARGETS and credential is None and not pending:
        try:
            credential = _inline_credential_form(
                configuration,
                request,
                target,
                required=not configured,
                organization=organization,
            )
        except IntegrationUnavailable:
            # Settings stay editable while the key installer is unavailable.
            unavailable = True
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
            # ParishSoft's organization line, text above the key (#1554).
            "organization": organization,
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
            # A key that reaches several organizations (#1554): what the
            # current snapshot's load recorded, None otherwise.
            "organizations": (
                snapshot_organizations() if target == "parishsoft" else None
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
    """Whether Drive is closed here: LOCAL without its real-key terms (#1399).

    Off-site copies sign in with the Workspace key, so the local environment
    reaches Drive only where a real key may be used
    (``mail_catcher.admit_real_workspace``): with a mail redirect, on the
    normal clock. Every other profile is never closed here. A redirect that
    the recorded settings refuse counts as closed.
    """
    from parishkit.config import ConfigError
    from parishkit.stewardship.deployment import (
        DeploymentProfile,
        recorded_mail_redirect,
        recorded_profile,
    )
    from parishkit.stewardship.mail_catcher import admit_real_workspace

    profile = recorded_profile()
    if profile is not DeploymentProfile.LOCAL:
        return False
    try:
        admit_real_workspace(profile, recorded_mail_redirect())
    except ConfigError:
        return True
    return False


def _refuse_drive_in_local():
    """No off-site folder is set or tested where Drive is closed (#476, #1399).

    In the local environment Drive opens only with a mail redirect on the
    normal clock (``_drive_in_local``); the Drive session refuses anything
    else anyway, and this refusal says so before anything is queued or
    saved. The page draws Test access unavailable there already; this is
    the server's own check.
    """
    if _drive_in_local():
        raise UserFacingError(
            _(
                "Off-site Google Drive copies are not available in this local "
                "environment."
            ),
            fix=_(
                "The local laptop environment reaches Google Drive only with a "
                "mail redirect (deploy --mail-redirect) and the normal clock; "
                "until then its backups stay inside the VM."
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


def _inline_credential_form(
    configuration, request, target, *, required=False, organization=None
):
    """Sign a fresh request identity for the page's new-key field.

    ``required`` is true for an optional integration that is not set up yet,
    whose key is what sets it up (#563). ``organization`` is ParishSoft's
    named organization (``_organization``), which its help names; it is
    read here when not given.
    """
    intent = sign_key_intent(configuration, request.portal_session.principal_id, target)
    return InlineCredentialForm(
        target,
        initial={"intent": intent},
        required=required,
        organization=organization or _organization(configuration, target),
    )


def sign_key_intent(configuration, actor_id, target):
    """A fresh signed request identity for one new key (request-free).

    The settings page draws it in its new-key field and ``pk-admin
    integration key replace`` signs one per run: it fixes the secret
    request's and staging's identities and the configuration it was drawn
    against. The target's installer must have published its handoff;
    otherwise ``IntegrationUnavailable``.
    """
    _handoff(target)
    return signing.dumps(
        {
            "actor": str(actor_id),
            "target": target,
            "base": configuration.active_configuration.digest,
            "request": str(uuid4()),
            "staging": str(uuid4()),
        },
        salt=CREDENTIAL_SALT,
    )


def _refused_key(
    configuration, request, target, message=None, *, code=None, organization=_LOOKUP
):
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
    if organization is _LOOKUP:
        organization = _organization(configuration, target)
    blank = _inline_credential_form(
        configuration,
        request,
        target,
        required=message is None,
        organization=organization,
    )
    credential = InlineCredentialForm(
        target,
        {"intent": blank.initial["intent"], "candidate": ""},
        required=message is None,
        organization=organization,
    )
    if credential.is_valid():
        credential.add_error("candidate", ValidationError(message, code=code))
    return credential


def _refused_key_page(request, configuration, target, form, *args, **kwargs):
    """A 400 page with the key field refused, looking the organization up once.

    ``args`` and ``kwargs`` are ``_refused_key``'s message and code. The page
    and the refused field both name the organization (#1554), so it is looked
    up here and passed to both.
    """
    organization = _organization(configuration, target)
    return _page(
        request,
        configuration,
        target,
        form=form,
        credential=_refused_key(
            configuration,
            request,
            target,
            *args,
            organization=organization,
            **kwargs,
        ),
        status=400,
        organization=organization,
    )


def _key_with_schedule(request, configuration, target, form):
    """Refuse a new key saved with a refresh schedule change, at the key field.

    A key save never changes the refresh schedule: change it first. The
    refusal is shown at the key field, with the schedule edits kept on the
    redrawn page (#563); the key itself is never shown. The key's review
    (``_key_review``) and its save (``_save``) both refuse it.
    """
    return _refused_key_page(
        request,
        configuration,
        target,
        form,
        _(
            "Nothing was saved: a new key and a refresh schedule change "
            "can't be saved together. Leave this empty and save the "
            "schedule change first, then paste the new key again."
        ),
        code="key_with_schedule_change",
    )


def _refused_entries(request, configuration, target, form):
    """The settings page redrawn with a key save's refused entries (status 400).

    Redrawn as the first draw does: an optional integration not set up yet
    keeps its key required, with its hint and the complete gate. The key
    field is drawn empty; only its signed request is kept.
    """
    credential = InlineCredentialForm(
        target,
        initial={"intent": request.POST.get("intent", "")},
        required=_optional(configuration, target) is None,
        organization=_organization(configuration, target),
    )
    return _page(
        request,
        configuration,
        target,
        form=form,
        credential=credential,
        status=400,
    )


def _key_review(request, configuration, actor, target):
    """The review dialog's confirmation of Save with a new key pasted (#1568).

    The page script posts the form without the key (``key_review``), so
    the key never reaches a review and nothing is sealed or recorded here:
    the key goes only with Apply, which posts the page's own form to
    ``_save``. This checks everything ``_save`` checks before staging,
    except the key itself, so a refusal is said before Apply: a refused
    value (the organization lock, #1496) or a key with a refresh schedule
    change is the settings page again with status 400, at its field, with
    the dialog shut; an out-of-date page or a key field's request that is
    expired, altered or another Administrator's, or an earlier key change
    still installing (409), raises its refusal, which the dialog shows.
    Otherwise the answer is ``integration-review.html``'s
    key confirmation, listing any other setting saved with the key, old
    -> new. The browser names the key by its last characters; the server
    draws no part of it.
    """
    record = _optional(configuration, target)
    # The page hides the key field while a key change is installing, so
    # only another tab gets here; refuse now rather than let its Apply meet
    # the intake guard's generic "temporarily unavailable" answer.
    latest = key_summary(configuration, target)
    if latest is not None and latest.kind == "pending":
        raise UserFacingStale(
            _("A new key is already being installed."),
            fix=_("Reload the page to follow it, then paste the key again."),
        )
    form = _form(target, request.POST, stored=record)
    # The key field's own form, without the key: only its signed request.
    credential = InlineCredentialForm(
        target, {"intent": request.POST.get("intent", ""), "candidate": ""}
    )
    if not (form.is_valid() and credential.is_valid()):
        return _refused_entries(request, configuration, target, form)
    key_intent(configuration, actor, target, form, credential)
    if form.schedule is not None and form.schedule.changed:
        return _key_with_schedule(request, configuration, target, form)
    before = (record or _unset(target))["values"]["settings"]
    return render(
        request,
        "stewardship/integration-review.html",
        {
            "kind": "key",
            "target": target,
            "label": LABELS[target],
            "setup": record is None,
            "changes": _changed_settings(form, before, form.public_settings()),
        },
    )


def _save(request, service, configuration, actor, target):
    """Replace the key (and any changed settings) from the settings page.

    The review dialog's Apply of a key's confirmation (``_key_review``)
    posts the page's own form here, key included, without a page load; the
    answer is this page again, which the dialog redraws in place.
    """
    form = _form(target, request.POST, stored=_optional(configuration, target))
    credential = InlineCredentialForm(target, request.POST)
    refused = [
        error
        for error in (
            credential.errors.as_data().get("candidate", [])
            if form.is_valid() and not credential.is_valid()
            else []
        )
        if error.code == "invalid_credential"
    ]
    if refused:
        # A pasted key its rule refuses (#1577) is refused at the key field,
        # in the rule's words; the key is never drawn again. Any other
        # refusal of the key (a forged oversized post) is drawn as before.
        error = refused[0]
        return _refused_key_page(
            request, configuration, target, form, error.message, code=error.code
        )
    if not (form.is_valid() and credential.is_valid()):
        return _refused_entries(request, configuration, target, form)
    intent = key_intent(configuration, actor, target, form, credential)
    if form.schedule is not None and form.schedule.changed:
        return _key_with_schedule(request, configuration, target, form)
    stage_key(
        request,
        service,
        configuration,
        actor,
        target,
        form=form,
        credential=credential,
        intent=intent,
    )
    return HttpResponseRedirect(request.path)


def key_intent(configuration, actor, target, form, credential):
    """The verified signed intent of a validated key save (request-free).

    ``form`` and ``credential`` are the bound, valid settings and new-key
    forms. A version other than the applied one raises ``StaleRecordError``;
    an intent older than an hour, altered or signed for another
    Administrator or integration is refused (``BadSignature`` or
    ``PermissionError``). The page's Save with a key and ``pk-admin
    integration key replace`` share it.
    """
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
    return intent


def stage_key(
    caller,
    service,
    configuration,
    actor,
    target,
    *,
    form,
    credential,
    intent,
    attach=None,
):
    """Seal the new key and queue its selection with the form's settings.

    Request-free, so the page's Save with a key and ``pk-admin integration
    key replace`` stage it identically through ``save_credential``: sealed
    for the target's installer, staged under the SQL intake guard, then the
    selection request (with ``attach`` in its transaction). Returns the
    secret request's id. The plain key is taken out of the form's cleaned
    data and dropped here; it is never returned, logged or stored.
    """
    from .key_files import MAX_FILE_BYTES

    record = _optional(configuration, target)
    settings = form.public_settings()
    value = credential.cleaned_data.pop("candidate").encode("utf-8")
    if len(value) > MAX_FILE_BYTES:
        raise ValueError("Credential exceeds its byte bound.")
    try:
        return save_credential(
            caller,
            service,
            configuration,
            actor,
            target=target,
            intent=intent,
            value=value,
            record=record,
            settings=settings,
            attach=attach,
        )
    finally:
        del value


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
        return _refused_key_page(request, configuration, target, form)
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
            "changes": _changed_settings(form, before, settings),
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


def _changed_settings(form, before, settings):
    """The review's lines for each setting ``settings`` changes from ``before``.

    Public settings only, in the form's order, each as its label and its old
    and new value in words (``_shown``); ``form.fields`` never holds the key
    field.
    """
    return [
        {
            "label": form.fields[name].label,
            "before": _shown(form.fields[name], before.get(name, "")),
            "after": _shown(form.fields[name], settings.get(name, "")),
        }
        for name in form.fields
        if name != "base_digest" and before.get(name, "") != settings.get(name, "")
    ]


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


# A private key pasted by mistake into the backup key field must not reach an
# error report either.
@sensitive_post_parameters("candidate", "public_key", "intent", "code")
@require_http_methods(["GET", "HEAD", "POST"])
def integration_settings(request, target=None):
    """List configured integrations or submit an exact non-secret YAML preview."""
    try:
        service = runtime()
        # The page's own quiet re-read once a key change or a backup check
        # settles (#1985) is passive, so it never extends the sign-in.
        actor = principal(request, service, passive=automatic_read(request))
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
            response = _remove(request, service, configuration, actor, target)
        elif request.method == "POST" and request.POST.get("action") == "finish":
            from .integration_selection_views import review_finish

            response = review_finish(request, service, actor, target)
        elif (
            request.method == "POST"
            and request.POST.get("action") == "confirm"
            and "request_id" in request.POST
        ):
            from .integration_selection_views import confirm_finish

            response = confirm_finish(request, service, actor, target)
        elif (
            request.method == "POST"
            and request.POST.get("action") == "test"
            and target == "backup"
        ):
            response = _test_access(request, configuration, actor)
        elif request.method == "POST":
            _optional(configuration, target)
            fields = set(IntegrationForm(target).fields)
            if target == "parishsoft":
                fields |= posted_names()
            if target in ROTATING_TARGETS:
                fields |= {"candidate", "intent", "key_review"}
            action = form_action(request.POST, preview_fields=fields)
            # A key was pasted unless the field is empty after str.strip(),
            # the key field's own cleaning; the page script's SERVER_SPACE
            # (ui-v1.js) reads it the same way. Its Review (key_review,
            # #1568) is posted without the key, which goes only with Apply.
            pasted = request.POST.get("candidate", "").strip()
            if action == "preview" and "key_review" in request.POST:
                if pasted or request.FILES or request.POST["key_review"] != "yes":
                    # A review never takes a key, not even as an uploaded
                    # file; the refusal names no field, so the refusal log
                    # never records the pasted value's.
                    # No sources: the fields are allowed, their values are not.
                    raise unexpected_fields()
                response = _key_review(request, configuration, actor, target)
            elif action == "preview" and pasted:
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
                        privileged=True,
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
    an otherwise idle login alive. When the change finishes, the fragment's
    follow-up link redraws the page's status line and editor in place, once
    (#1568).
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
        page_url = reverse("admin:integration_settings", args=[target])
        response = render(
            request,
            "stewardship/integration-status.html",
            {
                "target": target,
                "summary": latest,
                "pending": pending,
                "follow_url": page_url,
                # Finish switching's Review posts to the settings page.
                "page_url": page_url,
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


def integration_origin(name, arguments):
    """``(arguments, labels)`` naming the integration a change came from (#196).

    A change confirmed on an integration's settings page (Finish switching
    to a new key included, #1568) stands under that integration on its
    status page. Anything else adds nothing.
    """
    target = arguments.get("target")
    if target not in LABELS:
        return {}, {}
    return {"target": target}, {"integration_settings": LABELS[target]}


def _selectable(configuration, receipt, target):
    """Whether ``pk-admin integration key status`` reports Finish switching.

    Offered only for the key the settings page says needs it.

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
