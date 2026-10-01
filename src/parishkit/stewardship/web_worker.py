"""Gunicorn's threaded worker, made to drop idle keep-alive connections on stop.

Gunicorn's ``gthread`` worker closes an idle keep-alive connection once its
``keepalive`` seconds pass, but only after its event wait returns. While it
serves, that wait is at most one second. While it drains after SIGTERM, the
wait is the whole remaining ``graceful_timeout``, so an idle connection is not
reaped until some event arrives. Caddy keeps idle upstream connections for two
minutes by default. One that a request left open just before a deploy's stop
therefore held the web container for 120 s instead of under a second (#374).

This module imports only Gunicorn, so the supervisor (which never loads
Django) can resolve the worker class by name.
"""

from gunicorn.workers.gthread import ThreadWorker

# The longest any one event wait may block, serving or draining, before idle
# keep-alive and pending connections are checked against their deadlines.
# Gunicorn's own serving loop already waits one second at a time.
REAP_SECONDS = 1.0


class DrainingThreadWorker(ThreadWorker):
    """A ``gthread`` worker whose drain still expires idle connections."""

    def wait_for_and_dispatch_events(self, timeout):
        """Wait no longer than REAP_SECONDS so the caller can reap after it."""
        super().wait_for_and_dispatch_events(min(timeout, REAP_SECONDS))
