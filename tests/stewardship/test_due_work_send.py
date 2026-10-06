"""A bulk Family send's backlog is judged by its progress, not per task (#634).

The scan's per-page bookkeeping and the send verdict are pure; the reads they
make (which messages are Family send work, whether a send is in progress and
the send's progress) are replaced here and exercised against PostgreSQL with
the real scheduler login in ``database/test_due_work_health_postgresql.py``.
"""

import logging
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs import due_work_health
from parishkit.stewardship.jobs.due_work_health import (
    DELIVERY_TASK_TYPE,
    MAX_GAP,
    SEND_BOUND,
    SEND_STALL,
    DueWorkScan,
    SendEvidence,
    send_context,
)

NOW = datetime(2026, 10, 8, 10, 30, tzinfo=UTC)
DEFINITION, REVISION = uuid4(), uuid4()
SEND = (DEFINITION, REVISION, "production", 1)


def task(lag, *, task_type=DELIVERY_TASK_TYPE, state="queued", message=None):
    """A due TaskRun stand-in that has waited ``lag`` since its due time."""
    return SimpleNamespace(
        state=state,
        task_type=task_type,
        not_before=NOW - lag,
        lease_expires_at=NOW - lag,
        updated_at=NOW - lag,
        domain_request_id=message or uuid4(),
    )


def evidence(*, remaining, done, progress):
    """The send's progress, its newest settlement ``progress`` before NOW."""
    return SendEvidence(
        DEFINITION,
        REVISION,
        remaining,
        done,
        None if progress is None else NOW - progress,
    )


@pytest.fixture(autouse=True)
def savepoints(monkeypatch):
    """No database here: the reads' savepoints are no-ops (real in PostgreSQL)."""
    monkeypatch.setattr(due_work_health.transaction, "atomic", nullcontext)


@pytest.fixture
def send(monkeypatch):
    """Every message is the one send's; ``state`` sets its progress and status."""
    state = SimpleNamespace(
        active=True,
        evidence=evidence(remaining=700, done=300, progress=timedelta(seconds=3)),
        reads=0,
    )

    def read(*key):
        """Count the evidence reads, which must happen once per sweep."""
        assert key == SEND
        state.reads += 1
        return state.evidence

    monkeypatch.setattr(
        due_work_health, "family_sends", lambda ids: dict.fromkeys(ids, SEND)
    )
    monkeypatch.setattr(due_work_health, "send_in_progress", lambda: state.active)
    monkeypatch.setattr(due_work_health, "send_evidence", read)
    return state


def sweep(scan, rows, *, page=100):
    """Admit ``rows`` in pages, judging each page as ``finish`` does."""
    for start in range(0, len(rows), page):
        for row in rows[start : start + page]:
            scan.admitted(row, NOW)
        scan._judge()


def test_delivery_task_type_is_the_dispatch_task_type():
    """The scan's literal is the Family delivery task's real type."""
    from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE

    assert DELIVERY_TASK_TYPE == TASK_TYPE


def test_a_progressing_bulk_send_of_a_thousand_raises_nothing(send):
    """1,000 delivery tasks due at 06:00, still waiting 17 minutes later."""
    scan = DueWorkScan()
    rows = [task(timedelta(minutes=17) - timedelta(seconds=i)) for i in range(1000)]
    sweep(scan, rows)
    assert not scan.late and scan.details() == {}
    # The send is read once for the whole sweep, not once per page.
    assert send.reads == 1
    # Later in the send, 25 minutes in, and close to the two-hour bound.
    for lag in (timedelta(minutes=25), SEND_BOUND):
        scan = DueWorkScan()
        sweep(scan, [task(lag) for _ in range(300)])
        assert not scan.late


def test_a_stalled_send_raises_with_its_context(send):
    """Nothing settled for longer than SEND_STALL: late, saying which send."""
    send.evidence = evidence(
        remaining=650, done=327, progress=SEND_STALL + timedelta(seconds=1)
    )
    scan = DueWorkScan()
    sweep(scan, [task(timedelta(minutes=30)) for _ in range(650)])
    assert scan.late
    stall = SEND_STALL + timedelta(seconds=1)
    assert scan.details() == {
        "task_type": "outbox_delivery",
        "definition_id": str(DEFINITION),
        "revision_id": str(REVISION),
        "remaining_count": 650,
        "done_count": 327,
        "stall_seconds": int(stall.total_seconds()),
        "elapsed_seconds": 1800,
        "limit_seconds": int(SEND_STALL.total_seconds()),
    }


def test_progress_before_the_due_time_does_not_count():
    """A reminder prepared ahead has made no progress until it is sending."""
    prepared = evidence(remaining=1000, done=0, progress=timedelta(hours=1))
    assert send_context(prepared, now=NOW, lag=SEND_STALL) is None
    late = send_context(prepared, now=NOW, lag=SEND_STALL + timedelta(seconds=1))
    assert late["stall_seconds"] == late["elapsed_seconds"] == 601
    nothing = evidence(remaining=0, done=0, progress=None)
    late = send_context(nothing, now=NOW, lag=timedelta(minutes=11))
    assert late["stall_seconds"] == 660
    assert late["remaining_count"] == late["done_count"] == 0


def test_a_send_still_going_past_its_bound_is_late():
    """Steady progress does not excuse a send two hours past its due time."""
    steady = evidence(remaining=50, done=950, progress=timedelta(seconds=5))
    assert send_context(steady, now=NOW, lag=SEND_BOUND) is None
    late = send_context(steady, now=NOW, lag=SEND_BOUND + timedelta(seconds=1))
    assert late["limit_seconds"] == int(SEND_BOUND.total_seconds())
    assert late["stall_seconds"] == 5


def test_without_a_send_in_progress_delivery_tasks_keep_the_per_task_rule(send):
    """A send's last few messages, or a paused send, are not a bulk backlog."""
    send.active = False
    scan = DueWorkScan()
    sweep(scan, [task(timedelta(minutes=17)) for _ in range(9)])
    assert scan.late and send.reads == 0
    assert scan.details() == {
        "task_type": "outbox_delivery",
        "count": 9,
        "lag_seconds": 1020,
        "limit_seconds": 90,
    }


def test_other_task_types_and_messages_keep_the_per_task_rule(send, monkeypatch):
    """Only waiting Family send deliveries are set aside; the rest are unchanged."""
    for row in (
        task(timedelta(seconds=91), task_type="family_mail_prepare"),
        task(timedelta(seconds=91), task_type="operational_collect"),
        # A delivery whose worker lease expired is a recovery, not a backlog.
        task(timedelta(seconds=91), state="running"),
    ):
        scan = DueWorkScan()
        scan.admitted(task(MAX_GAP, task_type=row.task_type), NOW)
        assert not scan.late and not scan.deferred
        scan.admitted(row, NOW)
        scan._judge()
        assert scan.late and send.reads == 0
        assert scan.details()["task_type"] == row.task_type
        assert scan.details()["lag_seconds"] == 91
    # A receipt, digest or alert delivery is not Family send work.
    monkeypatch.setattr(due_work_health, "family_sends", lambda ids: {})
    scan = DueWorkScan()
    sweep(scan, [task(timedelta(seconds=91)), task(timedelta(minutes=3))])
    assert scan.late and scan.details()["count"] == 2
    assert scan.details()["lag_seconds"] == 180


def test_a_late_task_beside_a_healthy_send_still_raises(send):
    """The send's tasks are excused; an unrelated late task is not."""
    scan = DueWorkScan()
    rows = [task(timedelta(minutes=17)) for _ in range(200)]
    rows.append(task(timedelta(minutes=2), task_type="operational_collect"))
    sweep(scan, rows)
    assert scan.late
    assert scan.details() == {
        "task_type": "operational_collect",
        "count": 1,
        "lag_seconds": 120,
        "limit_seconds": 90,
    }


def test_reset_forgets_the_sweep(send):
    """Each sweep reads whether a send is in progress and its progress afresh."""
    scan = DueWorkScan()
    sweep(scan, [task(timedelta(minutes=17))])
    assert scan.sends and scan.active
    scan.reset()
    assert (scan.sends, scan.active, scan.deferred, scan.stalled) == (
        {},
        None,
        {},
        None,
    )


def test_a_stalled_send_beside_a_late_task_keeps_the_send_and_adds_the_count(send):
    """The send's context is chosen; the per-task late count is added to it."""
    send.evidence = evidence(remaining=650, done=327, progress=timedelta(minutes=12))
    scan = DueWorkScan()
    rows = [task(timedelta(minutes=30)) for _ in range(5)]
    rows += [task(timedelta(minutes=2), task_type="operational_collect")] * 2
    sweep(scan, rows)
    details = scan.details()
    assert details["definition_id"] == str(DEFINITION)
    assert details["other_late_count"] == 2 and "count" not in details
    assert details["stall_seconds"] == 720 and "lag_seconds" not in details


def test_a_context_that_fails_validation_is_reported_empty():
    """details() never raises into the checkpoint; the entry is kept, unexplained."""
    scan = DueWorkScan()
    scan.admitted(task(timedelta(minutes=2), task_type="Not A Type"), NOW)
    assert scan.late and scan.details() == {}


def test_a_failed_progress_read_leaves_only_the_send_unknown(send, monkeypatch):
    """Receipts and alerts beside the send keep the per-task rule (#643)."""
    family = {uuid4() for _ in range(3)}
    monkeypatch.setattr(
        due_work_health,
        "family_sends",
        lambda ids: {message: SEND for message in ids if message in family},
    )

    def broken(*key):
        """The send's progress cannot be read (a statement timeout, say)."""
        raise RuntimeError("synthetic read failure")

    failures = []
    monkeypatch.setattr(due_work_health, "send_evidence", broken)
    monkeypatch.setattr(
        due_work_health, "emit_failure", lambda error, **kw: failures.append(kw)
    )
    scan = DueWorkScan()
    rows = [task(timedelta(minutes=17), message=message) for message in family]
    rows.append(task(timedelta(minutes=2)))  # a receipt, say
    sweep(scan, rows)
    assert scan.uncertain and scan.late
    assert scan.details()["count"] == 1 and scan.details()["lag_seconds"] == 120
    assert failures == [{"level": logging.WARNING}]
    # Telling Family work apart failing leaves every set-aside task unknown.
    monkeypatch.setattr(due_work_health, "family_sends", broken)
    scan = DueWorkScan()
    sweep(scan, rows)
    assert scan.uncertain and not scan.late and scan.details() == {}
