"""Operations commands of the Admin automation command line (ADM-11 PR 9).

``task retry`` retries a failed background task as its Background work page's
**Retry** button does, through the same functions
(``jobs.task_retries``): a Family mail preparation, daily or weekly digest
work, or an export file cleanup. The task's stored type selects which of the
page's four retries runs; a task the page offers no retry for is
``not_available``.

The command admits as the page's form post does (recording activity, so it
needs a full-scope session) and runs the retry inside the page's command
scope: the work transaction, a lock on the command session's row, and the
Administrator rechecked before and after the effect. In that same
transaction it records one ``admin_cmd_task_retry`` event whose subject is
the automation session, only when it created the retry. The request key is
the page's ``command_id`` (``request_key`` for an export cleanup): repeating
it returns the original retry and records nothing new.

The delivery commands (PR 9b) read and resolve Outgoing mail as its pages
do, through ``jobs.delivery_reads`` and ``delivery_resolution``:
``delivery list``, ``delivery show``, ``delivery refusals`` and ``delivery
refusal-show`` admit passively (any session), read in the page's
transaction, recheck and record the page's ``delivery_viewed`` event.
Their documents never name a recipient: no address, Family DUID or Family
id, and no evidence note text. ``delivery resolve`` is the delivery page's
resolution form in the page's command scope, keyed by the page's
``command_id``, recording ``admin_cmd_delivery_resolve`` when it creates
the resolution. The duplicate-risk ``delivery resend`` and the verified
``delivery refusal-clear`` (PR 9c) need the page's ticked acknowledgement,
which the command line asks for at its prompt (PR 5b) before they act.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import NotAvailable, ReadModel, _held, _recheck


@dataclass(frozen=True)
class TaskRetry(ReadModel):
    """The retry a ``task retry`` created, or found for its request key.

    ``created`` is false when the key had already retried this task (a
    repeat), which returns the original retry unchanged. ``task`` is the
    retry run: follow it with ``task show``.
    """

    created: bool
    request_key: UUID
    task: dict


def task_retry_model(status, *, created, request_key):
    """The command's projection of a retry's ``TaskStatus``."""
    return TaskRetry(
        created=created,
        request_key=request_key,
        task={
            "id": status.run_id,
            "root_id": status.root_id,
            "parent_id": status.parent_id,
            "retry_sequence": status.retry_sequence,
            "type": status.task_type,
            "state": status.state,
        },
    )


# The constraint refusals the pages answer with 409 (stale): a check
# (23514) or a uniqueness conflict (23505) a concurrent change caused.
STALE_STATES = frozenset({"23514", "23505"})


def _raise_stale_on_conflict(error):
    """Report a check or uniqueness refusal as the page does: ``stale_version``.

    The delivery and retry pages answer these with 409 (read again); other
    database errors keep their own classification.
    """
    from django.db import IntegrityError

    from .storage import StaleRecordError

    if isinstance(error, IntegrityError) and (
        getattr(error.__cause__, "sqlstate", None) in STALE_STATES
    ):
        raise StaleRecordError("The record changed; read it again.") from None


def _admit(caller, service):
    """The page's admission of a retry, recording activity as its post does.

    A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's refusal.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin
    from .jobs.task_retries import background_principal

    try:
        return background_principal(caller, service.store, activity=True)
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _ended_or_raise(caller, service, actor, error):
    """Re-raise a refusal, as exit 5 when the session ended meanwhile."""
    from .accounts.policy import Capability

    _recheck(caller, service.store, actor, Capability.BACKGROUND_WORK)
    raise error


def retry_task(caller, service, task_id, *, request_key, context):
    """``task retry``: the Background work page's **Retry** for a failed task.

    An unknown task, or one whose type the page offers no retry for, is
    ``not_available``. A task that is no longer the latest failed run of its
    chain is ``stale_version`` (the page's 409). Keys are bound within a
    task's retry chain: a key another Administrator used for this run is
    ``invalid``; a key used for another run of the chain, whoever used it,
    is ``stale_version`` (``invalid`` for an export cleanup); a key used only
    in another task's chain is a new key here. A preparation that may no longer
    be retried is ``denied``. A configuration refusal (an activating
    change) is ``unavailable``.

    ``context["request_id"]`` is the request key, so an exit-6 document
    names it. ``context["committed"]`` is set once the retry may have
    committed: a database error counts only once the retry and its event
    were written, since it may then have struck the commit; before that the
    transaction rolled back and nothing changed (exit 3).
    """
    from django.core.exceptions import ObjectDoesNotExist

    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.models import TaskRun
    from .jobs.storage import TaskRetryConflict
    from .jobs.task_retries import (
        EXPORT_CLEANUP,
        command_scope,
        retry_export_cleanup_task,
        retry_kind,
        retry_preparation_task,
    )
    from .observability import _guard_refusal
    from .storage import StaleRecordError

    actor = _admit(caller, service)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """Retry inside the page's command scope, with the command's event."""
        # A step repeated after an activating change starts over: nothing
        # from an earlier attempt, which rolled back, counts as written.
        written.clear()
        with command_scope(caller, service, actor):
            task = TaskRun.objects.filter(pk=task_id).values("root_id", "task_type")
            task = task.first()
            kind = None if task is None else retry_kind(task["task_type"])
            if kind is None:
                raise NotAvailable("This task has no retry.")
            # A key that already retried this chain is a repeat: the services
            # return its original retry, and no second event is recorded. The
            # work-order lock the scope holds serializes concurrent repeats.
            repeat = TaskRun.objects.filter(
                root_id=task["root_id"], retry_command_id=request_key
            ).exists()
            try:
                if kind == EXPORT_CLEANUP:
                    status = retry_export_cleanup_task(
                        service.store, actor, task_id, request_key=request_key
                    )
                else:
                    status = retry_preparation_task(
                        service.store,
                        actor,
                        task_id,
                        command_id=request_key,
                        kind=kind,
                    )
            except TaskRetryConflict:
                # Not the chain's latest failed run: the page's 409.
                raise StaleRecordError("The task is no longer retryable.") from None
            except ObjectDoesNotExist:
                # A record the retry needs is gone; the transaction rolls back.
                raise NotAvailable("This task has no retry.") from None
            if not repeat:
                record_action(
                    Action.ADMIN_CMD_TASK_RETRY,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
            return task_retry_model(status, created=not repeat, request_key=request_key)

    try:
        model = _held(step)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        _raise_stale_on_conflict(error)
        raise
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return model


# ---------------------------------------------------------------- deliveries

# The resolutions ``delivery resolve`` takes: every one the page offers but
# the duplicate-risk resend, which is ``delivery resend`` because it asks for
# the page's acknowledgement at the prompt (PR 9c).
RESOLVE_ACTIONS = ("note", "accept", "confirm_unsent", "retry_failed", "retry_unsent")
# The resolutions that prepare the email again before it is sent.
PREPARING_ACTIONS = frozenset({"resend", "retry_failed", "retry_unsent"})
# Mail a retry prepares with the web's Family keys (a Family invitation or
# reminder), which this process loads only then (``admin_cli.load_keyrings`` with
# ``FAMILY_KEYRINGS``).
# Every other purpose prepares without keys: receipts and reports, and a
# chosen-Family test, which preparation refuses before any key is used. The
# page's ``jobs.delivery_views._retry_inputs`` makes the same split (it names
# the keyless purposes instead); change both together.
FAMILY_KEYED_PURPOSES = frozenset({"initial", "reminder"})
# The page's evidence note limit (DeliveryResolution.evidence_note).
NOTE_LIMIT = 2000


@dataclass(frozen=True)
class DeliveryList(ReadModel):
    """One page of Outgoing mail: delivery metadata, never a recipient."""

    state: str
    send: str | None
    page: int
    size: int
    sort: str
    has_next: bool
    matching: int
    matching_capped: bool
    deliveries: list


@dataclass(frozen=True)
class DeliveryShow(ReadModel):
    """One delivery: its metadata, history, latest task, notes and actions.

    ``version`` (in ``delivery``) is what ``delivery resolve`` takes as
    ``--expected-version``. Notes give when and which action only; the
    evidence text stays on the page.
    """

    delivery: dict
    task: dict | None
    actions: list
    retry_unavailable: bool
    page: int
    size: int
    has_next: bool
    events: list
    notes: list


@dataclass(frozen=True)
class RefusalList(ReadModel):
    """One page of unresolved refused addresses, by id and time only."""

    page: int
    size: int
    sort: str
    has_next: bool
    matching: int
    matching_capped: bool
    refusals: list


@dataclass(frozen=True)
class RefusalShow(ReadModel):
    """One refusal, how it was resolved, and the source version to verify."""

    id: UUID
    created_at: object
    resolved: dict | None
    source: dict | None
    can_clear: bool


@dataclass(frozen=True)
class DeliveryResolve(ReadModel):
    """The resolution a ``delivery resolve`` recorded, or found for its key."""

    created: bool
    request_key: UUID
    resolution: dict


def delivery_row(row):
    """A message's metadata, without its recipient or semantic key."""
    return {
        name: row[name]
        for name in (
            "id",
            "campaign_id",
            "purpose",
            "mode",
            "state",
            "version",
            "attempt",
            "task_id",
            "created_at",
            "updated_at",
            "finished_at",
        )
    }


def delivery_list_model(data):
    """The command's projection of ``delivery_reads.read_listing``."""
    window, values = data["window"], data["values"]
    count, capped = data["total"]
    return DeliveryList(
        state=values["state"],
        send=values.get("send"),
        page=window.page,
        size=window.size,
        sort=values["sort"],
        has_next=data["has_next"],
        matching=count,
        matching_capped=capped,
        deliveries=[delivery_row(row) for row in data["rows"]],
    )


def delivery_show_model(data):
    """The command's projection of ``delivery_reads.read_detail``."""
    window = data["window"]
    return DeliveryShow(
        delivery=delivery_row(data["delivery"]),
        task=data["task"],
        actions=list(data["actions"]),
        retry_unavailable=data["retry_unavailable"],
        page=window.page,
        size=window.size,
        has_next=bool(data["has_next"]),
        events=[
            {
                "version": event["version"],
                "at": event["created_at"],
                "state": event["state"],
                "action": event["action"],
                "attempt": event["attempt"],
                # The stored closed code; "reason" is no document member name.
                "result": event["reason"],
            }
            for event in data["events"]
        ],
        notes=[
            {"created_at": note["created_at"], "action": note["action"]}
            for note in data["notes"]
        ],
    )


def refusal_list_model(data):
    """The command's projection of ``delivery_reads.read_refusals``."""
    window = data["window"]
    count, capped = data["total"]
    return RefusalList(
        page=window.page,
        size=window.size,
        sort=data["values"]["sort"],
        has_next=data["has_next"],
        matching=count,
        matching_capped=capped,
        refusals=[
            {"id": row["id"], "created_at": row["created_at"]} for row in data["rows"]
        ],
    )


def refusal_show_model(data):
    """The command's projection of ``delivery_reads.read_refusal``."""
    refusal, resolved, source = data["refusal"], data["resolved"], data["source"]
    return RefusalShow(
        id=refusal.pk,
        created_at=refusal.created_at,
        resolved=None
        if resolved is None
        else {"at": resolved.created_at, "kind": resolved.reason},
        source=None
        if source is None or source.snapshot_id is None
        else {"snapshot_id": source.snapshot_id, "generation": source.generation},
        can_clear=bool(data["can_clear"]),
    )


def _delivery_read(caller, service, read, project, *, subject=None, audit=True):
    """Read as an Outgoing mail page does (``delivery_views._page``).

    Admits passively with ``BACKGROUND_WORK``, reads in one transaction
    unless a restore is under review, rechecks the session and the restore
    in another, and records ``delivery_viewed`` with the page's count and
    subject. A missing message or refusal is ``not_available``, reported
    only after the recheck.
    """
    from django.core.exceptions import ObjectDoesNotExist
    from django.db import transaction

    from .accounts.policy import Capability
    from .admin_reads import Unavailable, _admit, _audit, _restore_review
    from .audit.schemas import Action

    def step():
        """Admit, read, recheck and audit, as the page."""
        actor = _admit(caller, service.store, Capability.BACKGROUND_WORK)
        missing = False
        with transaction.atomic():
            if _restore_review():
                raise Unavailable("A restore is under review.")
            try:
                model, count = project(read())
            except ObjectDoesNotExist:
                missing, count = True, 0
        with transaction.atomic():
            current = _recheck(caller, service.store, actor, Capability.BACKGROUND_WORK)
            if _restore_review():
                raise Unavailable("A restore is under review.")
            if missing:
                raise NotAvailable("No such record.")
            if audit:
                _audit(Action.DELIVERY_VIEWED, current, subject_id=subject, count=count)
        return model

    return _held(step)


def read_deliveries(caller, service, parameters):
    """``delivery list``: one filtered page of Outgoing mail."""
    from .jobs.delivery_reads import read_listing

    def project(data):
        """The document, and the page's audited row count."""
        return delivery_list_model(data), len(data["rows"])

    return _delivery_read(caller, service, lambda: read_listing(parameters), project)


def read_delivery(caller, service, message_id, parameters):
    """``delivery show``: one delivery with a page of its history and notes."""
    from .jobs.delivery_reads import read_detail

    def project(data):
        """The document, and the page's audited count (events and notes)."""
        return delivery_show_model(data), len(data["events"]) + len(data["notes"])

    return _delivery_read(
        caller,
        service,
        lambda: read_detail(message_id, parameters),
        project,
        subject=message_id,
    )


def read_refusal_list(caller, service, parameters):
    """``delivery refusals``: one page of unresolved refused addresses."""
    from .jobs.delivery_reads import read_refusals

    def project(data):
        """The document, and the page's audited row count."""
        return refusal_list_model(data), len(data["rows"])

    return _delivery_read(caller, service, lambda: read_refusals(parameters), project)


def read_refusal_detail(caller, service, refusal_id):
    """``delivery refusal-show``: one refusal and the source version to verify."""
    from django.http import QueryDict

    from .jobs.delivery_reads import read_refusal

    return _delivery_read(
        caller,
        service,
        lambda: read_refusal(refusal_id, QueryDict()),
        lambda data: (refusal_show_model(data), 1),
        subject=refusal_id,
    )


def delivery_resolve_model(receipt, *, created, request_key):
    """The command's projection of a ``DeliveryResolution`` receipt."""
    return DeliveryResolve(
        created=created,
        request_key=request_key,
        resolution={
            "id": receipt.pk,
            "message_id": receipt.message_id,
            "action": receipt.action,
            "expected_version": receipt.expected_version,
            "previous_task_id": receipt.previous_task_id,
            "retry_task_id": receipt.retry_task_id,
            "created_at": receipt.created_at,
        },
    )


def _family_keyed(message_id, request_key):
    """Whether resolving ``message_id`` would prepare a Family email.

    False for a repeat of a bound key (``resolve_delivery`` returns its
    receipt before any preparation, so no key is needed) and for an unknown
    delivery (refused later as ``not_available``).
    """
    from .jobs.delivery_resolution_models import DeliveryResolution
    from .jobs.outbox_models import OutboxMessage

    if DeliveryResolution.objects.filter(pk=request_key).exists():
        return False
    purpose = (
        OutboxMessage.objects.filter(pk=message_id)
        .values_list("purpose", flat=True)
        .first()
    )
    return purpose in FAMILY_KEYED_PURPOSES


def _keyed_command(caller, service, actor, context, *, event, exists, act):
    """Run one keyed page command in the page's command scope (PR 9b, 9c).

    ``exists()`` says whether the request key is already bound, read in the
    command's own transaction; ``act(created=...)`` runs the page's function
    and returns the document. ``event`` (``admin_cmd_<area>_<verb>``) is
    recorded in the same transaction only when the key was new, so a repeat
    records nothing. A missing record raised by ``act`` as ``NotAvailable``,
    or a refusal, is exit 5 when the session ended meanwhile; a check or
    uniqueness refusal from the database is ``stale_version``, as the pages
    answer 409.
    """
    from .audit.schemas import ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal

    written = []

    def step():
        """The page's command, then the command's event when it created one."""
        written.clear()
        with command_scope(caller, service, actor):
            repeat = exists()
            model = act(created=not repeat)
            if not repeat:
                record_action(
                    event,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
            return model

    try:
        model = _held(step)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        _raise_stale_on_conflict(error)
        raise
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return model


def _resolve(
    caller,
    service,
    message_id,
    *,
    action,
    expected_version,
    note,
    request_key,
    context,
    event,
):
    """``resolve_delivery`` as the delivery page's form posts it.

    ``resend`` carries the page's ticked duplicate-risk acknowledgement,
    which ``delivery resend`` asked for at the prompt before calling this;
    every other action carries none, as on the page.

    A retry or resend re-prepares the email as the page does. A Family email
    seals the Family's current code and link with the web's general and
    public token keyrings (reading them, never writing or rotating a
    credential), so ``service.keyrings(FAMILY_KEYRINGS)`` loads them first,
    outside every lock, and only when a Family email will be prepared; a
    keyring that differs from the running web's is ``credential_mismatch``.
    The message's purpose never changes, so reading it before the command's
    transaction decides that safely. A repeat with the same key returns
    before any preparation.
    """
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.cryptography import CryptographicError
    from .admin_reads import Unavailable
    from .jobs.delivery_resolution import resolve_delivery
    from .jobs.delivery_resolution_models import DeliveryResolution
    from .jobs.storage import TaskRetryConflict
    from .storage import StaleRecordError

    actor = _admit(caller, service)
    context["request_id"] = str(request_key)
    general = public = None
    if action in PREPARING_ACTIONS and _family_keyed(message_id, request_key):
        from .admin_cli import FAMILY_KEYRINGS

        if service.keyrings is None:
            raise NotAvailable("This process cannot load the Family keys.")
        general, public = service.keyrings(FAMILY_KEYRINGS)

    def inputs(purpose):
        """What a retry prepares with, as the page's ``_retry_inputs``."""
        keyed = purpose in FAMILY_KEYED_PURPOSES
        if keyed and general is None:
            # Unreachable: the purpose was read above and cannot change.
            raise NotAvailable("The Family keys were not loaded.")
        return dict(
            general=general if keyed else None,
            public=public if keyed else None,
            public_origin=service.public_origin,
        )

    def act(*, created):
        """The page's ``resolve_delivery``, as the page's form posts it."""
        try:
            receipt = resolve_delivery(
                service.store,
                actor.identity,
                message_id=message_id,
                command_id=request_key,
                expected_version=expected_version,
                action=action,
                note=note,
                duplicate_acknowledged=action == "resend",
                preparation_inputs=inputs,
            )
        except TaskRetryConflict:
            raise StaleRecordError("The delivery's task changed.") from None
        except ObjectDoesNotExist:
            raise NotAvailable("No such delivery.") from None
        except CryptographicError:
            if general is None:
                raise
            # Sealing with the Family keys refused: a key rotation holds the
            # credential key lock, or the database's accepted key inventory
            # moved past the web's rings. The transaction rolled back, so
            # nothing changed; retry once the rotation settles.
            raise Unavailable("The Family keys are changing; retry.") from None
        return delivery_resolve_model(receipt, created=created, request_key=request_key)

    return _keyed_command(
        caller,
        service,
        actor,
        context,
        event=event,
        exists=lambda: DeliveryResolution.objects.filter(pk=request_key).exists(),
        act=act,
    )


def resolve_delivery_command(
    caller, service, message_id, *, action, expected_version, note, request_key, context
):
    """``delivery resolve``: the delivery page's resolution form.

    Admits as the page's form post does (``BACKGROUND_WORK``, recording
    activity) and runs ``resolve_delivery`` inside the page's command scope,
    where it records ``admin_cmd_delivery_resolve`` only when it creates the
    resolution. The key is the page's ``command_id``: a repeat with the same
    intent returns the original receipt; with another intent it is
    ``invalid``. A delivery that changed since ``--expected-version``, or an
    action its state does not allow, is ``stale_version``; an unknown
    delivery is ``not_available``. Retries load the Family keys as
    ``_resolve`` describes. ``resend`` is ``delivery resend``.
    """
    from .audit.schemas import Action

    if action not in RESOLVE_ACTIONS:
        raise ValueError("This resolution is not offered here.")
    return _resolve(
        caller,
        service,
        message_id,
        action=action,
        expected_version=expected_version,
        note=note,
        request_key=request_key,
        context=context,
        event=Action.ADMIN_CMD_DELIVERY_RESOLVE,
    )


def resend_delivery_command(
    caller, service, message_id, *, expected_version, note, request_key, context
):
    """``delivery resend``: the page's duplicate-risk **Resend** (PR 9c).

    The command line asked for the page's acknowledgement first (the prompt,
    or ``--yes``); this passes it as the ticked box. Otherwise it is
    ``delivery resolve`` with the action ``resend``: only a delivery whose
    outcome is unknown, keyed by the page's ``command_id`` (a repeat returns
    the receipt and prepares nothing; once resent, another key for that
    version is ``stale_version``), recording ``admin_cmd_delivery_resend``
    when it creates the resolution.
    """
    from .audit.schemas import Action

    return _resolve(
        caller,
        service,
        message_id,
        action="resend",
        expected_version=expected_version,
        note=note,
        request_key=request_key,
        context=context,
        event=Action.ADMIN_CMD_DELIVERY_RESEND,
    )


@dataclass(frozen=True)
class RefusalClear(ReadModel):
    """The clearance a ``delivery refusal-clear`` recorded, or found for its key."""

    created: bool
    request_key: UUID
    resolution: dict


def refusal_clear_model(receipt, *, created, request_key):
    """The command's projection of a ``RecipientRefusalResolution``.

    Never the evidence note: ids, the verified source version and the time.
    """
    return RefusalClear(
        created=created,
        request_key=request_key,
        resolution={
            "id": receipt.pk,
            "refusal_id": receipt.refusal_id,
            "source_snapshot_id": receipt.source_snapshot_id,
            "source_generation": receipt.source_generation,
            "created_at": receipt.created_at,
        },
    )


def clear_refusal_command(
    caller,
    service,
    refusal_id,
    *,
    source_snapshot_id,
    source_generation,
    note,
    request_key,
    context,
):
    """``delivery refusal-clear``: the Refused address page's verified clearance.

    The command line asked for the page's acknowledgement ("I verified this
    address") first, at the prompt or with ``--yes``; this passes it as the
    ticked box to ``clear_recipient_refusal`` in the page's command scope.
    The key is the page's ``command_id``: a repeat with the same intent
    returns the original clearance, another intent is ``invalid``. A refusal
    already cleared, or a source version that is no longer current, is
    ``stale_version``; an unknown refusal is ``not_available``. Records
    ``admin_cmd_delivery_refusal_clear`` when it creates the clearance.
    """
    from django.core.exceptions import ObjectDoesNotExist

    from .audit.schemas import Action
    from .jobs.delivery_admin import clear_recipient_refusal
    from .jobs.recipient_models import RecipientRefusalResolution

    actor = _admit(caller, service)
    context["request_id"] = str(request_key)

    def act(*, created):
        """The page's ``clear_recipient_refusal``, with the box ticked."""
        try:
            receipt = clear_recipient_refusal(
                service.store,
                actor.identity,
                refusal_id=refusal_id,
                command_id=request_key,
                source_snapshot_id=source_snapshot_id,
                source_generation=source_generation,
                note=note,
                verified=True,
            )
        except ObjectDoesNotExist:
            raise NotAvailable("No such refusal.") from None
        return refusal_clear_model(receipt, created=created, request_key=request_key)

    return _keyed_command(
        caller,
        service,
        actor,
        context,
        event=Action.ADMIN_CMD_DELIVERY_REFUSAL_CLEAR,
        exists=lambda: RecipientRefusalResolution.objects.filter(
            pk=request_key
        ).exists(),
        act=act,
    )
