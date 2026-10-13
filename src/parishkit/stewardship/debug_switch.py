"""Honor System health's debug-off switch in every process (ADM-13, #530).

Debug logging is on in a process started with ``PARISHKIT_DEBUG_LOGGING=1``
(``observability.debug_logging_enabled``). Its lines keep free text and error
details that can hold personal data, and the setting lives in the server's
environment, so before this switch only recreating the services turned it
off. A set switch (``jobs.debug_switch_models.DebugOffSwitch``) turns debug
logging off in each process that reads it:

- the process takes the setting out of its own environment and keeps its
  value, so ``debug_logging_enabled()`` reads false everywhere: the log
  formatter drops message text and tracebacks again, and every helper
  subprocess started afterwards inherits no setting;
- the root log handlers that had a DEBUG threshold go back to INFO;
- it logs one WARNING ``debug_logging_switched_off`` line.

When the switch is cleared, a process that the switch turned off puts the
setting back, restores those thresholds and logs the WARNING
``debug_logging_enabled`` line again. A process started without the setting
is never affected, and does no work here at all: the switch can only turn
debug logging off.

When a process reads it: on every new database connection (Django's
``connection_created``, which covers each service's startup admission,
before ``startup_validated``, and every one-shot command when it connects),
and again before each service status record (about every 60 seconds,
``service_status``). A read that fails, or that this login may not make
(the download login, or a database before migration 0078), changes nothing;
the next connection or status record reads again. Lines written before a
process first reads the switch are not covered.

A child process started while the switch holds the setting off (mail
dispatch's second consumer, for example) starts without it, so it stays off
after a clear until its service is recreated.

The connection hook is registered by the jobs app's ``ready()``
(``install``), so every process that sets Django up has it.
"""

import logging
import os
from threading import Lock

from django.db.backends.signals import connection_created

from .observability import DEBUG_LOGGING_VARIABLE, Event, emit

# Every runtime login may EXECUTE the read (runtime_grants.DEBUG_OFF_FUNCTION).
# The backup login's admission refuses any definer grant, but its
# pg_read_all_data lets it read the table itself.
READ_FUNCTION = "public.stewardship_debug_logging_off_v1()"
TABLE = "public.stewardship_debug_off_switch"

_lock = Lock()
# One read-and-apply at a time (see ``refresh``).
_refreshing = Lock()
# The setting's value while the switch holds it off; None otherwise.
_held = None
# The root log handlers whose DEBUG threshold the switch raised to INFO, and
# every root handler present when it did.
_raised = []
_present = []


def watching():
    """Whether this process has debug logging the switch could turn off or back on.

    False for a process started without the setting, so it never reads the
    switch.
    """
    return _held is not None or os.environ.get(DEBUG_LOGGING_VARIABLE) == "1"


def read_switch(database):
    """Whether the switch is set, or None when this login cannot tell.

    One catalog query first says whether the read function exists and this
    login may run it, or else whether it may read the table; neither is
    true for the download login, or before migration 0078 is applied.
    """
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege(f,'EXECUTE'), "
            "has_table_privilege(t,'SELECT') "
            "FROM (SELECT to_regprocedure(%s) f, to_regclass(%s) t) x",
            [READ_FUNCTION, TABLE],
        )
        function, table = cursor.fetchone()
        if function:
            cursor.execute(f"SELECT {READ_FUNCTION}")
        elif table:
            cursor.execute(
                f"SELECT EXISTS(SELECT 1 FROM {TABLE} WHERE cleared_at IS NULL)"
            )
        else:
            return None
        return cursor.fetchone()[0]


def _thresholds(level):
    """Move the root handlers' DEBUG thresholds to ``level`` and back.

    Going to INFO remembers each handler it raised and every handler then
    present. Going back to DEBUG lowers, among the current root handlers,
    the ones it raised and any added since at INFO: logging configured again
    while the switch held the setting off (``configure_logging``) built its
    handlers at INFO only because the setting was out of the environment,
    and they would have been at DEBUG with it. A handler that was present
    and not at DEBUG is never touched, and a raised one since removed is
    ignored.
    """
    root = logging.getLogger()
    if level == logging.INFO:
        _present[:] = root.handlers
        for handler in root.handlers:
            if handler.level == logging.DEBUG:
                handler.setLevel(logging.INFO)
                _raised.append(handler)
    else:
        for handler in root.handlers:
            if handler in _raised or (
                handler not in _present and handler.level == logging.INFO
            ):
                handler.setLevel(logging.DEBUG)
        _raised.clear()
        _present.clear()


def apply(off):
    """Turn this process's debug logging off for a set switch, or back on.

    Off only affects a process whose setting is on; back on only affects one
    the switch turned off. Either change happens once and logs one WARNING.
    """
    global _held
    with _lock:
        if off and _held is None and os.environ.get(DEBUG_LOGGING_VARIABLE) == "1":
            # Changing os.environ while other threads run is a tradeoff: a
            # thread starting a helper at this moment may copy the
            # environment just before or just after the change (CPython's
            # dict operations are atomic, so never a torn copy). Either is
            # acceptable, since the switch is not a sharp boundary: lines
            # before it is applied are not covered either. In exchange,
            # every reader of the setting and every helper honors it with
            # no change of its own.
            _held = os.environ.pop(DEBUG_LOGGING_VARIABLE)
            _thresholds(logging.INFO)
            emit(Event.DEBUG_LOGGING_SWITCHED_OFF, level=logging.WARNING)
        elif not off and _held is not None:
            os.environ[DEBUG_LOGGING_VARIABLE] = _held
            _held = None
            _thresholds(logging.DEBUG)
            emit(Event.DEBUG_LOGGING_ENABLED, level=logging.WARNING)


def refresh(database):
    """Read the switch on ``database`` and apply it; never raise.

    It does nothing in a process started without debug logging. It must run
    outside a transaction (on a new connection, or between a service's
    units of work), so a refused read cannot abort anyone's work.

    Reading and applying happen under one lock, so an older read can never
    be applied after a newer one (a stale "not set" undoing a fresh "set").
    A refresh that finds another running skips instead of waiting: the
    running one reads at least as new a state, and the next connection or
    status record reads again. That also covers the read opening a new
    connection on this thread, whose hook would otherwise wait on itself.
    """
    if not watching() or not _refreshing.acquire(blocking=False):
        return
    try:
        try:
            off = read_switch(database)
        except Exception:
            # Read again on the next connection or status record.
            return
        if off is not None:
            apply(off)
    finally:
        _refreshing.release()


def _connected(sender, connection, **kwargs):
    """Read the switch on each new PostgreSQL connection (see the module text)."""
    if connection.vendor == "postgresql":
        refresh(connection)


def install():
    """Register the connection hook once (``jobs.apps.JobsConfig.ready``)."""
    connection_created.connect(_connected, dispatch_uid="stewardship-debug-off-switch")
