"""Bounded fair replay of lost execution hints from authoritative TaskRun rows.

This is the task portion of a scheduler scan. Owning occurrence/outbox producers
materialize their durable work before calling it. The cursor is an optimization,
not authority: restarting at the beginning is always safe. Denied work advances
the cursor so a large held prefix cannot permanently starve later eligible work.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

# The newest-due scan (#1921) looks at no more than this many rows a loop.
NEWEST_DUE_ROWS = 10
# How far before the previous newest-due scan its window starts. A new row's
# not_before is its transaction's start (the column's database default), so a
# request that waited for a lock commits after a scan that could not see it
# yet; this overlap still catches it on the next loop.
NEWEST_DUE_SLACK = timedelta(seconds=60)

# The farthest back the window may reach beyond the slack. ``since`` stays
# put while newest-due scans fail, so without this a long run of failures
# would widen the window (and the seen-row record) without bound.
NEWEST_DUE_MAX_LOOKBACK = timedelta(minutes=5)


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
    # The newest-due scan (#1921). run_id -> (not_before, version) of each
    # row it has looked at, which later newest-due scans pass over while the
    # row is inside the window and unchanged; a row whose not_before or
    # version differs (it failed and is retrying, say) is looked at again.
    # The fair sweep still reaches every one.
    newest_seen: dict = field(default_factory=dict)
    # run_id -> row version of each row the newest-due scan checked for
    # admission in this loop, which this loop's sweep scans then skip.
    newest_checked: dict = field(default_factory=dict)

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
    hints = _admit_page(
        page, now=now, handlers=handlers, bulk=bulk, health=health, recent=recent
    )
    position = (
        ScanCursor(page[-1].not_before, page[-1].pk) if len(page) == limit else None
    )
    return hints, position


def collect_newest_hints(*, handlers, recent, since=None, limit=NEWEST_DUE_ROWS):
    """Admit the newest few rows that fell due since the last loop (#1921).

    The fair sweep pages due rows oldest first, so a row that fell due just
    now (a requested refresh, load or export) waits until the sweep reaches
    it, behind every due row the scheduler keeps refusing (a paused send's
    messages, say). This scan runs on every loop and looks at no more than
    ``limit`` queued or retry-waiting rows whose ``not_before`` lies after
    ``since`` minus NEWEST_DUE_SLACK, newest first, skipping the rows an
    earlier newest-due scan already looked at and that have not changed
    since. ``since`` is the clock that scan returned; None (the first loop)
    means now. It is never taken to be more than NEWEST_DUE_MAX_LOOKBACK
    before now, however long scans have been failing.

    Each row goes through exactly the sweep's admission (_admit_page): the
    handler's scope and row lock, one transaction per row, and RecentHints.
    It keeps no cursor and records no due-work health; the fair sweep still
    pages every due row in order. Returns the hints and this scan's clock.
    """
    if connection.in_atomic_block:
        raise StorageInvariantError("Scheduler scanning must own its transactions.")
    if not isinstance(recent, RecentHints):
        raise ValueError("The newest-due scan requires the scheduler's hint memory.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Scheduler hint pages must be bounded.")
    handlers = dict(handlers)
    if any(not isinstance(handler, Handler) for handler in handlers.values()):
        raise ValueError("Scheduler requires the internal handler registry.")
    recent.start_page()
    recent.newest_checked.clear()
    with transaction.atomic():
        now = database_now()
        start = (
            now
            if since is None
            else max(min(since, now), now - NEWEST_DUE_MAX_LOOKBACK)
        )
        cutoff = start - NEWEST_DUE_SLACK
        recent.newest_seen = {
            key: seen for key, seen in recent.newest_seen.items() if seen[0] > cutoff
        }
        # Over-read by the number of remembered rows, since any of them may
        # still be unchanged and so be passed over below.
        rows = newest_due_rows(handlers, cutoff=cutoff, now=now)[
            : limit + len(recent.newest_seen)
        ]
        page = [
            row
            for row in rows
            if recent.newest_seen.get(row.pk) != (row.not_before, row.version)
        ][:limit]
        bulk = _bulk_rows(page, recent.drained)
    for row in page:
        recent.newest_seen[row.pk] = (row.not_before, row.version)
    hints = _admit_page(
        page, now=now, handlers=handlers, bulk=bulk, recent=recent, newest=True
    )
    return hints, now


def newest_due_rows(handlers, *, cutoff, now):
    """Queued or retry-waiting rows that fell due after ``cutoff``, newest first.

    A range of the task_due (state, not_before) index, so only the rows that
    fell due inside the window are read, however many older due rows the
    fair sweep has to page through; no other index is needed.
    """
    return TaskRun.objects.filter(
        task_type__in=handlers,
        state__in=("queued", "retry_wait"),
        not_before__gt=cutoff,
        not_before__lte=now,
    ).order_by("-not_before", "-id")


def _admit_page(page, *, now, handlers, bulk, health=None, recent=None, newest=False):
    """Admit each selected row under its handler's locks; return its hints.

    One transaction per row, under the handler's scope (for most tasks the
    global work-order lock) and the row's lock, rechecking that it is due.
    ``newest`` marks the newest-due scan, which notes each row it checks in
    ``recent.newest_checked``; a sweep page skips such a row if it has not
    changed, so no row is checked twice in one loop.
    """
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
        if newest:
            recent.newest_checked[candidate.pk] = candidate.version
        elif (
            recent is not None
            and recent.newest_checked.get(candidate.pk) == candidate.version
        ):
            # This loop's newest-due scan checked the unchanged row already
            # and did not publish it (refused, or its publication failed),
            # which the health proof counts as unknown, as a refusal here
            # would be. Forgetting it lets the next newest-due scan check it
            # again, so a gate that cleared during this loop delays it by one
            # loop, not one sweep.
            recent.newest_seen.pop(candidate.pk, None)
            if health is not None:
                health.unknown()
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
    return tuple(hints)
