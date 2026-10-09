"""The web liveness probe and its scheduler producer, without a database (#392 L1)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.jobs import web_health
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.web_health import (
    ProbeResult,
    WebHealthProducer,
    probe_one,
    probe_replicas,
    replica_hosts,
)
from parishkit.stewardship.observability import FailureKind


class Server:
    """A loopback HTTP server whose answer each test chooses."""

    def __init__(self, status=200, body=b"ok\n", delay=0.0, location=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the http.server interface
                outer.requests.append((self.path, self.headers.get("Host")))
                if delay:
                    threading.Event().wait(delay)
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                """Keep test output quiet."""

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        """Stop serving and release the port."""
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    """Start a server on a free port and point the probe at it."""
    started = []

    def start(**kwargs):
        instance = Server(**kwargs)
        started.append(instance)
        monkeypatch.setattr(web_health, "PORT", instance.port)
        return instance

    yield start
    for instance in started:
        instance.close()


def test_replica_hosts_follow_the_topology_names():
    """Replica 0 is "web"; later ones are "web-N", as runtime_topology names them."""
    assert replica_hosts(1) == ("web",)
    assert replica_hosts(3) == ("web", "web-1", "web-2")
    for bad in (0, 9, True, "1"):
        with pytest.raises(ValueError):
            replica_hosts(bad)


def test_a_live_replica_passes_and_the_request_is_internal(server):
    """GET /health/live with the Host web's ALLOWED_HOSTS always admits."""
    instance = server()
    result = probe_one("127.0.0.1")
    assert result == ProbeResult() and result.healthy
    assert instance.requests == [("/health/live", "127.0.0.1")]
    assert result.context() == {}


@pytest.mark.parametrize(
    "answer, status",
    [
        ({"status": 503, "body": b"unavailable\n"}, 503),
        ({"status": 404, "body": b"Not Found\n"}, 404),
        ({"status": 200, "body": b"nope\n"}, 200),
        ({"status": 302, "body": b"", "location": "http://example.invalid/"}, 302),
    ],
)
def test_a_wrong_answer_fails_with_its_status(server, answer, status):
    """Any answer but 200 "ok" fails; a redirect is never followed."""
    server(**answer)
    result = probe_one("127.0.0.1")
    assert (result.failure_kind, result.status, result.timed_out) == (
        FailureKind.WEB_BAD_RESPONSE,
        status,
        False,
    )
    assert result.context() == {
        "failure": "web_unresponsive",
        "failure_kind": "web_bad_response",
        "status": status,
    }


def test_a_slow_replica_times_out_with_its_limit_and_elapsed_time(server):
    """A socket timeout is a failure that carries what the timeout rule records."""
    server(delay=1.0)
    result = probe_one("127.0.0.1", timeout=0.2)
    assert result.failure_kind is FailureKind.WEB_PROBE_TIMEOUT and result.timed_out
    assert result.limit == 0.2 and 0.15 <= result.elapsed < 1.0
    assert result.context() == sanitize(
        ContextKind.FAILURE,
        {"failure": "web_unresponsive", "failure_kind": FailureKind.WEB_PROBE_TIMEOUT},
    )


def test_an_unreachable_replica_fails(server, monkeypatch):
    """A refused connection or an unknown name is "unreachable", not a timeout."""
    import socket

    instance = server()
    instance.close()
    result = probe_one("127.0.0.1")
    assert result.failure_kind is FailureKind.WEB_UNREACHABLE
    assert not result.timed_out and result.status is None

    def unknown(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "unknown name")

    # An unknown name, without asking a real resolver.
    monkeypatch.setattr(socket, "getaddrinfo", unknown)
    assert probe_one("web").failure_kind is FailureKind.WEB_UNREACHABLE


def test_every_replica_must_pass(monkeypatch):
    """The minute fails with the first failing replica; later ones are skipped."""
    answers = {
        "web": ProbeResult(),
        "web-1": ProbeResult(FailureKind.WEB_UNREACHABLE),
        "web-2": ProbeResult(),
    }
    asked = []
    monkeypatch.setattr(
        web_health, "probe_one", lambda host: asked.append(host) or answers[host]
    )
    assert probe_replicas(("web", "web-1", "web-2")).failure_kind is (
        FailureKind.WEB_UNREACHABLE
    )
    assert asked == ["web", "web-1"]
    assert probe_replicas(("web",)).healthy


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.now = 600.0

    def __call__(self):
        return self.now


@pytest.fixture
def recorded(monkeypatch):
    """Capture what the producer would write instead of using a database."""
    results = []
    monkeypatch.setattr(web_health, "record_observation", results.append)
    return results


def guard():
    """A stand-in for the owned scheduler's guard."""
    return Mock(spec=SchedulerGuard)


def finish(producer):
    """Wait for the background probe thread to end."""
    producer.thread.join(5)
    assert not producer.thread.is_alive()


def test_producer_probes_once_a_minute_and_records_on_a_later_pass(recorded):
    """The loop never waits: a pass starts a probe; a later pass records it."""
    clock, calls = Clock(), []
    producer = WebHealthProducer(
        ("web",), probe=lambda hosts: calls.append(hosts) or ProbeResult(), clock=clock
    )
    owner = guard()
    assert producer(owner) == ()
    owner.check.assert_called_once_with()
    finish(producer)
    assert calls == [("web",)] and recorded == []
    clock.now += 5
    producer(owner)
    assert recorded == [ProbeResult()] and producer.thread is None
    # The same minute starts nothing more.
    producer(owner)
    assert calls == [("web",)] and len(recorded) == 1
    clock.now += 60
    producer(owner)
    finish(producer)
    producer(owner)
    assert calls == [("web",), ("web",)] and len(recorded) == 2


def test_producer_refuses_anything_but_the_owned_scheduler():
    """Only the scheduler, holding its session, may run the probe."""
    with pytest.raises(PermissionError):
        WebHealthProducer(("web",))(Mock())


def test_a_hung_probe_is_one_thread_recorded_as_a_timeout_each_minute(recorded):
    """No second thread starts; each minute past the limit records a timeout."""
    release, started = threading.Event(), []

    def hang(hosts):
        started.append(hosts)
        release.wait(10)
        return ProbeResult()

    clock = Clock()
    producer = WebHealthProducer(("web", "web-1"), probe=hang, clock=clock)
    assert producer.limit == 20
    owner = guard()
    producer(owner)
    clock.now += 15
    producer(owner)
    # Still within its limit and its minute: nothing recorded, no new thread.
    assert recorded == [] and len(started) == 1
    clock.now += 45
    producer(owner)
    (timeout,) = recorded
    assert timeout.failure_kind is FailureKind.WEB_PROBE_TIMEOUT
    assert timeout.timed_out and timeout.limit == 20 and timeout.elapsed == 60
    producer(owner)
    assert len(recorded) == 1
    clock.now += 60
    producer(owner)
    assert len(recorded) == 2 and recorded[1].elapsed == 120
    assert len(started) == 1
    # The late answer is discarded; the next minute probes afresh.
    release.set()
    finish(producer)
    producer(owner)
    assert len(recorded) == 2 and producer.thread is None
    clock.now += 60
    producer(owner)
    finish(producer)
    producer(owner)
    assert len(started) == 2 and recorded[2] == ProbeResult()


def test_no_probe_starts_within_30_seconds_of_the_last_result(recorded):
    """A late result defers the next minute's probe, so none is dropped."""
    clock, calls = Clock(), []
    producer = WebHealthProducer(
        ("web",), probe=lambda hosts: calls.append(hosts) or ProbeResult(), clock=clock
    )
    owner = guard()
    clock.now = 650.0  # minute 10, second 50
    producer(owner)
    finish(producer)
    clock.now = 655.0
    producer(owner)
    assert len(recorded) == 1
    clock.now = 661.0  # minute 11, but only 6 s after that result
    producer(owner)
    assert len(calls) == 1 and producer.thread is None
    clock.now = 685.0  # 30 s after the result: this minute's probe starts
    producer(owner)
    finish(producer)
    producer(owner)
    assert len(calls) == 2 and len(recorded) == 2


def test_timing_constants_match_the_trigger():
    """The producer's spacing and STALE are the trigger's 30 and 150 seconds."""
    from pathlib import Path

    text = (
        Path(web_health.__file__).parents[1] / "schema/migrations/0019_web_health.sql"
    ).read_text(encoding="utf-8")
    start = text.index("CREATE FUNCTION public.stewardship_web_health_v1(")
    body = text[start : text.index("END $$;", start)]
    assert f"interval '{web_health.RESULT_SPACING_SECONDS} seconds'" in body
    assert f"interval '{int(web_health.STALE.total_seconds())} seconds'" in body
    assert web_health.THREAD_SECONDS_CAP < 60


def test_a_probe_that_raises_is_an_unreachable_minute(recorded):
    """An unexpected error in the thread is a failure, never a lost minute."""

    def broken(hosts):
        raise RuntimeError("synthetic")

    clock = Clock()
    producer = WebHealthProducer(("web",), probe=broken, clock=clock)
    producer(guard())
    finish(producer)
    producer(guard())
    assert recorded == [ProbeResult(FailureKind.WEB_UNREACHABLE)]


def test_the_trigger_writes_the_contract_keys_and_never_an_empty_context():
    """The SQL producer (frozen 0019) carries what log_contract requires.

    test_log_contract scans the fresh-install files only, and this trigger
    exists only in its migration, so it is checked here.
    """
    from pathlib import Path

    from parishkit.stewardship.audit.log_contract import REQUIRED
    from parishkit.stewardship.observability import Event

    text = (
        Path(web_health.__file__).parents[1] / "schema/migrations/0019_web_health.sql"
    ).read_text(encoding="utf-8")
    start = text.index("CREATE FUNCTION public.stewardship_web_health_v1(")
    body = text[start : text.index("END $$;", start)]
    for key in REQUIRED[Event.WEB_UNHEALTHY]:
        assert f"'{key}'" in body, key
    assert f"current_setting('{web_health.CONTEXT_SETTING}',true)" in body
    assert (
        f"jsonb_build_object('failure','{web_health.FAILURE}',"
        "'failure_kind','unexpected_failure')"
    ) in body
    assert f"NEW.failures>={web_health.FAILED_MINUTES}" in body
    assert "'{}'" not in body


def test_a_repeated_overrun_keeps_the_30_second_spacing(recorded):
    """With a long limit, a hung probe's next timeout waits for the spacing too."""
    release = threading.Event()

    def hang(hosts):
        release.wait(10)
        return ProbeResult()

    clock = Clock()
    producer = WebHealthProducer(
        ("web", "web-1", "web-2", "web-3", "web-4"), probe=hang, clock=clock
    )
    assert producer.limit == 50
    owner = guard()
    clock.now = 600.0
    producer(owner)  # started at minute 10, second 0
    clock.now = 660.0
    producer(owner)  # minute 11: 60 s >= 50 s, the first timeout
    assert len(recorded) == 1
    clock.now = 720.0
    producer(owner)  # minute 12: 60 s after it, a second timeout
    assert len(recorded) == 2
    # A result recorded late in a minute defers the next one past 30 s.
    producer.recorded = 775.0
    clock.now = 781.0
    producer(owner)  # minute 13 but 6 s after the last result: wait
    assert len(recorded) == 2
    clock.now = 806.0
    producer(owner)
    assert len(recorded) == 3
    release.set()
    finish(producer)
