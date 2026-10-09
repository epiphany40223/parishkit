"""The hourly maintenance prunes old liveness events in batches (#386, L2).

The SQL function is a stand-in here (its rules are exercised against
PostgreSQL in ``database/test_task_event_retention_postgresql.py``); the
batching, the limits, the budget and their log lines are the real code.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import automation_maintenance as maintenance


class Timeout(Exception):
    """What PostgreSQL raises when a batch's lock_timeout stops it."""

    sqlstate = "55P03"


def run(monkeypatch, counts, *, clock_step=0.0):
    """Drive prune_task_events with scripted batch results and a fake clock."""
    calls, entries, atomics = [], [], []
    clock = {"now": 0.0}

    class Cursor:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            if "stewardship_task_event_prune_v1" in sql:
                clock["now"] += clock_step
                step = counts[0]
                if isinstance(step, BaseException):
                    counts.pop(0)
                    raise step

        def fetchone(self):
            return (counts.pop(0),)

    @contextmanager
    def cursor():
        yield Cursor()

    @contextmanager
    def atomic():
        atomics.append(1)
        yield

    def effect():
        raise AssertionError("the prune must not take the work-order lock")

    monkeypatch.setattr(maintenance.connection, "cursor", cursor)
    monkeypatch.setattr("django.db.transaction.atomic", atomic)
    monkeypatch.setattr(maintenance, "monotonic", lambda: clock["now"])
    from parishkit.stewardship.audit import timeouts

    monkeypatch.setattr(
        timeouts, "record_timeout", lambda event, **facts: entries.append(facts)
    )
    execution = SimpleNamespace(
        check=lambda: None, effect=effect, claim=SimpleNamespace(run_id=uuid4())
    )
    removed = maintenance.prune_task_events(execution)
    prunes = [params for sql, params in calls if "prune_v1" in sql]
    limits = [sql for sql, _ in calls if sql.startswith("SET LOCAL")]
    return removed, prunes, limits, entries, atomics


def test_batches_run_until_one_removes_nothing(monkeypatch):
    """Each batch is its own transaction with its own limits, never the
    work-order lock; the pass ends when a batch finds nothing left."""
    removed, prunes, limits, entries, atomics = run(monkeypatch, [300, 40, 0])
    assert removed == 340 and entries == [] and len(atomics) == 3
    assert prunes[0] == [
        maintenance.EVENT_RETENTION_DAYS,
        maintenance.EVENT_PRUNE_WINDOW_DAYS,
        maintenance.EVENT_PRUNE_RUNS,
    ]
    assert limits[:2] == [
        "SET LOCAL statement_timeout = '5s'",
        "SET LOCAL lock_timeout = '1s'",
    ]
    assert maintenance.EVENT_RETENTION_DAYS == 30


def test_the_time_budget_stops_the_pass_and_is_logged(monkeypatch):
    """Past the budget the pass stops after its batch, with an INFO
    retention_budget entry naming the limit and the elapsed time."""
    half = maintenance.EVENT_PRUNE_BUDGET_SECONDS / 2
    removed, prunes, _, [entry], _ = run(monkeypatch, [5] * 10, clock_step=half)
    assert len(prunes) == 2 and removed == 10
    assert (entry["what"], entry["level"]) == ("retention_budget", "INFO")
    assert entry["limit_seconds"] == maintenance.EVENT_PRUNE_BUDGET_SECONDS
    assert entry["elapsed_seconds"] >= maintenance.EVENT_PRUNE_BUDGET_SECONDS
    assert "count" not in entry


def test_a_batch_stopped_by_its_own_limit_is_logged_and_ends_the_pass(monkeypatch):
    """A lock timeout: a WARNING naming the limit, the batch's elapsed time
    and the task, and nothing more is tried this pass."""
    removed, prunes, _, [entry], _ = run(monkeypatch, [7, Timeout(), 9], clock_step=0.5)
    assert removed == 7 and len(prunes) == 2
    assert (entry["what"], entry["level"], entry["limit_seconds"]) == (
        "lock_timeout",
        "WARNING",
        1,
    )
    assert entry["elapsed_seconds"] == 0.5


def test_any_other_error_propagates(monkeypatch):
    """Only the batch's own limits are absorbed."""
    with pytest.raises(RuntimeError):
        run(monkeypatch, [RuntimeError("synthetic")])


def test_the_maintenance_task_runs_the_prune(monkeypatch):
    """_execute calls prune_task_events after its other steps."""
    from contextlib import nullcontext

    from parishkit.stewardship import service_status
    from parishkit.stewardship.accounts import automation_sessions
    from parishkit.stewardship.source import slot_decisions

    order = []
    monkeypatch.setattr(maintenance.connection, "in_atomic_block", False)
    monkeypatch.setattr(automation_sessions, "maintain", lambda **k: order.append("s"))
    monkeypatch.setattr(service_status, "prune_service_status", lambda: None)
    monkeypatch.setattr(
        slot_decisions, "prune_slot_decisions", lambda: order.append("slots")
    )
    monkeypatch.setattr(
        maintenance, "prune_task_events", lambda execution: order.append("prune")
    )
    execution = SimpleNamespace(
        control=SimpleNamespace(active=True),
        check=lambda: None,
        effect=nullcontext,
        transition=lambda action: order.append(action),
    )
    maintenance._execute(execution)
    assert order == ["s", "slots", "prune", "complete"]
