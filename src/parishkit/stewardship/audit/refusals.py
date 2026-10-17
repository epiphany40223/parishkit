"""Record a refused Admin input (#1403; operations spec, Admin input refusals).

``record_refusal`` takes one ``RefusalRecord`` and logs it: always one
``admin_input_refused`` line in the process log, and, for a signed-in
Administrator, one durable operational log entry, so the System logs page
can say what was refused and why. Both hold the same closed context
(``audit.refusal_context``) and never a submitted value.

The durable entry is bounded, because the operational log keeps every row
for good: each actor has a budget of ``CAP`` entries per level (INFO and
WARNING separately) in each fixed ``WINDOW`` of UTC database time. The
window is read from ``statement_timestamp()`` after the lock, the clock the
writer trigger stamps the row with, so the count and the row agree on the
window even when the lock wait crosses its boundary. The ``CAP``-th entry
of a budget carries ``capped: true``; later refusals of that level in that
window have the process-log line only. A per-actor, per-level transaction
advisory lock serialises the count and the insert, so the cap is exact
under concurrent requests. On the durable path the line is written once the
count is known, before the insert, and carries ``capped`` exactly as its
entry would; when a time limit stops the lock or the count, the line is
written at that point, without ``capped``.

The durable write never joins the caller's work: it runs only when the
connection is in no atomic block and in autocommit (a refused view's
transaction has ended, and a rolled-back one cannot take the entry or its
limits with it), otherwise only the line is written. It uses the caller's
own connection, in a new short transaction with ``SET LOCAL`` statement and
lock limits (those of ``audit.timeouts``), and opens no second connection,
so a burst of refusals cannot push the web login past its connection limit.
It never raises, so a failure to record cannot change the refused answer;
when a limit stops it, the process log records a WARNING ``task_timed_out``
(``refusal_log_write``) with the limit and the elapsed seconds, after the
refusal's own line.

The Admin refusal middleware (``web.refusal_log``) calls it for refused
Admin page requests. The command line (``admin_cli``) calls it for a refused
command, passing ``db``, a private short-lived connection of its own opened
after the command's connections are closed (admin-automation spec, Refusal
entry); the same rules apply on it.
"""

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from time import monotonic
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.observability import (
    Event,
    current_correlation,
    emit,
    emit_failure,
)

from .log_contract import enforce
from .refusal_context import WARNING_REASONS, clean, complete
from .schemas import ContextKind

# Durable entries per actor and level in one window.
CAP = 30
# The fixed window, as a PostgreSQL interval; windows start on the UTC hour.
WINDOW = "10 minutes"
# The durable write's limits, those of ``audit.timeouts``: each of the
# count and the insert may take this long, and the advisory lock may wait
# this long.
STATEMENT_SECONDS = 2
LOCK_SECONDS = 1
# The advisory lock namespace: the next free key in the 7362xx sequence
# (736248 is archive.imports.REVIEW_LOCK); the second key is a hash of the
# actor and level.
LOCK_NAMESPACE = 736249

_COUNT = (
    "SELECT count(*) FROM stewardship_operational_log "
    "WHERE event=%s AND actor_id=%s AND level=%s "
    "AND created_at>=date_bin(%s::interval, statement_timestamp(), "
    "TIMESTAMPTZ '2000-01-01 00:00+00')"
)
_INSERT = (
    "INSERT INTO stewardship_operational_log "
    "(id,correlation_id,actor_id,event,level,schema,context) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)"
)


def _class(error):
    """``error``'s class, when ``error`` is an exception or already a class."""
    return error if isinstance(error, type) else type(error)


def exception_class_of(error):
    """An exception type's own class name in lowercase snake form.

    ``ValueError`` is ``value_error`` and ``HTTPError`` is ``http_error``.
    ``error`` is the exception or its class. Never its module or text; a
    name that does not fit the identifier shape afterwards is dropped by
    ``refusal_context.clean``.
    """
    name = _class(error).__name__
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def level_of(reason, error=None):
    """The level the reason decides: WARNING for a denial or a server-state
    reason, and for an unclassified PermissionError (or a subclass of it);
    INFO for everything else."""
    if reason in WARNING_REASONS:
        return "WARNING"
    if (
        reason == "unclassified"
        and error is not None
        and issubclass(_class(error), PermissionError)
    ):
        return "WARNING"
    return "INFO"


@dataclass(frozen=True)
class RefusalRecord:
    """One refused Admin input, as the code that refused it knows it.

    ``actor_id`` is the signed-in user's ``PortalUser`` id, or None for an
    anonymous request (which gets the process-log line only). Exactly one of
    ``route`` (the resolved page name) and ``command`` (the pk-admin catalog
    name) is expected. ``error`` is the refusing exception, or only its
    class, if any: it is never recorded, only used for the level and, for
    an unclassified refusal, its class name. Every other value is checked
    and dropped when it is not on the allow-list (``refusal_context.clean``).
    """

    reason: str
    actor_id: UUID | None = None
    route: str | None = None
    command: str | None = None
    method: str | None = None
    status: int | None = None
    form: str | None = None
    fields: tuple = ()
    codes: tuple = ()
    expected: str | None = None
    actual: str | None = None
    unexpected_count: int | None = None
    mode: str | None = None
    error: BaseException | type | None = None

    def context(self):
        """The cleaned, allow-listed context for the line and the entry."""
        return clean(
            {
                "route": self.route,
                "command": self.command,
                "method": self.method.lower() if type(self.method) is str else None,
                "status": self.status,
                "form": self.form,
                # Passed through as given: clean() keeps only a list or
                # tuple of names, so None or a string is dropped.
                "fields": self.fields,
                "codes": self.codes,
                "reason": self.reason,
                "exception_class": (
                    exception_class_of(self.error) if self.error is not None else None
                ),
                "expected": self.expected,
                "actual": self.actual,
                "unexpected_count": self.unexpected_count,
                "mode": self.mode,
            }
        )

    @property
    def level(self):
        """INFO or WARNING, from the cleaned reason (see ``level_of``)."""
        return level_of(self.context()["reason"], self.error)


def _line(level, context, actor_id):
    """The refusal's process-log line, at the entry's level."""
    emit(
        Event.ADMIN_INPUT_REFUSED,
        level=getattr(logging, level),
        refusal=context,
        actor_id=actor_id,
    )


def _timeout_kind(error):
    """Which of this write's own limits stopped it, or None."""
    # Deferred, as in service_status and audit.log_reads: jobs.broker
    # imports Celery and the jobs package, which imports audit modules, so
    # a module-level import would make a cycle.
    from parishkit.stewardship.jobs.broker import sql_timeout_kind

    kind = sql_timeout_kind(error)
    return kind if kind in {"statement_timeout", "lock_timeout"} else None


@contextmanager
def _short_transaction(db):
    """One new transaction on ``db``, committed on success, else rolled back.

    ``None`` is this thread's own connection, through Django's ``atomic``. A
    private connection (the command line's) is a separate wrapper that
    ``atomic`` cannot name, so its transaction is begun and ended directly;
    its caller closes it afterwards, so autocommit is not turned back on.
    """
    if db is None:
        with transaction.atomic():
            yield
        return
    db.set_autocommit(False)
    try:
        yield
    except BaseException:
        db.rollback()
        raise
    db.commit()


def record_refusal(record, *, db=None, durable=True):
    """Log one refused Admin input; never raise.

    The process-log line comes first whatever happens to the durable entry.
    A durable entry is written only for a signed-in actor, outside any
    atomic block, and only while the actor's budget for that level in the
    current window lasts; see the module docstring for the cap and limits.
    ``db`` is the command line's private connection; None (a page) is this
    thread's own connection. ``durable=False`` (no connection could be made
    for it) writes the line alone, still naming the actor.
    """
    try:
        context = record.context()
        level = record.level
        actor_id = record.actor_id if isinstance(record.actor_id, UUID) else None
    except Exception as error:
        # No context means no line can be written; say so instead.
        emit_failure(error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING)
        return
    target = connection if db is None else db
    try:
        # With autocommit off, Django's outermost atomic() would only be a
        # savepoint in the caller's transaction, so the entry and its
        # SET LOCAL limits would share that transaction's fate.
        # get_autocommit() reconnects a closed connection, within its
        # connect_timeout.
        durable = (
            durable
            and actor_id is not None
            and complete(context)
            and not target.in_atomic_block
            and target.get_autocommit()
        )
    except Exception as error:
        _guarded_line(level, context, actor_id)
        _failed(error, 0)
        return
    if not durable:
        _guarded_line(level, context, actor_id)
        return
    logged = False
    started = monotonic()
    try:
        enforce(Event.ADMIN_INPUT_REFUSED, level, context)
        with _short_transaction(db), target.cursor() as cursor:
            cursor.execute(f"SET LOCAL statement_timeout='{STATEMENT_SECONDS}s'")
            cursor.execute(f"SET LOCAL lock_timeout='{LOCK_SECONDS}s'")
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                [LOCK_NAMESPACE, f"{actor_id}:{level}"],
            )
            cursor.execute(
                _COUNT, [Event.ADMIN_INPUT_REFUSED.value, actor_id, level, WINDOW]
            )
            (count,) = cursor.fetchone()
            # The CAP-th entry is kept and marked; past it only the line is.
            if count >= CAP - 1:
                context = {**context, "capped": True}
            # Marked before the attempt, so a line that fails here is
            # reported once (below) and not tried a second time.
            logged = True
            _line(level, context, actor_id)
            if count < CAP:
                cursor.execute(
                    _INSERT,
                    [
                        uuid4(),
                        current_correlation(),
                        actor_id,
                        Event.ADMIN_INPUT_REFUSED.value,
                        level,
                        ContextKind.REFUSAL.value,
                        json.dumps(context),
                    ],
                )
    except Exception as error:
        elapsed = monotonic() - started
        if not logged:
            _guarded_line(level, context, actor_id)
        _failed(error, elapsed)


def _guarded_line(level, context, actor_id):
    """Write the line, logging a failure instead of raising."""
    try:
        _line(level, context, actor_id)
    except Exception as error:
        emit_failure(error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING)


def _failed(error, elapsed):
    """Log why the durable entry was not written; never raise.

    A stop by this write's own statement or lock limit is a WARNING
    ``task_timed_out`` naming ``refusal_log_write``, the limit and the
    elapsed seconds, in the process log only (a second durable write would
    most likely meet the same limit). Anything else is a WARNING failure
    line on the refusal's own event.
    """
    try:
        kind = _timeout_kind(error)
        if kind is None:
            emit_failure(error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING)
            return
        emit(
            Event.TASK_TIMED_OUT,
            level=logging.WARNING,
            timeout="refusal_log_write",
            limit_seconds=(
                STATEMENT_SECONDS if kind == "statement_timeout" else LOCK_SECONDS
            ),
            elapsed_seconds=max(1, ceil(elapsed)),
        )
    except Exception:
        # The refusal's own line is already written; nothing else to do.
        pass
