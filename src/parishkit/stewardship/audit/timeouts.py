"""Durable records of work stopped by a time limit (#293).

Whenever a deadline, budget or lease stops work, an operational log entry
says what was stopped, which limit stopped it, and after how long. Some of
these moments come right before the process exits (a read guard's hard stop)
or inside a transaction that is about to roll back, so the entry is written on
its own short-lived database connection and committed immediately. Recording
never raises: a failure to record is logged to the process log instead, and
never replaces the original failure.
"""

import json
import logging
from copy import deepcopy
from threading import Thread
from uuid import UUID, uuid4

from django.db import connections

from parishkit.stewardship.observability import (
    Event,
    current_correlation,
    current_task,
    emit,
    emit_failure,
)

from .schemas import ContextKind, Outcome, sanitize

LEVELS = {
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
TIMEOUT_EVENTS = frozenset(
    {
        Event.TASK_TIMED_OUT,
        Event.HELPER_TIMED_OUT,
        Event.WORK_BUDGET_REACHED,
        Event.TASK_LEASE_LOST,
    }
)


def _seconds(value):
    """Whole, non-negative seconds for the log, or None when unknown."""
    if value is None:
        return None
    return max(0, int(round(value)))


def timeout_facts(
    *,
    what,
    helper=None,
    task_id=None,
    task_type=None,
    attempt=None,
    limit_seconds=None,
    elapsed_seconds=None,
    count=None,
    outcome=None,
):
    """The unsanitized ``timeout`` context values; unknown values are left out."""
    if outcome is not None and not isinstance(outcome, Outcome):
        raise ValueError("A timeout outcome must be a reviewed value.")
    values = {
        "what": what,
        "helper": helper,
        "task_id": task_id,
        "task_type": task_type,
        "attempt": attempt,
        "limit_seconds": _seconds(limit_seconds),
        "elapsed_seconds": _seconds(elapsed_seconds),
        "count": count,
        "outcome": outcome,
    }
    return {key: value for key, value in values.items() if value is not None}


def timeout_context(**values):
    """Build the sanitized ``timeout`` context from ``timeout_facts`` values."""
    return sanitize(ContextKind.TIMEOUT, timeout_facts(**values))


def insert_timeout(cursor, event, level, context):
    """Insert one sanitized timeout entry with ``cursor``.

    A plain INSERT needs no read access to the log, so logins that may only
    append timeout entries (mail dispatch, backup) can use it too.
    """
    cursor.execute(
        "INSERT INTO stewardship_operational_log "
        "(id,correlation_id,event,level,schema,context) "
        "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
        [
            uuid4(),
            current_correlation(),
            event.value,
            level,
            ContextKind.TIMEOUT.value,
            json.dumps(context),
        ],
    )


def _private_connection(settings_dict):
    """A fresh, short-lived connection that shares no transaction with the caller."""
    base = connections["default"]
    settings = deepcopy(base.settings_dict if settings_dict is None else settings_dict)
    settings.update(CONN_MAX_AGE=0, CONN_HEALTH_CHECKS=False)
    # Deployed OPTIONS carry their own, longer connect timeout; this write
    # must stay short, and a dead network must not hang it either.
    options = dict(settings.get("OPTIONS") or {})
    options.update(connect_timeout=2, tcp_user_timeout=2000)
    settings["OPTIONS"] = options
    return type(base)(settings, alias="default")


def record_timeout(
    event,
    *,
    what,
    level="ERROR",
    helper=None,
    task_id=None,
    task_type=None,
    attempt=None,
    limit_seconds=None,
    elapsed_seconds=None,
    count=None,
    outcome=None,
    settings_dict=None,
):
    """Persist one timeout entry on a private connection; never raise.

    ``task_id`` alone is enough for a task: its type and attempt are looked up
    when not given, and it defaults to the task the running worker bound.
    ``helper`` names a killed helper process by its entry point, and
    ``count`` says how many occurrences one summary entry stands for.
    ``settings_dict`` lets a helper thread (a read guard's deadline timer) use
    its owning thread's database login.
    """
    try:
        if event not in TIMEOUT_EVENTS or level not in LEVELS:
            raise ValueError("A timeout entry needs a reviewed event and level.")
        if task_id is None:
            # A helper deep inside a task names the task its worker bound.
            task_id = current_task()
        if task_id is not None and not isinstance(task_id, UUID):
            raise ValueError("A timeout entry names a task by its UUID.")
        emit(event, level=LEVELS[level], task_id=task_id)
        db = _private_connection(settings_dict)
        try:
            with db.cursor() as cursor:
                cursor.execute("SET statement_timeout = '2s'")
                cursor.execute("SET lock_timeout = '1s'")
                if task_id is not None and (task_type is None or attempt is None):
                    try:
                        cursor.execute(
                            "SELECT task_type, attempt FROM stewardship_task_run "
                            "WHERE id=%s",
                            [task_id],
                        )
                        row = cursor.fetchone()
                    except Exception:
                        # A login without task read access still records the
                        # entry; only the task's type and attempt are missing.
                        row = None
                    if row is not None:
                        task_type = task_type or row[0]
                        attempt = attempt if attempt is not None else row[1]
                context = timeout_context(
                    what=what,
                    helper=helper,
                    task_id=task_id,
                    task_type=task_type,
                    attempt=attempt,
                    limit_seconds=limit_seconds,
                    elapsed_seconds=elapsed_seconds,
                    count=count,
                    outcome=outcome,
                )
                insert_timeout(cursor, event, level, context)
        finally:
            db.close()
    except Exception as error:
        # Recording is best effort; the stopped work already has its own outcome.
        emit_failure(
            error, event=event if event in TIMEOUT_EVENTS else Event.TASK_FAILED
        )


def record_timeout_within(seconds, event, **values):
    """Record like ``record_timeout``, but wait at most ``seconds`` for it.

    For callers about to stop work that must not outlive its limit (a read
    guard's abort): the write runs on a daemon thread, and the caller moves on
    after ``seconds`` even if the database is slow or unreachable.
    """
    writer = Thread(
        target=record_timeout,
        args=(event,),
        kwargs=values,
        name="stewardship-timeout-log",
        daemon=True,
    )
    writer.start()
    writer.join(timeout=seconds)
