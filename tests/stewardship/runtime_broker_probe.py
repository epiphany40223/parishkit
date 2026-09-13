"""Synthetic transport/controller probe; no SQL, provider or deployment authority.

The parent test provisions a disposable Valkey server and supplies its loopback
port over stdin. Domain dispatch is deliberately replaced by one exact UUID
assertion; separate PostgreSQL tests exercise actual authorization and claims.
"""

import json
import os
import signal
import sys
import traceback
from threading import Event, Timer


class SyntheticLease:
    """Only this probe substitutes a lease; production must admit real mounts."""

    def check(self):
        """There is no deployment to mutate or offline authority to claim here."""


def main():
    """Run a real worker to completion or deliver SIGTERM after real queue startup."""
    import django

    os.environ["DJANGO_SETTINGS_MODULE"] = "parishkit.stewardship.settings.test"
    django.setup()
    from celery.worker import state
    from celery.worker import worker as celery_worker

    from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
    from parishkit.stewardship.jobs import broker
    from parishkit.stewardship.jobs.processes import serve_consumer
    from parishkit.stewardship.observability import configure_logging

    configure_logging()

    def diagnostic(message, *args, **kwargs):
        """Expose only test exception types and code locations, never values."""
        error = sys.exception()
        if error is not None:
            print(
                json.dumps(
                    {
                        "error": type(error).__name__,
                        "frames": [
                            [frame.name, frame.lineno]
                            for frame in traceback.extract_tb(error.__traceback__)
                        ],
                    }
                ),
                file=sys.stderr,
            )

    celery_worker.logger.critical = diagnostic
    payload = json.loads(sys.stdin.read())
    assert set(payload) == {"port", "expected", "mode"}
    stop, seen, ready, timers = Event(), [], [], []

    def consume(args, kwargs, *, service, handlers, stop):
        """No broker metadata may substitute any identity or additional arguments."""
        assert service is ServiceRole.WORKER and not kwargs
        assert args == (payload["expected"],) or args == [payload["expected"]]
        seen.append(args[0])
        stop.set()
        state.should_stop = 0

    def heartbeat():
        """Exercise the installed real signal handler only after queue admission."""
        ready.append(True)
        if payload["mode"] == "idle" and len(ready) == 1:
            timer = Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGTERM))
            timers.append(timer)
            timer.start()

    broker.consume_hint = consume
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("127.0.0.1", payload["port"], 0, None),
        password="synthetic-worker-password",
        service=ServiceRole.WORKER,
        handlers={},
        stop=stop,
    )
    result = serve_consumer(
        runtime, lease=SyntheticLease(), stop=stop, heartbeat=heartbeat
    )
    for timer in timers:
        timer.join(timeout=2)
    print(json.dumps({"status": result, "ready": bool(ready), "seen": seen}))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
