"""Unattended authentication observations owned by admitted web worker children."""

from secrets import randbelow
from threading import Event, Thread

from .observability import Event as LogEvent
from .observability import emit_failure

INTERVAL_SECONDS = 30
JOIN_SECONDS = 2


def observe_once(limiter):
    """Use a dedicated bounded SQL connection, never a request's transaction.

    The real probe owns its observation timestamp, counter continuity and
    incident fences. This wrapper does not manufacture healthy evidence or
    insert attempts. Session limits affect only this observer thread; closing
    every pass releases its explicitly reserved auxiliary connection.
    """
    from django.db import connection, connections

    try:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout='2s'")
            cursor.execute("SET lock_timeout='1s'")
            cursor.execute("SET transaction_timeout='10s'")
        limiter.observe_health()
    finally:
        connections.close_all()


class PeriodicAuthenticationHealth:
    """One daemon per child, never an accumulating pool of timed-out probes.

    Normal SQL and Valkey operations have finite timeouts. If a dependency still
    hangs outside those controls, no replacement thread is spawned, stale proof
    expires through the existing gap policy, and process exit has a bounded join.
    Readiness/metrics HTTP calls neither start this observer nor mutate episodes.
    """

    def __init__(self, limiter, *, check, active, retire, status=None):
        """Retain the admitted limiter and child-specific lifecycle callbacks.

        ``status`` (a ``service_status.ServiceStatusReporter``) records this
        web worker's service status when the thread starts and after each
        pass, on this thread's own short connection (ADM-13).
        """
        self.limiter, self.check = limiter, check
        self.active, self.retire = active, retire
        self.status = status
        self.stop = Event()
        self.thread = Thread(
            target=self.run, name="stewardship-auth-health", daemon=True
        )

    def start(self):
        """Start once, only after worker admission and credential receipts."""
        self.thread.start()

    def close(self):
        """Wake an idle observer without extending worker shutdown indefinitely."""
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(JOIN_SECONDS)

    def report_status(self):
        """Write the service status record when due, then close the connection.

        The reporter logs and absorbs its own failures; the record is display
        only and never affects the health observation.
        """
        if self.status is None:
            return
        from django.db import connections

        try:
            self.status.report(connect=True)
        finally:
            connections.close_all()

    def run(self):
        """Sample every thirty seconds; failures never become healthy silence."""
        # This worker's status record first, so it shows as started at once.
        self.report_status()
        # Stagger children that start together; proof still uses actual SQL
        # observations, not the random phase or elapsed wall time. Even the
        # latest first pass starts within the ninety-second continuity budget.
        if self.stop.wait(INTERVAL_SECONDS * randbelow(1001) / 1000):
            return
        # Startup already made a real probe, so subsequent ticks wait first.
        while not self.stop.wait(INTERVAL_SECONDS):
            if not self.active():
                break
            try:
                self.check()
            except Exception as error:
                emit_failure(error, event=LogEvent.AUTH_HEALTH_FAILED)
                self.retire()
                break
            try:
                observe_once(self.limiter)
            except Exception as error:
                # The limiter owns durable outage intents. This noncritical
                # diagnostic cannot recursively create provider notifications.
                emit_failure(error, event=LogEvent.AUTH_HEALTH_FAILED)
            self.report_status()
