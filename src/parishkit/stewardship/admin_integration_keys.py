"""A key change's status, Finish switching and key replacement (ADM-11 PR 10).

``integration key status`` is a key change's status, which the settings
page's status line shows, and ``integration key finish-switching-preview``
and ``integration key finish-switching`` are the settings page's **Finish
switching to the new key** review and its Apply, in the page's review dialog
(#1568; until then, the separate Key replacement status and Finish switching
pages), through the page's own pieces (``secret_request_status`` and
``_selectable``; ``_selection``, ``_changes``, the page's salt and
``confirm_intent``), so the page and the command line cannot drift:

- ``integration key status TARGET [--request-id ID]`` admits passively, as
  the page does, and reads one of the session Administrator's own key
  changes (by default the latest for TARGET): only its id, its state,
  whether it is still running and whether the page offers Finish switching.
- ``integration key finish-switching-preview TARGET`` admits as the page
  does (recording activity, so it needs a full-scope session), passes the
  page's privileged check with the live session, and prints the
  page's review of the switch, with ``preview.token``, the same signed
  binding the page embeds. It changes nothing and records no event.
- ``integration key finish-switching TARGET --token TOKEN`` takes that
  token (or one the page signed) and records the configuration request
  exactly as the page's confirm button does, with the privileged check under the
  work lock, ``admin_cmd_integration_key_finish_switching`` and, as it
  selects a new integration key, a ``policy_change`` notice.
- ``integration key replace TARGET`` is a settings page's **Save** with a
  new key pasted: the page's forms (``IntegrationForm`` and
  ``InlineCredentialForm``) and its request-free ``key_intent`` and
  ``stage_key``, so the key is sealed for the target's installer and
  staged exactly as from the page, never written anywhere directly. The
  host wrapper reads the key from a private file or standard input and
  sends it after the preamble on one tagged base64 line (see
  ``admin_cli.read_secret``); it is never an argument.

No key material is ever printed, logged or recorded here, and no output
field names a fingerprint: the commands print only request ids, states and
the public settings the page's table shows. ``preview.token`` is the page's
own signed (not encrypted) binding of the reviewed patch, which includes the
new key's fingerprint, exactly as the page's hidden form field does; the
guide recommends passing it back as ``--token -``.
"""

from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import _admit_change, _ended_or_raise
from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request

# The integrations whose key the settings pages replace (``ROTATING_TARGETS``),
# spelled out for the parser, which is built before Django is set up; a test
# keeps them equal.
KEY_TARGETS = ("parishsoft", "google_workspace", "slack")
# The page's own refusal of a switch that would change the loaded ParishSoft
# organization (``integration_selection_views.ORGANIZATION_LOCKED``, which a
# test keeps equal).
ORGANIZATION_LOCKED = (
    "This key was saved with a different ParishSoft organization ID. The "
    "organization ID can't change after ParishSoft data has been loaded, so "
    "this switch was refused and nothing was changed."
)


def _organization_locked():
    """The page's refusal of a changed loaded organization, at its field."""
    from .admin_changes import InvalidChange

    return InvalidChange(
        [
            {
                "field": "organization_id",
                "code": "invalid",
                "message": ORGANIZATION_LOCKED,
            }
        ]
    )


@dataclass(frozen=True)
class KeyStatus(ReadModel):
    """``integration key status``: one key change, as its status page shows it.

    ``state`` is the page's technical state (``staged``, ``testing``,
    ``awaiting_ack``, ``applied``, ``cleanup_pending``, ``expired``, ...);
    ``pending`` whether it is still being checked or installed (a watch stops
    once it is not); ``selectable`` whether the page offers Finish switching.
    As on the page, a change is no longer pending once the key is installed
    and acknowledged, even while its automatic switch is still queued; the
    settings page's line (``integration status``) says whether that switch
    finished.
    """

    target: str
    request_id: str
    state: str
    pending: bool
    selectable: bool

    @property
    def terminal(self):
        """A watch stops once the key change is no longer running."""
        return not self.pending


@dataclass(frozen=True)
class FinishPreview(ReadModel):
    """``integration key finish-switching-preview``: the page's review.

    ``changes`` are the settings saved with the key that the switch also
    applies (``field``, ``before``, ``after``), as the page's table lists
    them; ``preview.token`` is what ``integration key finish-switching``
    takes.
    """

    version: str
    target: str
    name: str
    request_id: str
    changes: list
    preview: dict


@dataclass(frozen=True)
class FinishConfirm(ReadModel):
    """``integration key finish-switching``: the recorded request.

    ``created`` is false for a repeat of the same token, which returns the
    original request.
    """

    created: bool
    request: dict


def read_key_status(caller, service, target, request_id=None):
    """``integration key status``: one of the Administrator's own key changes.

    As the page, only the session Administrator's own key changes are read
    (``secret_request_status``): another Administrator's, another
    integration's or an unknown one is ``not_available``, the page's 404.
    Without ``request_id`` it is the Administrator's latest for ``target``.
    """
    from .accounts.integration_views import _selectable
    from .accounts.secret_models import SECRET_PENDING, SecretReplacementRequest
    from .accounts.secret_requests import secret_request_status
    from .admin_integrations import _read

    def read(configuration, actor):
        """The page's receipt and its Finish switching offer, in one snapshot."""
        rows = SecretReplacementRequest.objects.filter(
            target=target, requested_by_id=actor.identity
        )
        if request_id is not None:
            rows = rows.filter(pk=request_id)
        chosen = (
            rows.order_by("-created_at", "-pk").values_list("pk", flat=True).first()
        )
        if chosen is None:
            raise LookupError("No such key change.")
        receipt = secret_request_status(request_id=chosen, actor_id=actor.identity)
        return KeyStatus(
            target=target,
            request_id=str(receipt.request_id),
            state=receipt.state,
            pending=receipt.state in SECRET_PENDING,
            selectable=_selectable(configuration, receipt, target),
        )

    return _read(caller, service, read, missing="That key change is not available.")


def _finish_request(target):
    """The key change Finish switching for ``target`` is about, or None.

    The latest one, as the settings page's line (``summary``) reads it: an
    earlier one is history, which the page no longer offers.
    """
    from .accounts.secret_models import SecretReplacementRequest

    return (
        SecretReplacementRequest.objects.filter(target=target)
        .order_by("-created_at", "-pk")
        .values_list("pk", flat=True)
        .first()
    )


def preview_finish(caller, service, target):
    """``integration key finish-switching-preview``: the page's review.

    Admits as the page's GET does, passes its privileged check with the live
    full-scope session, then in one read snapshot checks that the settings
    page offers Finish switching (its line is ``unselected``; otherwise
    ``not_available``), runs the page's ``_selection`` (a key no longer
    current is ``stale_version``; a changed loaded ParishSoft organization
    is ``invalid`` at ``organization_id`` with the page's words), validates
    the candidate and signs the page's own preview. Rechecks access
    afterwards. Changes nothing and records no event.
    """
    from uuid import uuid4

    from django.db import transaction

    from .accounts.admin_editing import editable_configuration, sign_preview
    from .accounts.integration_forms import LABELS
    from .accounts.integration_selection import OrganizationLocked
    from .accounts.integration_selection_views import SALT, _changes, _selection
    from .accounts.integration_views import key_summary
    from .accounts.policy import Capability
    from .accounts.request_patch import build_candidate, credential_request_schema
    from .accounts.sessions import sign_in_instant
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit_change(caller, service)

    def step():
        """Pass the privileged check, then review in one snapshot."""
        # As ``integration preview``: the session's FOR SHARE lock needs a
        # writable transaction, so the gate is passed just before the
        # read-only snapshot. A preview records no notice.
        with transaction.atomic():
            sign_in_instant(caller)
        with read_transaction():
            configuration = editable_configuration(service)
            try:
                line = key_summary(configuration, target)
            except LookupError:
                raise NotAvailable("That integration is not set up.") from None
            if line is None or line.kind != "unselected":
                raise NotAvailable("No new key is waiting to be switched to.")
            try:
                configuration, receipt, _, patch = _selection(service, line.request_id)
            except OrganizationLocked:
                raise _organization_locked() from None
            if patch is None:
                raise NotAvailable("This key is already in use.")
            base = service.store.active()
            if base is None or base.digest != configuration.active_configuration.digest:
                raise StaleRecordError("The credential preview base changed.")
            build_candidate(
                base,
                patch,
                candidate_id=uuid4(),
                request_schema=credential_request_schema(base.document()),
            )
            token = sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT + str(receipt.pk),
            )
            changes = [
                {"field": row["field"], "before": row["before"], "after": row["after"]}
                for row in _changes(configuration, patch)
            ]
        return FinishPreview(
            version=configuration.active_configuration.digest,
            target=target,
            name=str(LABELS[target]),
            request_id=str(receipt.pk),
            changes=changes,
            preview={"token": token},
        )

    try:
        model = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def _intent(target, token):
    """``(request id, signed intent)`` of a Finish switching token for ``target``.

    The page's salt names the key change, so the token is read with the salt
    of ``target``'s latest one. Altered, another page's or an earlier key
    change's token is ``invalid`` (``confirm_intent`` checks expiry later),
    so a repeat of an applied token after a newer key change started is
    ``invalid`` too, not the original request; no key change at all is
    ``not_available``.
    """
    from django.core import signing

    from .accounts.integration_selection_views import SALT

    request_id = _finish_request(target)
    if request_id is None:
        raise NotAvailable("No new key is waiting to be switched to.")
    try:
        intent = signing.loads(token, salt=SALT + str(request_id))
        [operation] = intent["patch"]
        if not isinstance(operation["values"], dict):
            raise TypeError("The reviewed change has no values.")
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    return request_id, intent


def finish_prompt_lines(service, target, token):
    """The confirmation prompt's summary, from the token's reviewed patch.

    Names the integration and the settings the switch also changes against
    the applied ones, never a value or a fingerprint. A token that is not
    one for ``target``'s latest key change is ``invalid`` before the prompt.
    """
    from .accounts.integration_forms import LABELS
    from .admin_integration_changes import applied_settings

    def read():
        """The token's intent, against the latest key change, in a snapshot."""
        from .campaigns.work_locks import read_transaction

        with read_transaction():
            return _intent(target, token)

    _, intent = _held(read)
    applied = applied_settings(service, target)
    after = intent["patch"][0]["values"].get("settings", applied)
    changed = sorted(
        name
        for name in set(applied) | set(after)
        if applied.get(name) != after.get(name)
    )
    label = LABELS[target]
    lines = [f"This switches {label} to its new key."]
    if changed:
        lines.append(f"It also changes the {label} settings: {', '.join(changed)}.")
    return lines


def confirm_finish(caller, service, target, *, token, context):
    """``integration key finish-switching``: the page's confirm button.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for ``target``'s latest key change (expired or out of
    date is ``stale_version``, altered or another one's ``invalid``, another
    Administrator's ``denied``), passes the privileged check again under the
    work lock (``privileged``), repeats the page's ``_selection`` as the current scope
    with the page's frozen-base request schema, and records the request with
    ``admin_cmd_integration_key_finish_switching`` and the ``policy_change``
    notice in its own durable transaction. A changed loaded ParishSoft
    organization found under the lock is ``invalid`` at ``organization_id``
    (the page refuses it in its dialog). ``context["committed"]`` and
    ``context["request_id"]`` follow ``parish confirm``'s rules, so a later
    error is ``outcome_unknown`` naming the request.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.automation_sessions import notify, observe
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.integration_selection import OrganizationLocked
    from .accounts.integration_selection_views import SALT, _selection
    from .accounts.request_admission import intake_base
    from .accounts.request_patch import credential_request_schema
    from .admin_integration_actions import _command_event
    from .audit.schemas import Action
    from .campaigns.work_locks import read_transaction
    from .jobs.operational_content import IncidentKind
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)

    def schema():
        """The token's request id and the frozen signed base's parser.

        As the page's ``_preview_schema``: the parser is the reviewed base's,
        even after a later schedule edit. Reading it grants no authority.
        """
        with read_transaction():
            editable_configuration(service)
            request_id, intent = _intent(target, token)
            _, base = intake_base(intent["base"])
            return request_id, intent, credential_request_schema(base.document())

    request_id, intent, request_schema = _held(schema)
    context["request_id"] = str(
        policy_operation_id(actor.identity, UUID(intent["key"]))
    )
    created = []

    def attach(request, extra):
        """Record the command's event and the policy notice with the request."""
        _command_event(Action.ADMIN_CMD_INTEGRATION_KEY_FINISH_SWITCHING, caller, actor)
        # Selecting a new integration key tells every Administrator
        # (admin-automation specification, "Notifications").
        notify(
            "policy_change",
            caller.automation_session,
            command_type=caller.command_type,
            actor_id=actor.identity,
        )
        observe(IncidentKind.AUTOMATION_POLICY_CHANGE)
        created.append(request.pk)

    def scope(service):
        """The page's own scope: the receipt is still current (under the lock)."""
        configuration, _, _, _ = _selection(service, request_id)
        return configuration, None

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=SALT + str(request_id),
            link=None,
            current_scope=scope,
            request_schema=request_schema,
            attach=attach,
            # The page's privileged check again, under the work lock.
            privileged=True,
        )
    except signing.BadSignature:
        raise ValueError("The preview token is not valid.") from None
    except OrganizationLocked:
        raise _organization_locked() from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return FinishConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )


# ------------------------------------------------------------ key replacement


@dataclass(frozen=True)
class ReplaceReview:
    """What ``integration key replace`` asks about before it stages the key.

    ``setup`` is true when the key sets the integration up (Slack not set up
    yet); ``changes`` are the public settings saved with the key (``field``,
    ``before``, ``after``), as ``integration preview`` lists them.
    """

    version: str
    target: str
    name: str
    setup: bool
    changes: list


@dataclass(frozen=True)
class KeyReplacement(ReadModel):
    """``integration key replace``: the staged key change and its selection.

    ``key_request_id`` is the key change ``integration key status`` follows
    (staged, checked and installed by the target's installer); ``request``
    the configuration request that switches to the key once it is installed,
    as ``config request show`` reads it. No key or fingerprint is included.
    """

    version: str
    target: str
    name: str
    setup: bool
    changes: list
    key_request_id: str
    request: dict


def _key_problem(message, code="invalid"):
    """One problem at the key, in fixed words that never quote the key."""
    from .admin_changes import _problem

    return _problem("key", {"code": code, "message": message}, "key")


def _credential_problems(credential):
    """The key form's problems, at ``key`` and never quoting the key.

    The candidate's own errors are the page's words. Blank keeps the current
    key on the page; here it is a mistake. Any other error (the signed
    intent, which the command makes itself, so it is not expected) is still
    a refusal at ``key``, never a missing ``cleaned_data`` entry.
    """
    from .admin_changes import _problem

    if credential.is_valid():
        if not credential.cleaned_data["candidate"]:
            return [_key_problem("Give the new key; it is empty.", "required")]
        return []
    errors = credential.errors.get_json_data().get("candidate", [])
    return [_problem("key", item, "key") for item in errors] or [
        _key_problem("The key could not be checked.")
    ]


def _bind(service, actor, configuration, target, fields, expected_version, key):
    """The page's own forms, bound to the settings and the new key.

    Returns ``(setup, form, credential, changes)`` for a valid save, as the
    page's Save with a key would accept it: a version other than
    ``expected_version`` is ``stale_version``; a key change still being
    checked or installed is ``not_available`` (the page hides its key field
    then); installer discovery not published is ``unavailable``; every
    problem of the settings form (``settings.<name>``) and of the key field
    (``key``, in the page's words, never the key) is ``invalid``. ``key`` is
    the decoded text, which the page's ``InlineCredentialForm`` normalizes.
    """
    from parishkit.config import ConfigError

    from .accounts.integration_forms import InlineCredentialForm
    from .accounts.integration_views import (
        IntegrationUnavailable,
        _form,
        _optional,
        key_summary,
        sign_key_intent,
    )
    from .accounts.setup_credentials import admit_candidate
    from .admin_changes import InvalidChange
    from .admin_integration_changes import field_errors, form_data, setting_names
    from .admin_reads import Unavailable
    from .deployment import recorded_mail_redirect, recorded_profile
    from .storage import StaleRecordError

    if configuration.active_configuration.digest != expected_version:
        raise StaleRecordError("The configuration changed since then.")
    try:
        record = _optional(configuration, target)
        line = key_summary(configuration, target)
    except LookupError:
        raise NotAvailable("That integration is not set up.") from None
    if line is not None and line.kind == "pending":
        raise NotAvailable("A key change for this integration is still running.")
    try:
        intent = sign_key_intent(configuration, actor.identity, target)
    except IntegrationUnavailable:
        raise Unavailable("The integration's installer is not ready.") from None
    stored = {} if record is None else record["values"]["settings"]
    names = setting_names(target)
    form = _form(
        target, form_data(stored, names, fields, expected_version), stored=record
    )
    credential = InlineCredentialForm(
        target, {"intent": intent, "candidate": key}, required=record is None
    )
    problems = [] if form.is_valid() else field_errors(form)
    problems += _credential_problems(credential)
    if not problems and target in {"google_workspace", "slack"}:
        # The page's own format check under the deployment profile, done now
        # so a refused key is reported before the prompt (#476, #1399).
        try:
            admit_candidate(
                target,
                credential.cleaned_data["candidate"].encode("utf-8"),
                recorded_profile(),
                redirect=recorded_mail_redirect(),
            )
        except ConfigError:
            problems.append(_key_problem("The key has an invalid format."))
    if problems:
        raise InvalidChange(problems)
    after = form.public_settings()
    changes = [
        {"field": name, "before": stored.get(name), "after": after.get(name)}
        for name in names
        if stored.get(name) != after.get(name)
    ]
    return record is None, form, credential, changes


def review_replacement(caller, service, target, *, expected_version, fields, key):
    """Check a key replacement as the page's Save would, before the prompt.

    Admits as the page's post does (recording activity), checks the live
    full-scope session without recording it (nothing is done yet), and binds
    the page's forms in one read snapshot (``_bind``). Rechecks access
    afterwards. Changes nothing; holds no transaction when it returns, as
    the prompt waits for a person.
    """
    from django.db import transaction

    from .accounts.admin_editing import editable_configuration
    from .accounts.integration_forms import LABELS
    from .accounts.policy import Capability
    from .accounts.sessions import sign_in_instant
    from .campaigns.work_locks import read_transaction

    actor = _admit_change(caller, service)

    def step():
        """Check the session unrecorded, then bind in one snapshot."""
        with transaction.atomic():
            sign_in_instant(caller, record=False)
        with read_transaction():
            configuration = editable_configuration(service)
            setup, _, _, changes = _bind(
                service, actor, configuration, target, fields, expected_version, key
            )
        return ReplaceReview(
            version=expected_version,
            target=target,
            name=str(LABELS[target]),
            setup=setup,
            changes=changes,
        )

    try:
        review = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return review


def replace_prompt_lines(review):
    """The confirmation prompt's summary: what the replacement does.

    Names the integration and the settings saved with the key, never a
    value, the key or its fingerprint.
    """
    label = review.name
    lines = [
        f"This sets up {label} with the key given."
        if review.setup
        else f"This replaces the {label} key with the key given."
    ]
    if review.changes:
        names = ", ".join(change["field"] for change in review.changes)
        lines.append(f"It also changes the {label} settings: {names}.")
    lines.append(
        "The installer checks the new key with the provider first; if the "
        "check fails, nothing changes"
        + ("." if review.setup else " and the current key stays in use.")
    )
    return lines


def replace_key(caller, service, target, *, expected_version, fields, key, context):
    """``integration key replace``: the page's Save with a new key pasted.

    Admits as the page's post does, binds the page's forms again against the
    configuration of this moment (``_bind``), verifies the signed intent as
    the page does (``key_intent``) and stages through the page's own
    ``stage_key``: sealed for the target's installer, staged under the SQL
    intake guard (which records the privileged command's event and notice), then
    the selection request, with ``admin_cmd_integration_key_replace`` and a
    ``policy_change`` notice in its durable transaction. If the selection
    cannot be recorded the staged key is cancelled, as on the page. A key
    change that started meanwhile is ``not_available``.
    ``context["request_id"]`` names the selection request before anything
    is staged and ``context["committed"]`` is set once it is recorded, so a
    later error is ``outcome_unknown`` naming it.
    """
    from uuid import UUID

    from django.core import signing

    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.automation_sessions import notify, observe
    from .accounts.configuration_requests import policy_operation_id, request_status
    from .accounts.integration_credentials import selection_key
    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import key_intent, stage_key
    from .accounts.secret_models import SECRET_PENDING, SecretReplacementRequest
    from .admin_integration_actions import _command_event
    from .audit.schemas import Action
    from .jobs.operational_content import IncidentKind
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    # Read outside any transaction, as the page's view does: staging and the
    # selection request each need their own durable transaction.
    configuration = _held(lambda: editable_configuration(service))
    setup, form, credential, changes = _bind(
        service, actor, configuration, target, fields, expected_version, key
    )
    try:
        intent = key_intent(configuration, actor, target, form, credential)
    except signing.BadSignature:
        raise ValueError("The key's intake identity is not valid.") from None
    identifier = UUID(intent["request"])
    selection = policy_operation_id(actor.identity, selection_key(identifier))
    context["request_id"] = str(selection)
    created = []

    def attach(request):
        """Record the command's event and the policy notice with the request."""
        _command_event(Action.ADMIN_CMD_INTEGRATION_KEY_REPLACE, caller, actor)
        # Selecting a new integration key tells every Administrator
        # (admin-automation specification, "Notifications").
        notify(
            "policy_change",
            caller.automation_session,
            command_type=caller.command_type,
            actor_id=actor.identity,
        )
        observe(IncidentKind.AUTOMATION_POLICY_CHANGE)
        created.append(request.pk)

    try:
        stage_key(
            caller,
            service,
            configuration,
            actor,
            target,
            form=form,
            credential=credential,
            intent=intent,
            attach=attach,
        )
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except ConfigError:
        if SecretReplacementRequest.objects.filter(
            target=target, state__in=SECRET_PENDING
        ).exists():
            # Another key change for this integration started meanwhile.
            raise NotAvailable(
                "A key change for this integration is still running."
            ) from None
        raise
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    finally:
        # The bound forms hold the plain key; drop them now.
        del form, credential
    context["committed"] = True
    receipt = request_status(request_id=selection, actor_id=actor.identity)
    return KeyReplacement(
        version=expected_version,
        target=target,
        name=str(LABELS[target]),
        setup=setup,
        changes=changes,
        key_request_id=str(identifier),
        request=config_request(receipt).to_document(),
    )


def key_text(value):
    """The key the wrapper sent, as the text the page's key field would hold.

    ``value`` is the decoded bytes. More than the page's limit (128 KiB) or
    bytes that are not UTF-8 are ``invalid`` at ``key``, in fixed words; the
    page's ``InlineCredentialForm`` then refuses a NUL and normalizes the
    rest (``_bind``).
    """
    from .accounts.key_files import MAX_FILE_BYTES
    from .admin_changes import InvalidChange

    if len(value) > MAX_FILE_BYTES:
        raise InvalidChange([_key_problem("The key is longer than 128 KiB.")])
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidChange([_key_problem("The key is not UTF-8 text.")]) from None
