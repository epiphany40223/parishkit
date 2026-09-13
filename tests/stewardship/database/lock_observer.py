"""Observe an actual PostgreSQL waiter before releasing a race-test blocker."""

from time import monotonic, sleep

from django.db import connection


def backend_pid():
    """Identify this thread's actual backend before it attempts the contested lock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def wait_for_lock(pid, *, timeout=5):
    """A start event is not proof: require a server-observed lock and blocker."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            # The caller may be the transaction holding the competing lock;
            # discard its cached activity snapshot before observing the waiter.
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT wait_event_type='Lock' "
                "AND cardinality(pg_blocking_pids(pid))>0 "
                "FROM pg_stat_activity WHERE pid=%s",
                [pid],
            )
            if cursor.fetchone() == (True,):
                return
        sleep(0.01)
    raise AssertionError("The test backend never became a blocked lock waiter.")
