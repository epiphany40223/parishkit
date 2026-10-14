"""A key change's status and Finish switching (ADM-11 PR 10).

``integration key status`` is the **Key replacement status** page
(``admin:credential_status``) and ``integration key finish-switching-preview``
and ``integration key finish-switching`` the **Finish switching to the new
key** page's review and its confirm button (``admin:select_credential``),
through the pages' own pieces (``secret_request_status`` and ``_selectable``;
``_selection``, ``_changes``, the page's salt and ``confirm_intent``), so the
pages and the command line cannot drift:

- ``integration key status TARGET [--request-id ID]`` admits passively, as
  the page does, and reads one of the session Administrator's own key
  changes (by default the latest for TARGET): only its id, its state,
  whether it is still running and whether the page offers Finish switching.
- ``integration key finish-switching-preview TARGET`` admits as the page
  does (recording activity, so it needs a full-scope session), stands in
  for the page's recent sign-in with the live session, and prints the
  page's review of the switch, with ``preview.token``, the same signed
  binding the page embeds. It changes nothing and records no event.
- ``integration key finish-switching TARGET --token TOKEN`` takes that
  token (or one the page signed) and records the configuration request
  exactly as the page's confirm button does, with the fresh gate under the
  work lock, ``admin_cmd_integration_key_finish_switching`` and, as it
  selects a new integration key, a ``policy_change`` notice.

Neither key material nor a fingerprint is ever printed, logged or recorded
here: the commands handle only request ids, states and the public settings
the page's table shows. Replacing a key (which accepts the secret) is a
separate command.
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
# organization (``credential-selection.html``'s blocked notice).
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

    Admits as the page's GET does, passes its recent sign-in with the live
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
    from .accounts.sessions import require_fresh
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit_change(caller, service)

    def step():
        """Pass the fresh gate, then review in one snapshot."""
        # As ``integration preview``: the session's FOR SHARE lock needs a
        # writable transaction, so the gate is passed just before the
        # read-only snapshot. A preview records no notice.
        with transaction.atomic():
            require_fresh(caller)
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
    change's token is ``invalid`` (``confirm_intent`` checks expiry later);
    no key change at all is ``not_available``.
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
    Administrator's ``denied``), passes the fresh gate again under the work
    lock (``fresh``), repeats the page's ``_selection`` as the current scope
    with the page's frozen-base request schema, and records the request with
    ``admin_cmd_integration_key_finish_switching`` and the ``policy_change``
    notice in its own durable transaction. ``context["committed"]`` and
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
            # The page asks for a recent sign-in again under the work lock.
            fresh=True,
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
