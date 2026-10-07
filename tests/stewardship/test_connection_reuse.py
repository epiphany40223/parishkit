"""A mail consumer keeps only a clean main-thread connection between messages (#365)."""

from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from psycopg.pq import TransactionStatus

from parishkit.stewardship import runtime_process
from parishkit.stewardship.jobs import broker as broker_module
from parishkit.stewardship.jobs import connection_reuse
from parishkit.stewardship.jobs.broker import HINT_TASK

from .test_broker import broker


class FakeDatabase:
    """A Django connection wrapper with only what connection_reuse reads."""

    def __init__(self, status=TransactionStatus.IDLE):
        """Start open, idle and clean."""
        self.connection = SimpleNamespace(
            info=SimpleNamespace(transaction_status=status)
        )
        self.in_atomic_block = self.needs_rollback = self.errors_occurred = False
        self.autocommit = True
        self.closed = 0

    def get_autocommit(self):
        """Django's autocommit flag."""
        return self.autocommit

    def close(self):
        """Count closes and drop the driver connection, as Django does."""
        self.closed += 1
        self.connection = None


class FakeConnections:
    """Stands in for django.db.connections."""

    def __init__(self, *databases):
        """Hold the given connection wrappers."""
        self.databases = list(databases)

    def all(self, initialized_only=False):
        """Every wrapper, as the real handler returns this thread's ones."""
        return list(self.databases)


@pytest.fixture
def reuse(monkeypatch):
    """Isolate the module's process switch, clock and connection handler."""
    monkeypatch.setattr(connection_reuse, "_enabled", False)
    monkeypatch.setattr(connection_reuse, "_kept", connection_reuse.WeakKeyDictionary())
    clock = [1000.0]
    monkeypatch.setattr(connection_reuse, "monotonic", lambda: clock[0])

    def use(*databases):
        """Install a fake handler over ``databases``."""
        monkeypatch.setattr(
            connection_reuse, "connections", FakeConnections(*databases)
        )

    return SimpleNamespace(use=use, clock=clock)


def test_release_closes_everything_unless_the_process_keeps_connections(reuse):
    """Every service but mail dispatch behaves exactly as close_all() did."""
    database = FakeDatabase()
    reuse.use(database)
    connection_reuse.release()
    assert database.closed == 1


def test_a_clean_connection_is_kept_across_messages(reuse):
    """Once enabled, an idle, error-free main-thread connection stays open."""
    connection_reuse.keep_connections()
    database = FakeDatabase()
    reuse.use(database)
    for _ in range(3):
        connection_reuse.release()
    assert database.closed == 0


@pytest.mark.parametrize(
    "spoil",
    [
        lambda db: setattr(db, "in_atomic_block", True),
        lambda db: setattr(db, "needs_rollback", True),
        lambda db: setattr(db, "errors_occurred", True),
        lambda db: setattr(db, "autocommit", False),
        lambda db: setattr(
            db.connection.info, "transaction_status", TransactionStatus.INTRANS
        ),
        lambda db: setattr(
            db.connection.info, "transaction_status", TransactionStatus.INERROR
        ),
        lambda db: setattr(db.connection, "info", None),
    ],
    ids=[
        "atomic",
        "rollback",
        "error",
        "autocommit",
        "intrans",
        "inerror",
        "unreadable",
    ],
)
def test_an_unclean_connection_is_closed(reuse, spoil):
    """Anything that could carry state or an error into the next message closes it."""
    connection_reuse.keep_connections()
    database = FakeDatabase()
    spoil(database)
    reuse.use(database)
    connection_reuse.release()
    assert database.closed == 1


def test_a_connection_is_kept_at_most_max_age(reuse):
    """The age bound makes a kept connection pass connect-time admission again."""
    connection_reuse.keep_connections()
    database = FakeDatabase()
    reuse.use(database)
    connection_reuse.release()
    reuse.clock[0] += connection_reuse.MAX_AGE_SECONDS - 1
    connection_reuse.release()
    assert database.closed == 0
    reuse.clock[0] += 1
    connection_reuse.release()
    assert database.closed == 1


def test_a_new_driver_connection_restarts_the_age(reuse):
    """The clock follows the driver connection, not Django's reused wrapper."""
    connection_reuse.keep_connections()
    database = FakeDatabase()
    reuse.use(database)
    connection_reuse.release()
    reuse.clock[0] += connection_reuse.MAX_AGE_SECONDS
    database.connection = FakeDatabase().connection  # Reconnected meanwhile.
    connection_reuse.release()
    assert database.closed == 0


def test_only_the_main_thread_keeps_a_connection(reuse):
    """Renewal and check threads keep their own short connections (#336 budget)."""
    connection_reuse.keep_connections()
    database = FakeDatabase()
    reuse.use(database)
    thread = Thread(target=connection_reuse.release)
    thread.start()
    thread.join()
    assert database.closed == 1


def test_refresh_closes_an_old_or_dead_connection_before_a_hint(reuse, monkeypatch):
    """An idle consumer's stale connection never serves the next message."""
    drop = Mock()
    monkeypatch.setattr(runtime_process, "drop_unusable", drop)
    database = FakeDatabase()
    reuse.use(database)
    connection_reuse.refresh()
    assert not drop.called  # Not enabled: close_all() already ran after each hint.
    connection_reuse.keep_connections()
    connection_reuse.release()
    connection_reuse.refresh()
    assert database.closed == 0 and drop.call_args.args == (
        connection_reuse.connections,
    )
    reuse.clock[0] += connection_reuse.MAX_AGE_SECONDS
    connection_reuse.refresh()
    assert database.closed == 1


@pytest.mark.parametrize("fails", [False, True])
def test_broker_releases_after_a_hint_and_closes_after_a_failure(monkeypatch, fails):
    """A failed hint drops every connection; a finished one only releases."""
    calls = []

    def consume(args, kwargs, **_):
        """Record the order of the hint and its connection handling."""
        calls.append("hint")
        if fails:
            raise RuntimeError("synthetic")

    close_all = Mock(side_effect=lambda: calls.append("close_all"))
    monkeypatch.setattr(broker_module, "consume_hint", consume)
    monkeypatch.setattr(broker_module, "emit_failure", Mock())
    monkeypatch.setattr(broker_module, "record_sql_timeout", Mock())
    monkeypatch.setattr("django.db.connections.close_all", close_all)
    monkeypatch.setattr(connection_reuse, "refresh", lambda: calls.append("refresh"))
    monkeypatch.setattr(connection_reuse, "release", lambda: calls.append("release"))
    runtime = broker(broker_module.ServiceRole.MAIL_DISPATCH)
    runtime.app.tasks[HINT_TASK].run(str(uuid4()))
    assert calls == ["refresh", "hint", "close_all" if fails else "release"]
