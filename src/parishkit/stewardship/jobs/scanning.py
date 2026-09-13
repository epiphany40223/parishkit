"""Bounded fair replay of lost execution hints from authoritative TaskRun rows.

This is the task portion of a scheduler scan. Owning occurrence/outbox producers
materialize their durable work before calling it. The cursor is an optimization,
not authority: restarting at the beginning is always safe. Denied work advances
the cursor so a large held prefix cannot permanently starve later eligible work.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection, transaction
from django.db.models import Q

from parishkit.stewardship.observability import emit_failure
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .dispatch import Handler, WorkQueue
from .models import TaskRun
from .ownership import database_now
from .storage import _locked, _status


@dataclass(frozen=True)
class ScanCursor:
    """Stable keyset position, reset after the bounded traversal reaches its end."""

    not_before: datetime
    run_id: UUID

    def __post_init__(self):
        """Do not accept naive times or coerced identities in pagination state."""
        if (
            not isinstance(self.not_before, datetime)
            or self.not_before.utcoffset() is None
            or not isinstance(self.run_id, UUID)
        ):
            raise ValueError("Scheduler cursor requires an aware instant and UUID.")


@dataclass(frozen=True)
class ExecutionHint:
    """The transport receives only the opaque task identity and service queue."""

    run_id: UUID
    queue: WorkQueue


def due(row, now):
    """Unknown provider state is recoverable work, never automatic redispatch."""
    return (
        row.state in {"queued", "retry_wait"}
        and row.not_before <= now
        or row.state == "abandoned"
        or row.state == "running"
        and row.lease_expires_at <= now
    )


def collect_hints(*, handlers, cursor=None, limit=100):
    """Select one fair page with fresh domain admission, without publishing I/O.

    Publishers run after every transaction here has committed. A hint may become
    stale immediately after selection; the dispatcher always reclaims/rechecks
    PostgreSQL. Broker success/failure never changes durable TaskRun state.
    """
    if connection.in_atomic_block:
        raise StorageInvariantError("Scheduler scanning must own its transactions.")
    if cursor is not None and not isinstance(cursor, ScanCursor):
        raise ValueError("Scheduler paging requires a canonical cursor.")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Scheduler hint pages must be bounded.")
    handlers = dict(handlers)
    if any(not isinstance(handler, Handler) for handler in handlers.values()):
        raise ValueError("Scheduler requires the internal handler registry.")
    with transaction.atomic():
        now = database_now()
        rows = TaskRun.objects.filter(task_type__in=handlers).filter(
            Q(state__in=("queued", "retry_wait"), not_before__lte=now)
            | Q(state="abandoned")
            | Q(state="running", lease_expires_at__lte=now)
        )
        if cursor is not None:
            rows = rows.filter(
                Q(not_before__gt=cursor.not_before)
                | Q(not_before=cursor.not_before, id__gt=cursor.run_id)
            )
        page = list(rows.order_by("not_before", "id")[:limit])
    hints = []
    for candidate in page:
        handler = handlers[candidate.task_type]
        try:
            with (
                handler.scope(),
                _locked(candidate.correlation_id, root_id=candidate.root_id),
            ):
                row = TaskRun.objects.select_for_update().get(pk=candidate.pk)
                if not due(row, database_now()):
                    continue
                action = (
                    "recovery_hint" if row.state in {"running", "abandoned"} else "hint"
                )
                if handler.admit(action, _status(row)) is True:
                    hints.append(ExecutionHint(row.pk, handler.queue))
        except PermissionError:
            # A raised gate denial and an explicit False are equally held work;
            # neither may pin the cursor forever on an ineligible prefix.
            continue
        except (StaleRecordError, StorageInvariantError, ObjectDoesNotExist) as error:
            # One broken durable scope must not discard earlier hints or strand
            # later independent work. Its own transaction has rolled back; retain
            # a closed diagnostic while advancing the same fair page cursor.
            # Database/transport failures still abort the scan as service outages.
            emit_failure(error)
    position = (
        ScanCursor(page[-1].not_before, page[-1].pk) if len(page) == limit else None
    )
    return tuple(hints), position
