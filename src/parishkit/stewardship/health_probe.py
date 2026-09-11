"""Bounded single-flight observations keep monitoring off request worker threads."""

from threading import Lock, Thread
from time import monotonic


class HealthProbe:
    """Cache one observation; even a permanently hung probe owns at most one thread.

    Only the caller starting a refresh waits, for at most the explicit deadline.
    Other callers receive the last fresh observation or an unavailable result.
    This bounds response latency even when filesystem or network IO cannot be
    cancelled. Results never authorize application work or replace its admission.
    """

    def __init__(self, *, timeout=2, ttl=3):
        self.timeout, self.ttl = timeout, ttl
        self.lock, self.running = Lock(), False
        self.value, self.completed = None, float("-inf")

    def read(self, probe, *, unavailable):
        """Return a recent value or start just one bounded-wait refresh."""
        with self.lock:
            if monotonic() - self.completed < self.ttl:
                return self.value
            if self.running:
                return unavailable
            self.running = True
        thread = Thread(target=self._run, args=(probe, unavailable), daemon=True)
        try:
            thread.start()
        except Exception:
            with self.lock:
                self.running = False
            return unavailable
        thread.join(self.timeout)
        with self.lock:
            return self.value if not self.running else unavailable

    def cached(self, *, unavailable=None):
        """Read only fresh evidence without starting or waiting for another probe."""
        with self.lock:
            return (
                self.value if monotonic() - self.completed < self.ttl else unavailable
            )

    def _run(self, probe, unavailable):
        """Do not retain exception objects or private values in failed observations."""
        try:
            value = probe()
        except Exception:
            value = unavailable
        with self.lock:
            self.value, self.completed, self.running = value, monotonic(), False
