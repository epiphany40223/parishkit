"""The web master durably logs each worker it kills at a time limit (#374)."""

import json
import signal
import socket
import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

pytest.importorskip("gunicorn")

from parishkit.stewardship import web_supervisor  # noqa: E402
from parishkit.stewardship.observability import Event, FailureKind  # noqa: E402


@pytest.fixture
def written(monkeypatch):
    """Capture the durable entries instead of writing them to a database."""
    entries = []
    monkeypatch.setattr(
        web_supervisor, "_insert", lambda settings, context: entries.append(context)
    )
    return entries


@pytest.fixture
def emitted(monkeypatch):
    """Capture the process-log lines the recorder emits."""
    lines = []
    monkeypatch.setattr(
        web_supervisor, "emit", lambda event, **values: lines.append((event, values))
    )
    return lines


def test_the_entry_says_what_was_killed_the_limit_and_the_elapsed_time(
    written, emitted
):
    """The durable entry and the process log carry the same reviewed facts."""
    web_supervisor.record_web_kill(
        lambda: {}, what="web_drain", limit_seconds=345, elapsed_seconds=345.6, count=2
    )
    assert written == [
        {"what": "web_drain", "limit_seconds": 345, "elapsed_seconds": 346, "count": 2}
    ]
    assert emitted == [
        (
            Event.HELPER_TIMED_OUT,
            {
                "level": 40,
                "timeout": "web_drain",
                "limit_seconds": 345,
                "elapsed_seconds": 346,
            },
        )
    ]


@pytest.mark.parametrize(
    ("database", "kind"),
    [
        # Nothing listens on port 1: the connection is refused at once.
        (
            lambda: {
                "HOST": "127.0.0.1",
                "PORT": 1,
                "NAME": "none",
                "USER": "none",
                "PASSWORD": "private",
                "OPTIONS": {"sslmode": "disable"},
            },
            FailureKind.DATABASE,
        ),
        # An unreadable password file fails before any connection.
        (
            lambda: (_ for _ in ()).throw(OSError("unreadable")),
            FailureKind.UNEXPECTED,
        ),
    ],
    ids=["refused", "unreadable"],
)
def test_a_failed_write_never_raises_and_keeps_the_process_log(emitted, database, kind):
    """The facts are already in the process log; the failure is a category."""
    pytest.importorskip("psycopg")
    started = monotonic()
    web_supervisor.record_web_kill(
        database, what="web_drain", limit_seconds=345, elapsed_seconds=345
    )
    assert monotonic() - started < 5
    assert emitted[0][1]["timeout"] == "web_drain"
    assert emitted[1] == (Event.HELPER_TIMED_OUT, {"level": 40, "failure_kind": kind})
    assert "private" not in repr(emitted)


def test_an_unreviewed_kind_is_not_written(written, emitted):
    """The context is sanitized before the write, so free text never lands."""
    web_supervisor.record_web_kill(
        lambda: {}, what="web drain", limit_seconds=1, elapsed_seconds=1
    )
    assert written == []
    assert emitted[-1][1]["failure_kind"] is FailureKind.UNEXPECTED


def test_a_hung_write_holds_the_master_only_briefly(monkeypatch, emitted):
    """The kill goes ahead after RECORD_SECONDS even if the database hangs."""
    monkeypatch.setattr(web_supervisor, "_insert", lambda *args: sleep(30))
    started = monotonic()
    web_supervisor.record_within(
        0.2, lambda: {}, what="web_drain", limit_seconds=1, elapsed_seconds=1
    )
    assert monotonic() - started < 2


def bare_arbiter(**attributes):
    """A RecordingArbiter without Gunicorn's application setup."""
    arbiter = object.__new__(web_supervisor.RecordingArbiter)
    arbiter.stewardship_database = lambda: {}
    arbiter.stewardship_stop_began = None
    arbiter.WORKERS = {}
    arbiter._stats = {"workers_killed": 0}
    # Gunicorn's real reap would waitpid(-1) the test process's own children.
    arbiter.reap_workers = lambda: None
    for name, value in attributes.items():
        setattr(arbiter, name, value)
    return arbiter


def sleeper():
    """A real child process standing in for a web worker."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_a_drain_kill_is_recorded_before_the_workers_are_killed(written, emitted):
    """One entry for the stop: the grace, the time since the stop, the count."""
    children = [sleeper(), sleeper()]
    arbiter = bare_arbiter(
        cfg=SimpleNamespace(graceful_timeout=345),
        WORKERS={child.pid: SimpleNamespace() for child in children},
        stewardship_stop_began=monotonic() - 345.2,
    )
    try:
        # Only the stop's final SIGKILL is a kill at the limit (SIGWINCH is
        # ignored by default, so the children survive it).
        arbiter.kill_workers(signal.SIGWINCH)
        assert written == []
        arbiter.kill_workers(signal.SIGKILL)
        for child in children:
            assert child.wait(timeout=10) == -signal.SIGKILL
    finally:
        for child in children:
            child.kill()
            child.wait()
    assert written == [
        {"what": "web_drain", "limit_seconds": 345, "elapsed_seconds": 345, "count": 2}
    ]


def test_no_entry_when_every_worker_already_exited(written):
    """Workers that exited since the stop's last reap are not killed or logged."""
    arbiter = bare_arbiter(
        cfg=SimpleNamespace(graceful_timeout=345),
        stewardship_stop_began=monotonic() - 345,
        # A worker that exited in the last moments, not yet reaped by stop().
        WORKERS={2**22: SimpleNamespace()},
    )
    arbiter.reap_workers = arbiter.WORKERS.clear
    arbiter.kill_workers(signal.SIGKILL)
    assert written == []


def test_a_quick_shutdown_names_its_own_limit(written):
    """SIGINT or SIGQUIT cuts the stop to Gunicorn's 2 s; the entry says so."""
    child = sleeper()
    arbiter = bare_arbiter(
        cfg=SimpleNamespace(graceful_timeout=345),
        WORKERS={child.pid: SimpleNamespace()},
        stewardship_stop_began=monotonic() - 3.2,
    )
    try:
        arbiter.kill_workers(signal.SIGKILL)
        assert child.wait(timeout=10) == -signal.SIGKILL
    finally:
        child.kill()
        child.wait()
    assert written == [
        {"what": "web_drain", "limit_seconds": 2, "elapsed_seconds": 3, "count": 1}
    ]


def test_a_thread_that_cannot_start_still_kills_the_worker(monkeypatch, emitted):
    """Recording can never crash the master or skip the kill (#428 review)."""

    def refuse(self):
        """As when the host cannot create another thread."""
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(web_supervisor.Thread, "start", refuse)
    child = sleeper()
    worker = SimpleNamespace(tmp=SimpleNamespace(last_update=lambda: monotonic() - 371))
    arbiter = bare_arbiter(timeout=370, WORKERS={child.pid: worker})
    try:
        arbiter.kill_worker(child.pid, signal.SIGABRT)
        assert child.wait(timeout=10) == -signal.SIGABRT
    finally:
        child.kill()
        child.wait()
    assert emitted == [
        (
            Event.HELPER_TIMED_OUT,
            {"level": 40, "failure_kind": FailureKind.UNEXPECTED},
        )
    ]


def test_a_silent_worker_is_recorded_at_its_abort(written, emitted):
    """The heartbeat limit and how long the worker was silent, once."""
    child = sleeper()
    worker = SimpleNamespace(tmp=SimpleNamespace(last_update=lambda: monotonic() - 371))
    arbiter = bare_arbiter(timeout=370, WORKERS={child.pid: worker})
    try:
        arbiter.kill_worker(child.pid, signal.SIGABRT)
        assert child.wait(timeout=10) == -signal.SIGABRT
    finally:
        child.kill()
        child.wait()
    assert written == [
        {"what": "web_heartbeat", "limit_seconds": 370, "elapsed_seconds": 371}
    ]


# A real Gunicorn master, its real stop and a real worker still serving at
# the grace: proves the hook fires where Gunicorn kills (a Gunicorn upgrade
# that renamed kill_workers would make this fail).
MASTER = """
import json, sys, time
from gunicorn.app.base import BaseApplication
from parishkit.stewardship import web_supervisor

port, out, grace = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])

def record(settings, context):
    with open(out, "a") as stream:
        stream.write(json.dumps(context) + "\\n")

web_supervisor._insert = record

def app(environ, start_response):
    if environ["PATH_INFO"] == "/slow":
        time.sleep(60)
    start_response("200 OK", [("Content-Length", "2")])
    return [b"ok"]

class Application(BaseApplication):
    def load_config(self):
        for key, value in {
            "bind": [f"127.0.0.1:{port}"],
            "workers": 1,
            "threads": 2,
            "worker_class": "parishkit.stewardship.web_worker.DrainingThreadWorker",
            "graceful_timeout": grace,
            "keepalive": 1,
            "control_socket_disable": True,
            "accesslog": None,
        }.items():
            self.cfg.set(key, value)

    def load(self):
        return app

web_supervisor.RecordingArbiter(Application(), database=lambda: {}).run()
"""


def stopped_master(tmp_path, *, grace, path):
    """Run a real master, send one request, SIGTERM it; return the stop time.

    The request is sent on a keep-alive connection and left open, idle once
    it is answered (``/``) or still in flight (``/slow``).
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    out = tmp_path / "entries.jsonl"
    master = subprocess.Popen(
        [sys.executable, "-c", MASTER, str(port), str(out), str(grace)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = monotonic() + 20
        while True:
            try:
                client = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                assert monotonic() < deadline, "the master never listened"
                sleep(0.1)
        with client:
            client.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            # Let the worker take (or answer) the request before the stop.
            sleep(1)
            started = monotonic()
            master.send_signal(signal.SIGTERM)
            assert master.wait(timeout=grace + 15) == 0
            stopped = monotonic() - started
    finally:
        master.kill()
        master.wait()
    return stopped, out


def test_a_real_idle_stop_is_fast_and_writes_nothing(tmp_path):
    """A normal stop with an idle keep-alive connection: no kill, no entry."""
    stopped, out = stopped_master(tmp_path, grace=30, path="/")
    assert stopped < 10
    assert not out.exists()


def test_a_real_master_records_a_worker_still_serving_at_the_grace(tmp_path):
    """SIGTERM the master mid-request: the worker is killed and logged."""
    _, out = stopped_master(tmp_path, grace=1, path="/slow")
    entries = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["what"] == "web_drain"
    assert entries[0]["limit_seconds"] == 1
    assert entries[0]["count"] == 1
    assert 1 <= entries[0]["elapsed_seconds"] <= 3


def test_the_master_side_never_loads_django():
    """The supervisor resolves this module without loading the application."""
    probe = (
        "import sys, parishkit.stewardship.web_supervisor; "
        "print(sorted(name for name in sys.modules if name.startswith('django')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
