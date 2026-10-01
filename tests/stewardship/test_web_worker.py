"""A stopping web worker drops idle keep-alive connections promptly (#374)."""

import os
import selectors
import signal
import socket
from threading import Thread
from time import monotonic
from unittest.mock import patch

import pytest

pytest.importorskip("gunicorn")

from gunicorn.config import Config  # noqa: E402
from gunicorn.glogging import Logger  # noqa: E402
from gunicorn.workers.gthread import ThreadWorker  # noqa: E402

from parishkit.stewardship import web_worker  # noqa: E402
from parishkit.stewardship.web_worker import DrainingThreadWorker  # noqa: E402

# Far longer than the test may take: a drain that waited for it would fail.
GRACEFUL_SECONDS = 60


def application(environ, start_response):
    """A tiny response with a length, so the connection may stay open."""
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "2")])
    return [b"ok"]


def started_worker(listener):
    """Run a real worker loop in a thread, as a forked worker would run it."""
    config = Config()
    for name, value in {
        "threads": 2,
        "keepalive": 1,
        "graceful_timeout": GRACEFUL_SECONDS,
        "accesslog": None,
    }.items():
        config.set(name, value)
    worker = DrainingThreadWorker(
        0, os.getppid(), [listener], None, 30, config, Logger(config)
    )
    # ThreadWorker.init_process without the process-wide parts of the base
    # class (signals, user switching, application loading).
    worker.tpool = worker.get_thread_pool()
    worker.poller = selectors.DefaultSelector()
    worker.method_queue.init()
    worker.wsgi = application
    thread = Thread(target=worker.run, daemon=True)
    thread.start()
    return worker, thread


def test_stop_does_not_wait_for_the_proxy_to_close_an_idle_connection():
    """An idle keep-alive connection is closed after keepalive, not the grace."""
    listener = socket.create_server(("127.0.0.1", 0))
    worker, thread = started_worker(listener)
    client = socket.create_connection(listener.getsockname(), timeout=10)
    try:
        client.sendall(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")
        response = b""
        while not response.endswith(b"ok"):
            chunk = client.recv(4096)
            assert chunk, response
            response += chunk
        # Like Caddy's pooled upstream connection: open and idle.
        stopped = monotonic()
        worker.handle_exit(signal.SIGTERM, None)
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert monotonic() - stopped < 10
        # The worker closed the connection; the client sees end of stream.
        assert client.recv(4096) == b""
    finally:
        client.close()
        # A worker still running on failure keeps its heartbeat file.
        if not thread.is_alive():
            worker.tmp.close()


@pytest.mark.parametrize(
    ("requested", "waited"),
    [(0.25, 0.25), (1.0, 1.0), (GRACEFUL_SECONDS, web_worker.REAP_SECONDS)],
    ids=["short", "serving", "draining"],
)
def test_each_wait_is_capped_so_idle_connections_are_reaped(requested, waited):
    """The serving loop is unchanged; only a long drain wait is split up."""
    worker = DrainingThreadWorker.__new__(DrainingThreadWorker)
    with patch.object(ThreadWorker, "wait_for_and_dispatch_events") as wait:
        worker.wait_for_and_dispatch_events(requested)
    wait.assert_called_once_with(waited)
