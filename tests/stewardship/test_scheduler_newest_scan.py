"""The scheduler's newest-due scan (#1921), without a database.

The PostgreSQL behaviour (held rows ahead of a new request, lock takes and
the index range) is in database/test_scheduler_process_postgresql.py.
"""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import processes
from parishkit.stewardship.jobs.broker import BrokerRuntime
from parishkit.stewardship.jobs.scanning import (
    RecentHints,
    ScanCursor,
    _admit_page,
    collect_newest_hints,
)
from parishkit.stewardship.jobs.scheduler import (
    NewestScanResult,
    ScanResult,
    SchedulerOwnershipLost,
)

NOW = datetime(2054, 10, 3, 12, tzinfo=UTC)
CURSOR = ScanCursor(NOW, uuid4())


def run_loops(monkeypatch, *, loops, sweep, newest=None):
    """Run serve_scheduler for ``loops`` loops with stubbed scans.

    ``sweep`` gives the cursor each scan_once call returns, in order;
    ``newest`` replaces scan_newest. Returns the ordered calls and the
    delays the loop waited.
    """
    calls, delays, cursors = [], [], iter(sweep)

    @contextmanager
    def session():
        yield Mock()

    def scan_once(guard, **kwargs):
        """Record a sweep scan and return the next scripted cursor."""
        calls.append(("sweep", kwargs["cursor"], kwargs.get("limit")))
        return ScanResult(next(cursors), 0, 0)

    def scan_newest(guard, **kwargs):
        """Record a newest-due scan; each ends at a later clock."""
        calls.append(("newest", kwargs["since"]))
        return NewestScanResult(NOW + timedelta(seconds=len(calls)), 1, 0)

    stop = Event()

    def heartbeat():
        """Stop once the producers ran ``loops`` times."""
        if sum(call == "produce" for call in calls) == loops:
            stop.set()

    def wait(delay):
        """Record the loop delay without sleeping."""
        delays.append(delay)
        return stop.is_set()

    monkeypatch.setattr(processes, "scheduler_session", session)
    monkeypatch.setattr(processes, "scan_once", scan_once)
    monkeypatch.setattr(processes, "scan_newest", newest or scan_newest)
    monkeypatch.setattr(processes, "DueWorkScan", Mock)
    monkeypatch.setattr(processes, "emit_failure", lambda error: calls.append(error))
    monkeypatch.setattr(stop, "wait", wait)
    processes.serve_scheduler(
        BrokerRuntime(Mock(), ServiceRole.SCHEDULER),
        handlers={},
        lease=Mock(),
        stop=stop,
        heartbeat=heartbeat,
        produce=lambda guard: calls.append("produce"),
    )
    return calls, delays


def test_the_newest_due_scan_runs_first_on_every_loop(monkeypatch, caplog):
    """Before the early scan when a sweep starts, and mid-sweep too.

    Each scan's window starts where the previous one's clock ended, and the
    count it hinted goes to the DEBUG log only.
    """
    with caplog.at_level(logging.DEBUG, logger="parishkit.stewardship.debug"):
        calls, delays = run_loops(monkeypatch, loops=2, sweep=[None, CURSOR, None])
    early = processes.EARLY_SCAN_ROWS
    assert calls == [
        ("newest", None),
        ("sweep", None, early),
        "produce",
        ("sweep", None, None),
        ("newest", NOW + timedelta(seconds=1)),
        "produce",
        ("sweep", CURSOR, None),
    ]
    assert delays == [2, 2]
    records = [r for r in caplog.records if r.name == "parishkit.stewardship.debug"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.DEBUG, "newest-due scan: 1 hinted, 0 unconfirmed")
    ] * 2


def test_a_failed_newest_due_scan_is_reported_and_the_loop_goes_on(monkeypatch):
    """The failure is reported, the sweep still runs and the loop backs off."""

    def failing(guard, **kwargs):
        raise RuntimeError("synthetic newest failure")

    calls, delays = run_loops(monkeypatch, loops=1, sweep=[None, None], newest=failing)
    [error, *rest] = calls
    assert str(error) == "synthetic newest failure"
    assert rest == [
        ("sweep", None, processes.EARLY_SCAN_ROWS),
        "produce",
        ("sweep", None, None),
    ]
    assert delays == [4]


def test_lost_ownership_in_the_newest_due_scan_is_fatal(monkeypatch):
    """Nothing else runs, and nothing is reported as an ordinary failure."""

    def lost(guard, **kwargs):
        raise SchedulerOwnershipLost("synthetic loss")

    with pytest.raises(SchedulerOwnershipLost):
        run_loops(monkeypatch, loops=1, sweep=[], newest=lost)


def candidate(**fields):
    """A selected row stand-in; only the fields admission reads first."""
    return SimpleNamespace(
        **({"pk": uuid4(), "version": 1, "task_type": "probe"} | fields)
    )


def test_the_sweep_skips_a_row_the_newest_scan_just_checked():
    """No second check in a loop; health counts it unknown; next loop retries.

    The handler registry is empty, so reaching admission would raise.
    """
    row = candidate()
    recent, health = RecentHints(), Mock()
    recent.newest_checked[row.pk] = row.version
    recent.newest_seen[row.pk] = (NOW, row.version)
    assert (
        _admit_page(
            [row], now=NOW, handlers={}, bulk=set(), health=health, recent=recent
        )
        == ()
    )
    health.unknown.assert_called_once_with()
    health.admitted.assert_not_called()
    assert row.pk not in recent.newest_seen


def test_a_changed_row_is_checked_again_by_the_sweep():
    """A version bump since the newest-due scan means fresh admission."""
    row = candidate(version=2)
    recent = RecentHints()
    recent.newest_checked[row.pk] = 1
    with pytest.raises(KeyError):
        _admit_page([row], now=NOW, handlers={}, bulk=set(), recent=recent)


def test_a_row_published_lately_is_skipped_by_either_scan():
    """RecentHints comes first, so a just-published row is never re-admitted."""
    row = candidate()
    recent, health = RecentHints(clock=lambda: 0.0), Mock()
    recent.admitted(row)
    recent.published(row.pk)
    assert (
        _admit_page([row], now=NOW, handlers={}, bulk=set(), recent=recent, newest=True)
        == ()
    )
    assert row.pk not in recent.newest_checked
    assert (
        _admit_page(
            [row], now=NOW, handlers={}, bulk=set(), health=health, recent=recent
        )
        == ()
    )
    health.admitted.assert_called_once_with(row, NOW)


@pytest.mark.parametrize(
    "options",
    [
        {"recent": None},
        {"recent": RecentHints(), "limit": 0},
        {"recent": RecentHints(), "limit": 101},
    ],
)
def test_the_newest_due_scan_is_bounded_and_remembers(options):
    """It needs the scheduler's hint memory and a small bound."""
    with pytest.raises(ValueError):
        collect_newest_hints(handlers={}, **options)
