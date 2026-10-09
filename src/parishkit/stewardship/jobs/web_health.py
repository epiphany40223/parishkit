"""Alert when web stops answering its liveness check, and record its recovery.

Docker restarts a container only when its process exits. A web server that
is still running but no longer answers (#392 L1) is marked unhealthy by its
container health check and nothing else happens. The application cannot
restart a container (it has no Docker socket, by design), so this module
makes the condition visible: the Admin critical-events banner, the alert
email and Slack, through the ordinary operational incident path.

How it works:

- The owned scheduler starts at most one probe a minute, in a background
  thread (``WebHealthProducer``), so the scheduling loop never waits on HTTP.
  The probe asks each web replica's ``/health/live`` (``probe_replicas``)
  over the backend network. The scheduler joins only that network, so the
  ``web`` name always resolves to the address web's internal-request rule
  admits.
- On a later pass the loop writes the finished result to the singleton
  ``stewardship_web_health`` row, in one short transaction. Its SQL trigger
  counts consecutive failed minutes and, from the ``FAILED_MINUTES``-th on,
  writes one CRITICAL ``web_unhealthy`` entry per failed minute. The
  operational collector maps that entry to the ``web_unhealthy`` incident.
- A probe that times out, or a probe thread still running when the next
  minute starts, also records a ``task_timed_out`` entry naming
  ``web_probe``, its limit and the elapsed time.
- An incident can open and close repeatedly if web keeps failing and
  recovering (flapping); each opening alerts again. There is no cooldown:
  add one only if the Administrator asks.
- While the incident is open, the collector checks the row each minute
  (``observe_web_health``) and resolves the incident after
  ``RECOVERY_WINDOW`` of passing probes, unless a failure entry is newer or
  still waiting for intake.
"""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from threading import Lock, Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.db import connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.audit.timeouts import record_timeout
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.observability import Event, FailureKind, emit_failure

from .operational_content import IncidentKind
from .operational_models import OperationalIncident, OperationalLogReceipt
from .operational_storage import record_recovery
from .ownership import database_now
from .scheduler import SchedulerGuard
from .web_health_models import WebHealth

# The web container's port and liveness path (probe.LIVENESS_URL, on its
# own loopback). The Host header is one ALLOWED_HOSTS always admits; the
# request still has to come from the backend network to be answered.
PORT = 8000
PATH = "/health/live"
HOST_HEADER = "127.0.0.1"
# Each request's socket timeout, as the container health check uses.
PROBE_SECONDS = 3
# A probe thread is checked again only when the next minute starts. If it is
# still running then, and has run at least this long per replica (capped
# below a minute), that minute fails as timed out: a resolver hang the socket
# timeout does not cover. A hung thread is cleared by restarting the
# scheduler, not web, since it lives in the scheduler process.
THREAD_SECONDS_PER_REPLICA = 10
THREAD_SECONDS_CAP = 50
# The trigger drops a result within this many seconds of the previous one
# (a retried pass), so the producer never records two results that close: it
# waits to start a minute's probe until this long after the last result.
RESULT_SPACING_SECONDS = 30
# Failed minutes in a row before the CRITICAL entry; the SQL trigger
# (stewardship_web_health_v1) uses the same number.
FAILED_MINUTES = 3
# Passing probes needed before the collector resolves the incident.
RECOVERY_WINDOW = timedelta(minutes=5)
# A row older than this is not current evidence of recovery, and a result
# this long after the previous one starts a new run in the trigger. Probes
# run once a minute; this allows one slow or missed pass.
STALE = timedelta(seconds=150)
# The transaction-local setting the trigger reads the failure's details from.
CONTEXT_SETTING = "parishkit.web_health_context"
# The ``failure`` word of the CRITICAL entry (audit.schemas.FAILURES).
FAILURE = "web_unresponsive"


def replica_hosts(replicas):
    """The Compose service names of the web replicas (runtime_topology)."""
    if type(replicas) is not int or not 1 <= replicas <= 8:
        raise ValueError("Unsupported web replica count.")
    return tuple("web" if index == 0 else f"web-{index}" for index in range(replicas))


@dataclass(frozen=True)
class ProbeResult:
    """One minute's verdict over every replica.

    ``failure_kind`` says how the first failing replica failed (None when
    all passed); ``status`` is its HTTP status when it answered with the
    wrong one. ``timed_out`` marks a socket timeout or an overrunning probe
    thread, with ``limit`` and ``elapsed`` in seconds.
    """

    failure_kind: FailureKind | None = None
    status: int | None = None
    timed_out: bool = False
    limit: float = PROBE_SECONDS
    elapsed: float = 0.0

    @property
    def healthy(self):
        """Whether every replica answered "ok"."""
        return self.failure_kind is None

    def context(self):
        """The sanitized ``failure`` context the trigger copies, or {} when healthy."""
        if self.healthy:
            return {}
        values = {"failure": FAILURE, "failure_kind": self.failure_kind}
        if self.status is not None:
            values["status"] = self.status
        return sanitize(ContextKind.FAILURE, values)


class _NoRedirect(HTTPRedirectHandler):
    """A redirected response fails rather than following it anywhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse every redirect; urllib then raises the 3xx as an HTTPError."""
        return None


def _timed_out(error):
    """Whether a urllib failure was a socket timeout."""
    reason = getattr(error, "reason", error)
    return isinstance(error, TimeoutError) or isinstance(reason, TimeoutError)


def probe_one(host, *, timeout=PROBE_SECONDS):
    """Ask one replica's liveness endpoint; return a ProbeResult.

    No proxy, no redirects, a fixed Host header and a bounded read. A
    socket timeout, an unreachable or unresolvable host, and any answer
    other than 200 "ok" are failures, each with its own category.
    """
    request = Request(f"http://{host}:{PORT}{PATH}", headers={"Host": HOST_HEADER})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    started = monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            status, body = response.status, response.read(4)
    except HTTPError as error:
        error.close()
        return ProbeResult(FailureKind.WEB_BAD_RESPONSE, status=error.code)
    except (URLError, OSError, ValueError) as error:
        if _timed_out(error):
            return ProbeResult(
                FailureKind.WEB_PROBE_TIMEOUT,
                timed_out=True,
                limit=timeout,
                elapsed=monotonic() - started,
            )
        return ProbeResult(FailureKind.WEB_UNREACHABLE)
    if status != 200 or body != b"ok\n":
        return ProbeResult(FailureKind.WEB_BAD_RESPONSE, status=status)
    return ProbeResult()


def probe_replicas(hosts):
    """Probe each replica in turn; the minute fails with the first failure."""
    for host in hosts:
        result = probe_one(host)
        if not result.healthy:
            return result
    return ProbeResult()


def record_observation(result):
    """Write one minute's result to the health row, in its own transaction.

    A timeout is recorded first, on its own connection, so it survives even
    if this write fails. The trigger counts failures and writes the CRITICAL
    entry; the limits keep a blocked row lock from holding the loop.
    """
    if result.timed_out:
        record_timeout(
            Event.TASK_TIMED_OUT,
            what="web_probe",
            level="WARNING",
            limit_seconds=result.limit,
            elapsed_seconds=result.elapsed,
            bind_task=False,
        )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout='2s'")
        cursor.execute("SET LOCAL statement_timeout='5s'")
        cursor.execute(
            "SELECT set_config(%s,%s,true)",
            [CONTEXT_SETTING, json.dumps(result.context())],
        )
        cursor.execute(
            "INSERT INTO stewardship_web_health (singleton,healthy) "
            "VALUES (true,%s) ON CONFLICT (singleton) "
            "DO UPDATE SET healthy=EXCLUDED.healthy",
            [result.healthy],
        )


class WebHealthProducer:
    """Probe web once a minute off the scheduling loop, then record the result.

    Called on every scheduler pass. It never blocks: a pass records a
    finished probe's result, and starts the next probe when a new minute
    began, no probe is running and the last result is at least
    RESULT_SPACING_SECONDS old (otherwise later in that minute; a repeated
    timeout waits for the same spacing). At most one
    probe thread exists, even if one hangs. A probe still running when the
    next minute starts, at least its limit after it began, is recorded as a
    timed-out failure, again at each later minute until it ends, and its late
    result is then discarded. The minute is read from the monotonic clock,
    so a wall clock step neither skips nor repeats probes. Returns no task
    ids.
    """

    def __init__(self, hosts, *, probe=probe_replicas, clock=monotonic):
        self.hosts = tuple(hosts)
        self.probe, self.clock = probe, clock
        self.limit = min(
            THREAD_SECONDS_PER_REPLICA * len(self.hosts), THREAD_SECONDS_CAP
        )
        self.lock = Lock()
        self.thread = self.result = None
        self.started = self.minute = self.recorded = None
        self.overran = False

    def __call__(self, guard):
        """Record any finished result, then start this minute's probe."""
        if not isinstance(guard, SchedulerGuard):
            raise PermissionError("The web health probe requires the owned scheduler.")
        guard.check()
        now = self.clock()
        with self.lock:
            finished, self.result = self.result, None
        if finished is not None:
            self.thread = None
            if not self.overran:
                self._record(finished, now)
            self.overran = False
        minute = int(now // 60)
        if minute == self.minute:
            return ()
        if self.recorded is not None and now - self.recorded < RESULT_SPACING_SECONDS:
            # The last result was recorded late in its minute; wait, so the
            # trigger does not drop this one. That applies to a new probe and
            # to a still-running probe's repeated timeout alike: with several
            # replicas the first timeout can fall late in a minute.
            return ()
        if self.thread is not None:
            if now - self.started >= self.limit:
                self.minute, self.overran = minute, True
                self._record(
                    ProbeResult(
                        FailureKind.WEB_PROBE_TIMEOUT,
                        timed_out=True,
                        limit=self.limit,
                        elapsed=now - self.started,
                    ),
                    now,
                )
            return ()
        self.minute, self.started = minute, now
        self.thread = Thread(target=self._run, name="web-health-probe", daemon=True)
        self.thread.start()
        return ()

    def _record(self, result, now):
        """Write one result and remember when, for the spacing rule."""
        self.recorded = now
        record_observation(result)

    def _run(self):
        """Probe in the background thread; an unexpected error is a failure."""
        try:
            result = self.probe(self.hosts)
        except Exception as error:
            emit_failure(error, level=logging.WARNING)
            result = ProbeResult(FailureKind.WEB_UNREACHABLE)
        with self.lock:
            self.result = result


def needs_web_observation():
    """Whether a web_unhealthy incident is open and may need resolving."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.WEB_UNHEALTHY, resolved_at__isnull=True
    ).exists()


def observe_web_health():
    """Resolve the open incident after ``RECOVERY_WINDOW`` of passing probes.

    Runs in the operational collector under the work order. Only a current
    row whose passes began at least ``RECOVERY_WINDOW`` before its latest
    observation counts. A failure entry newer than that start, or one the
    collector has not taken in yet, keeps the incident open. The fence is
    on the entries' own times, not the incident's: the collector may take in
    the last failure after the passes began, which must not block recovery.
    """
    require_work_order()
    if not needs_web_observation():
        return
    sample = WebHealth.objects.first()
    instant = database_now()
    if (
        sample is None
        or not sample.healthy
        or sample.passing_since is None
        or not timedelta(0) <= instant - sample.observed_at <= STALE
        or sample.observed_at - sample.passing_since < RECOVERY_WINDOW
    ):
        return
    failures = OperationalLog.objects.filter(
        level="CRITICAL", event=Event.WEB_UNHEALTHY
    )
    if (
        failures.filter(created_at__gte=sample.passing_since).exists()
        or failures.exclude(
            pk__in=OperationalLogReceipt.objects.values("log_id")
        ).exists()
    ):
        return
    record_recovery(IncidentKind.WEB_UNHEALTHY)
