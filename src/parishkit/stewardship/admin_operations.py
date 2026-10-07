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
    task's retry chain: a key another Administrator used in this chain is
    ``invalid``; one this Administrator used for another run of the chain is
    ``stale_version`` (``invalid`` for an export cleanup); a key used only in
    another task's chain is a new key here. A preparation that may no longer
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
        raise
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return model
