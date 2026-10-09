"""The hourly maintenance task for Admin sessions and automation sessions.

Before this task, ``sessions.cleanup_admin_sessions`` ran only in tests, so
nothing deleted ended Admin session rows in production. The scheduler now
produces one ``automation_maintenance`` task at most once an hour (the key is
the hour), and the general worker runs it (see the Admin automation
specification, "Maintenance task"). Each run, step by step in short fenced
transactions:

- deletes ended Admin session rows (browser and command sessions) and their
  Django sessions, in bounded batches, then the command logins whose Admin
  session is gone;
- records the endings that role loss, removal or recovery already imply
  (``role_lost``, ``user_removed``, ``recovery``) with their notices;
- resolves the automation incident episodes quiet for an hour.
- removes the service status records of processes that have not reported
  for a day (ADM-13; ``service_status.prune_service_status``);
- removes refresh slot decisions eight days after their due time (#632;
  ``source.slot_decisions.prune_slot_decisions``);
- deletes the ``heartbeat`` and ``progress`` events of task runs that
  finished more than EVENT_RETENTION_DAYS ago, in batches, within a time
  budget (#386; ``prune_task_events``).

Every step is repeat-safe, so an interrupted run is simply retried.
"""

from time import monotonic
from uuid import UUID, uuid5

from django.db import connection, transaction

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.storage import StorageInvariantError

TASK_TYPE = "automation_maintenance"
NAMESPACE = UUID("b6f0c1f9-5a3e-4bb5-9c1e-3f7a2d6c8e41")
MAX_ATTEMPTS = 3
# Task event retention (#386, L2): the liveness events of runs that finished
# this many days ago are pruned. Changing it is a code change (this
# constant); the SQL function refuses fewer than 7.
EVENT_RETENTION_DAYS = 30
# How far past that the hourly pass looks for finished runs: it keeps up
# hour by hour, so a week covers any ordinary gap. After a longer outage an
# operator calls the function with a wider window until it returns 0 (the
# runtime guide's task event retention step).
EVENT_PRUNE_WINDOW_DAYS = 7
# Runs per pruning transaction (their liveness events go together), and the
# time one hourly pass may spend; the next pass resumes.
EVENT_PRUNE_RUNS = 20
EVENT_PRUNE_BUDGET_SECONDS = 60
# Each batch's own SQL limits, so it can never hold up other work for long;
# a batch stopped by either is logged and ends the pass.
EVENT_PRUNE_LIMITS = {"statement_timeout": 5, "lock_timeout": 1}


class MaintenanceProducer:
    """Enqueue each hour's maintenance task once, from the scheduler loop.

    The task is keyed by the hour, so a repeated enqueue would only find the
    same task; remembering the hour already produced saves the scheduler that
    transaction on every other pass.
    """

    def __init__(self):
        self.produced = None

    def __call__(self, guard):
        """Return this hour's new task, or nothing when it was already produced."""
        if not isinstance(guard, SchedulerGuard):
            raise PermissionError(
                "Automation maintenance requires the owned scheduler."
            )
        guard.check()
        # The database clock, read in its own short transaction as the
        # ownership helper requires; no lock is taken for the check.
        with transaction.atomic():
            hour = int(database_now().timestamp()) // 3600
        if hour == self.produced:
            return ()
        key = uuid5(NAMESPACE, str(hour))
        with work_transaction():
            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=key,
                idempotency_key=key,
                actor_id=None,
                correlation_id=key,
                admit=admit_maintenance,
            )
        self.produced = hour
        return (task.run_id,)


def _current(status):
    """The exact task row a hint names; a hint never supplies its own facts."""
    require_work_order()
    if status.task_type != TASK_TYPE:
        raise PermissionError("Automation maintenance task type differs.")
    return TaskRun.objects.get(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=TASK_TYPE,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
    )


def recover_maintenance(status):
    """An interrupted run left only committed, repeatable steps: run it again."""
    row = _current(status)
    if row.state != "abandoned":
        return None
    if row.attempt >= MAX_ATTEMPTS:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", 60)


def admit_maintenance(action, status):
    """Admit this compiled task's own lifecycle; no campaign gate applies."""
    require_work_order()
    if status.task_type != TASK_TYPE or not isinstance(status.domain_request_id, UUID):
        return False
    if action == "enqueue":
        return True
    row = _current(status)
    if action in {"lease_expired", "heartbeat", "complete"}:
        return True
    if action == "recovery_hint" and row.state == "running":
        return True
    if action.startswith("recovery_"):
        plan = recover_maintenance(status)
        return plan is not None and (action == "recovery_hint" or plan.action == action)
    return action in {"hint", "claim", "effect", "progress"}


def _execute(execution):
    """Run one maintenance pass, each step in its own fenced effect."""
    from parishkit.stewardship.service_status import prune_service_status
    from parishkit.stewardship.source.slot_decisions import prune_slot_decisions

    from .automation_sessions import maintain

    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Automation maintenance requires its ownership.")
    maintain(effect=execution.effect, check=execution.check)
    # The worker's hourly housekeeping also removes the service status
    # records of processes that stopped reporting a day ago (ADM-13).
    execution.check()
    with execution.effect():
        prune_service_status()
    # ...and the refresh slot decisions due more than eight days ago (#632).
    execution.check()
    with execution.effect():
        prune_slot_decisions()
    prune_task_events(execution)
    execution.transition("complete")


def prune_task_events(execution):
    """Delete old liveness events in batches, each in its own short transaction.

    Not under the work-order lock (``execution.effect``): a batch touches only
    finished runs' heartbeat and progress events, which no writer changes,
    so it orders against nothing; a repeated pass (a stale owner's) deletes
    nothing twice. The task is still checked before each batch. Each batch
    has its own statement and lock limits; a batch stopped by one is logged
    (what, limit, elapsed) and ends the pass, as does the time budget (an
    INFO ``retention_budget`` entry). Stops when a batch removes nothing.
    Returns how many events went.
    """
    from django.db import transaction

    from parishkit.stewardship.audit.timeouts import record_timeout
    from parishkit.stewardship.jobs.broker import sql_timeout_kind
    from parishkit.stewardship.observability import Event

    started, removed = monotonic(), 0
    while True:
        execution.check()
        batch = monotonic()
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                for kind, seconds in EVENT_PRUNE_LIMITS.items():
                    cursor.execute(f"SET LOCAL {kind} = '{seconds}s'")
                cursor.execute(
                    "SELECT public.stewardship_task_event_prune_v1(%s,%s,%s)",
                    [EVENT_RETENTION_DAYS, EVENT_PRUNE_WINDOW_DAYS, EVENT_PRUNE_RUNS],
                )
                count = cursor.fetchone()[0]
        except Exception as error:
            kind = sql_timeout_kind(error)
            if kind not in EVENT_PRUNE_LIMITS:
                raise
            record_timeout(
                Event.TASK_TIMED_OUT,
                what=kind,
                level="WARNING",
                task_id=execution.claim.run_id,
                limit_seconds=EVENT_PRUNE_LIMITS[kind],
                elapsed_seconds=monotonic() - batch,
            )
            return removed
        removed += count
        if count == 0:
            _prune_done(removed)
            return removed
        if monotonic() - started >= EVENT_PRUNE_BUDGET_SECONDS:
            record_timeout(
                Event.WORK_BUDGET_REACHED,
                what="retention_budget",
                level="INFO",
                task_id=execution.claim.run_id,
                limit_seconds=EVENT_PRUNE_BUDGET_SECONDS,
                elapsed_seconds=monotonic() - started,
            )
            return removed


def _prune_done(removed):
    """Debug-log how many events one pass removed (observation only)."""
    import json
    import logging

    debug = logging.getLogger("parishkit.stewardship.debug")
    if debug.isEnabledFor(logging.DEBUG):
        debug.debug("task event prune: %s", json.dumps({"removed": removed}))


def maintenance_handler(*, scheduler=False):
    """Schedulers only allocate and recover the task; the worker runs it."""
    if type(scheduler) is not bool:
        raise TypeError("Automation maintenance requires a compiled role.")

    def unavailable(execution):
        """A scheduler cannot clean up sessions."""
        raise PermissionError("The scheduler cannot run automation maintenance.")

    return Handler(
        WorkQueue.GENERAL,
        admit_maintenance,
        unavailable if scheduler else _execute,
        recover=recover_maintenance,
        scope=work_transaction,
    )
