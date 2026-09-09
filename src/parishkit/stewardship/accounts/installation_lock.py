"""Connection-pinned, deployment-wide installer serialization across commits."""

from contextlib import contextmanager
from threading import get_ident, local

from django.db import connection

from parishkit.stewardship.storage import StorageInvariantError

INSTALLER_LOCK = (736212, 1)
_scope = local()


class ConfigurationBusy(RuntimeError):
    """Another installer owns the deployment; its caller may retry later."""


class InstallerLock:
    """Detect a reconnect/thread change rather than continuing without the lock."""

    def __init__(self, raw):
        self.raw = raw
        self.thread = get_ident()

    def check(self):
        """Every file/DB boundary requires the same live PostgreSQL connection."""
        if (
            get_ident() != self.thread
            or connection.connection is not self.raw
            or self.raw.closed
            or getattr(_scope, "lock", None) is not self
        ):
            raise StorageInvariantError("The configuration installer lock was lost.")


@contextmanager
def installation_lock():
    """Acquire without unbounded waiting; always release the exact owning session.

    Session advisory locks require a direct PostgreSQL connection, never a
    transaction-pooling proxy. No outer transaction may contain the workflow:
    each prepare/checkpoint/activation must really commit before file selection.
    """
    if (
        connection.vendor != "postgresql"
        or connection.in_atomic_block
        or not connection.get_autocommit()
        or getattr(_scope, "lock", None)
    ):
        raise StorageInvariantError("Installation requires its own PostgreSQL session.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", INSTALLER_LOCK)
        if not cursor.fetchone()[0]:
            raise ConfigurationBusy("Configuration installation is already running.")
    raw = connection.connection
    guard = InstallerLock(raw)
    _scope.lock = guard
    try:
        yield guard
    finally:
        _scope.lock = None
        # Never reconnect to unlock: that would leave the original lock behind.
        if not raw.closed:
            try:
                with raw.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s, %s)", INSTALLER_LOCK)
                    if not cursor.fetchone()[0]:
                        raise StorageInvariantError(
                            "The configuration installer lock was lost."
                        )
            except BaseException:
                raw.close()
                raise
