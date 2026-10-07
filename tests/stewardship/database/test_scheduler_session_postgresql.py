"""Actual cross-connection scheduler exclusion and lost broker/connection safety."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections

from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import (
    HintPublicationUnavailable,
    SchedulerBusy,
    SchedulerOwnershipLost,
    scan_once,
    scheduler_session,
)

from .test_dispatch_postgresql import queued

pytestmark = pytest.mark.django_db(transaction=True)


def contender():
    """Acquire using another backend, never advisory-lock reentrancy on this one."""
    try:
        with scheduler_session() as guard:
            guard.check()
            return True
    finally:
        connections.close_all()


def test_one_scheduler_session_excludes_another_until_release():
    """A second process cannot start scanning while the first owns its session."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        with scheduler_session() as guard:
            guard.check()
            future = pool.submit(contender)
            with pytest.raises(SchedulerBusy):
                future.result()
        assert pool.submit(contender).result()
    with pytest.raises(SchedulerOwnershipLost):
        guard.check()


def test_connection_loss_cannot_silently_continue_after_reconnect():
    """The retained guard cannot transfer to a fresh unlocked PostgreSQL session."""
    with scheduler_session() as guard:
        connection.close()
        connection.ensure_connection()
        with pytest.raises(SchedulerOwnershipLost):
            guard.check()
    with scheduler_session() as replacement:
        replacement.check()


def test_publish_failure_does_not_strand_or_claim_durable_work():
    """A failed publication replays safely and never runs under a database tx."""
    task, seen = queued(), []
    handlers = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None
        )
    }

    def failed(hint):
        """Model acceptance followed by an ambiguous transport response."""
        assert not connection.in_atomic_block
        seen.append(hint.run_id)
        raise HintPublicationUnavailable("synthetic broker failure")

    with scheduler_session() as guard:
        result = scan_once(guard, handlers=handlers, publish=failed)
        assert result.unconfirmed == 1 and result.published == 0
        result = scan_once(
            guard, handlers=handlers, publish=lambda hint: seen.append(hint.run_id)
        )
        assert result.published == 1 and result.unconfirmed == 0
    assert seen == [task.run_id, task.run_id]
    assert TaskRun.objects.get().state == "queued"


def test_one_unpublishable_hint_does_not_starve_later_pages():
    """Transport failure advances paging without deleting the failed operation."""
    first, second, seen = queued(), queued(), []
    handlers = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None
        )
    }

    def publish(hint):
        """Only the first synthetic route fails to confirm publication."""
        if hint.run_id == first.run_id:
            raise HintPublicationUnavailable("synthetic queue unavailable")
        seen.append(hint.run_id)

    with scheduler_session() as guard:
        result = scan_once(guard, handlers=handlers, publish=publish, limit=1)
        assert result.unconfirmed == 1 and result.cursor is not None
        result = scan_once(
            guard, handlers=handlers, publish=publish, cursor=result.cursor, limit=1
        )
        assert result.published == 1 and seen == [second.run_id]
    assert TaskRun.objects.filter(state="queued").count() == 2


def test_recent_hint_is_not_readmitted_until_changed_or_expired():
    """A sweep skips a row it just hinted, but still counts it for health (#394).

    Admission runs in the handler's scope, which is where production
    handlers take the global work-order lock. A published, unchanged row is
    skipped without entering it; the due-work health sample still sees the
    row as admitted, not unknown. A failed publication is not remembered,
    a version change is admitted at once, and the window expires.
    """
    from contextlib import contextmanager
    from unittest.mock import Mock
    from uuid import uuid4

    from parishkit.stewardship.jobs.scanning import RecentHints
    from parishkit.stewardship.jobs.storage import change_run

    entered, seen, now = [], [], [0.0]

    @contextmanager
    def scope():
        """Record each admission, as the work-order lock would be taken."""
        entered.append(1)
        yield

    handlers = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None, scope=scope
        )
    }
    recent = RecentHints(clock=lambda: now[0])
    task, health = queued(), Mock()

    def scan(publish=lambda hint: seen.append(hint.run_id)):
        """One scan with this test's memory, publisher and health sample."""
        entered.clear()
        return scan_once(
            guard, handlers=handlers, publish=publish, health=health, recent=recent
        )

    def failed(hint):
        """The transport could not confirm this hint."""
        raise HintPublicationUnavailable("synthetic broker failure")

    with scheduler_session() as guard:
        assert scan(failed).unconfirmed == 1 and entered
        # Not published, so not remembered: the next scan admits it again.
        assert scan().published == 1 and entered
        health.reset_mock()
        now[0] = 44
        assert scan().published == 0 and not entered
        health.admitted.assert_called_once()
        assert health.admitted.call_args.args[0].pk == task.run_id
        health.unknown.assert_not_called()
        # The window has passed: a lost hint is replaced.
        now[0] = 46
        assert scan().published == 1 and entered
        # A changed row is admitted at once, inside the window: here a claim
        # whose lease has already expired, so the row is due for recovery.
        change_run(
            run_id=task.run_id,
            expected_version=TaskRun.objects.get(pk=task.run_id).version,
            action="claim",
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
            lease_seconds=1,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
        now[0] = 47
        assert scan().published == 1 and entered
    assert seen == [task.run_id] * 3


def test_server_ownership_probe_runs_at_most_once_per_interval():
    """Local checks run every call; the pg_locks query at most once a second (#629).

    The in-session unlock below is something no production code does; it
    stands in for a server-side loss the local checks cannot see, to show
    how long a confirmation stands and that it then lapses. With
    PROBE_SECONDS at 0 the mid-window check fails.
    """
    from parishkit.stewardship.jobs.scheduler import PROBE_SECONDS, SCHEDULER_LOCK

    now = [100.0]
    with scheduler_session() as guard:
        guard.clock = lambda: now[0]
        guard.check()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s,%s)", SCHEDULER_LOCK)
            assert cursor.fetchone() == (True,)
        # Within the interval the last confirmation stands.
        now[0] += PROBE_SECONDS / 2
        guard.check()
        # Once it lapses, the server is asked again and the loss shows.
        now[0] += PROBE_SECONDS
        with pytest.raises(SchedulerOwnershipLost):
            guard.check()
        # A refused probe is not remembered: the next call asks again.
        with pytest.raises(SchedulerOwnershipLost):
            guard.check()
        # Owned again, the next call confirms it, so the release succeeds.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s,%s)", SCHEDULER_LOCK)
        guard.check()
    with scheduler_session() as replacement:
        replacement.check()


def test_terminated_backend_fails_its_next_statement_and_check():
    """A session ended by the server within the window cannot keep working (#629).

    pg_terminate_backend from another connection ends the scheduler's
    session and frees its lock, so another scheduler can start at once. The
    old process is not told: its next statement fails, and that failure
    marks the connection closed, so its next check refuses even inside
    the probe interval.
    """
    from django.db import InterfaceError, OperationalError

    def terminate(pid):
        """End the scheduler's backend from a second connection and wait."""
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s, 5000)", [pid])
                return cursor.fetchone()[0]
        finally:
            connections.close_all()

    now = [100.0]
    with ThreadPoolExecutor(max_workers=1) as pool, scheduler_session() as guard:
        guard.clock = lambda: now[0]
        guard.check()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid = cursor.fetchone()[0]
        assert pool.submit(terminate, pid).result()
        assert pool.submit(contender).result()
        with (
            pytest.raises((OperationalError, InterfaceError)),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT 1")
        with pytest.raises(SchedulerOwnershipLost):
            guard.check()
    with scheduler_session() as replacement:
        replacement.check()
