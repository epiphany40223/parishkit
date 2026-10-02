"""Bounded fair replay of lost execution hints from authoritative TaskRun rows.

This is the task portion of a scheduler scan. Owning occurrence/outbox producers
materialize their durable work before calling it. The cursor is an optimization,
not authority: restarting at the beginning is always safe. Denied work advances
the cursor so a large held prefix cannot permanently starve later eligible work.
"""

from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection, transaction
from django.db.models import Q

from parishkit.config import ConfigError
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


# How long the scheduler trusts a hint it published for an unchanged row.
# Shorter than the broker's 60 s hint expiry (broker.publish_hint), so a
# hint still waiting deep in a backed-up queue is replaced before it expires.
RECENT_HINT_SECONDS = 45


@dataclass
class RecentHints:
    """Hints this scheduler published lately, so its sweeps skip re-admitting them.

    Admitting a due row takes the handler's scope, which for most tasks is
    the deployment-wide work-order lock, one transaction per row. During a
    large send every sweep used to re-admit and re-publish each queued row
    until a consumer claimed it, holding that lock for most of the send
    (#394, #147). A row published within RECENT_HINT_SECONDS whose version
    has not changed is skipped instead: its hint is still queued or was
    just taken. Any transition (claim, retry, lease expiry, recovery) bumps
    the version, so changed work is admitted again at once; a lost hint is
    replaced after the window. Only published hints count: a hint whose
    publication failed or never happened is not remembered.

    This is in memory only. A restarted scheduler starts empty and admits
    and hints every due row again, which duplicate-safe consumers tolerate.

    ``drained`` names the task types the bulk Family send (#430) works
    through in batches. Its consumers find their due rows themselves, so a
    hint only has to wake them: once ``per_page`` bulk rows of a type are
    admitted in a page, the page's other bulk rows of that type are skipped
    like fresh ones (and, like them, counted as admitted for health).
    """

    seconds: float = RECENT_HINT_SECONDS
    clock: object = monotonic
    # run_id -> (row version, clock time) of each published hint.
    published_at: dict = field(default_factory=dict)
    # run_id -> row version of hints admitted in the current page.
    pending: dict = field(default_factory=dict)
    drained: frozenset = frozenset()
    # One hint per consumer process that drains the type: two mail
    # consumers, one worker; two covers both.
    per_page: int = 2
    # task type -> bulk rows admitted in the current page.
    counts: dict = field(default_factory=dict)

    def fresh(self, row, *, bulk=False):
        """Whether this unchanged row was published within the window.

        A ``bulk`` row (see ``drained``) is also fresh once its type has
        ``per_page`` admitted bulk rows in this page.
        """
        if bulk and self.counts.get(row.task_type, 0) >= self.per_page:
            return True
        entry = self.published_at.get(row.pk)
        return (
            entry is not None
            and entry[0] == row.version
            and self.clock() - entry[1] < self.seconds
        )

    def admitted(self, row, *, bulk=False):
        """Note the version of a row whose hint this page will publish."""
        self.pending[row.pk] = row.version
        if bulk:
            self.counts[row.task_type] = self.counts.get(row.task_type, 0) + 1

    def published(self, run_id):
        """Remember a hint the transport accepted."""
        version = self.pending.pop(run_id, None)
        if version is not None:
            self.published_at[run_id] = (version, self.clock())

    def start_page(self):
        """Forget unpublished admissions and hints older than the window."""
        now = self.clock()
        self.pending.clear()
        self.counts.clear()
        self.published_at = {
            key: entry
            for key, entry in self.published_at.items()
            if now - entry[1] < self.seconds
        }


def _bulk_rows(page, drained):
    """The page's rows that the bulk Family send drains (#430), if it is on.

    Preparation tasks all are; delivery tasks only for scheduled Family mail
    (receipts, digests, tests and alerts keep one hint per row).
    """
    if not drained:
        return set()
    from .outbox_models import OutboxMessage

    rows = {row.pk for row in page if row.task_type in drained}
    delivery = {
        row.domain_request_id: row.pk
        for row in page
        if row.pk in rows and row.task_type == "outbox_delivery"
    }
    family = set(
        OutboxMessage.objects.filter(
            pk__in=list(delivery), purpose__in=("initial", "reminder")
        ).values_list("pk", flat=True)
    )
    return {pk for pk in rows if pk not in delivery.values()} | {
        delivery[message] for message in family
    }


def due(row, now):
    """Unknown provider state is recoverable work, never automatic redispatch."""
    return (
        row.state in {"queued", "retry_wait"}
        and row.not_before <= now
        or row.state == "abandoned"
        or row.state == "running"
        and row.lease_expires_at <= now
    )


def collect_hints(*, handlers, cursor=None, limit=100, health=None, recent=None):
    """Select one fair page with fresh domain admission, without publishing I/O.

    Publishers run after every transaction here has committed. A hint may become
    stale immediately after selection; the dispatcher always reclaims/rechecks
    PostgreSQL. Broker success/failure never changes durable TaskRun state.
    With ``recent`` (a RecentHints), rows hinted lately and unchanged since
    are skipped without admission; the caller reports each publication back.
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
    if recent is not None:
        recent.start_page()
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
        bulk = _bulk_rows(page, recent.drained) if recent is not None else set()
    hints = []
    for candidate in page:
        if recent is not None and recent.fresh(candidate, bulk=candidate.pk in bulk):
            # Hinted lately and unchanged, so not re-admitted under the lock.
            # The due-work health proof still counts it as admitted, so an
            # overdue row is still reported late (architecture spec, change
            # 4). Its lateness uses this page's clock and row.
            if health is not None:
                health.admitted(candidate, now)
            continue
        handler = handlers[candidate.task_type]
        try:
            with (
                handler.scope(),
                _locked(candidate.correlation_id, root_id=candidate.root_id),
            ):
                row = TaskRun.objects.select_for_update().get(pk=candidate.pk)
                instant = database_now()
                if not due(row, instant):
                    continue
                action = (
                    "recovery_hint" if row.state in {"running", "abandoned"} else "hint"
                )
                if handler.admit(action, _status(row)) is True:
                    if health is not None:
                        health.admitted(row, instant)
                    hints.append(ExecutionHint(row.pk, handler.queue))
                    if recent is not None:
                        recent.admitted(row, bulk=row.pk in bulk)
                elif health is not None:
                    health.unknown()
        except (PermissionError, ConfigError):
            # A raised gate denial and an explicit False are equally held work;
            # neither may pin the cursor forever on an ineligible prefix.
            # Selected-YAML recovery is another scope-local admission hold;
            # operational collection/recovery may remain independently eligible.
            if health is not None:
                health.unknown()
            continue
        except (StaleRecordError, StorageInvariantError, ObjectDoesNotExist) as error:
            # One broken durable scope must not discard earlier hints or strand
            # later independent work. Its own transaction has rolled back; retain
            # a closed diagnostic while advancing the same fair page cursor.
            # Database/transport failures still abort the scan as service outages.
            emit_failure(error)
            if health is not None:
                health.unknown()
    position = (
        ScanCursor(page[-1].not_before, page[-1].pk) if len(page) == limit else None
    )
    return tuple(hints), position
