"""Operational process lifetimes retain a real shared offline-exclusion lease."""

import logging
import os
import signal
import subprocess
import sys
from contextlib import suppress
from threading import Event as StopEvent
from time import monotonic

from parishkit.config import ConfigError

from .consumer_runtime import (
    DIRECTORY,
    publish_supervisor_identity,
    publish_worker_receipts,
)
from .deployment import DeploymentProfile, ServiceRole, load_deployment
from .observability import (
    Event,
    configure_logging,
    emit_failure,
    emit_started,
    installer_request,
)
from .probe import MAX_AGE_SECONDS as PROBE_MAX_AGE
from .runtime_budget import STOP_MARGIN_SECONDS
from .runtime_paths import RuntimeLayout, private_directory
from .startup_interlock import StartupLease


def gunicorn_options(configuration):
    """Only validated finite topology values control workers, threads and drainage."""
    budget = configuration.runtime_budget
    return {
        "bind": ["0.0.0.0:8000"],
        "workers": budget.web_processes,
        "threads": budget.web_threads,
        # gthread, except that a stop drops idle keep-alive connections
        # after ``keepalive`` seconds instead of waiting for the proxy to
        # close them (#374; see web_worker).
        "worker_class": "parishkit.stewardship.web_worker.DrainingThreadWorker",
        "preload_app": False,
        "reload": configuration.profile is DeploymentProfile.DEVELOPMENT,
        "reload_engine": "poll",
        "timeout": budget.server_timeout_seconds,
        # A stop gives in-flight requests the container's stop grace less a
        # margin, so the master kills, and durably logs, a worker still
        # serving before Docker kills the container (#374; web_supervisor).
        "graceful_timeout": budget.web_grace_seconds,
        "keepalive": 5,
        "worker_tmp_dir": "/tmp",
        # Gunicorn 25+ opens a runtime-management control socket under
        # $HOME/.gunicorn by default. The image's root filesystem is read-only
        # and nothing uses gunicornc, so the socket only produced a startup
        # error and would add an unneeded management surface.
        "control_socket_disable": True,
        # Gunicorn forcibly chmods its native pidfile to 0644. Our startup hook
        # instead records the master in the owner-only receipt directory.
        "on_starting": publish_supervisor_identity,
        "umask": 0o077,
        "post_worker_init": admitted_worker_started,
        "worker_exit": admitted_worker_exited,
        "accesslog": None,
        "errorlog": "-",
        "forwarded_allow_ips": "",  # The application admits its one exact proxy.
        "limit_request_line": 8190,
        "limit_request_fields": 64,
        "limit_request_field_size": 8190,
    }


def admitted_worker_started(worker):
    """Publish receipts, then start child-owned observations; keep failures private."""
    try:
        from django.conf import settings

        from .runtime_auth_health import PeriodicAuthenticationHealth
        from .service_status import ServiceStatusReporter

        publish_worker_receipts(worker)
        observer = PeriodicAuthenticationHealth(
            settings.STEWARDSHIP_AUTH_RUNTIME.limiter,
            check=settings.STEWARDSHIP_WEB_LEASE.check,
            active=lambda: worker.alive,
            retire=lambda: worker.handle_exit(signal.SIGTERM, None),
            # Each web worker's service status record (ADM-13).
            status=ServiceStatusReporter(ServiceRole.WEB.value),
        )
        worker.stewardship_auth_health = observer
        observer.start()
    except Exception:
        raise ConfigError(
            "Web worker receipts or periodic observation could not be started."
        ) from None


def admitted_worker_exited(server, worker):
    """The hook also runs in the master after a lost child; never join there."""
    if worker.pid != os.getpid():
        return
    observer = getattr(worker, "stewardship_auth_health", None)
    if observer is not None:
        try:
            observer.close()
        except Exception as error:
            emit_failure(error, event=Event.AUTH_HEALTH_FAILED)


def serve_web(configuration, lease):
    """Fork workers that admit settings before handling any HTTP request.

    The master never loads Django application modules. Development worker
    replacement therefore imports changed source afresh instead of inheriting
    stale preloaded code. Every child retains the lifecycle lease. The
    master's only dependency socket is the one short connection
    execute_runtime's startup wait opens and closes before this runs
    (runtime_database.await_database, #453).
    """
    from gunicorn.app.base import BaseApplication
    from gunicorn.errors import HaltServer
    from gunicorn.glogging import Logger

    from .runtime_database import database_settings
    from .web_supervisor import RecordingArbiter

    lease.check()
    private_directory(DIRECTORY, create=True)

    class PrivateLogger(Logger):
        """Gunicorn's own handler setup must not restore unredacted error logging."""

        def setup(self, cfg):
            """Retain Gunicorn internals but route their output through safe JSONL."""
            super().setup(cfg)
            configure_logging()

    class Application(BaseApplication):
        """Do not read arbitrary command-line, environment or Python config files."""

        def load_config(self):
            """Install only the closed options derived from admitted deployment."""
            for key, value in gunicorn_options(configuration).items():
                self.cfg.set(key, value)
            self.cfg.set("logger_class", PrivateLogger)

        def load(self):
            """Admit each worker before accepting requests or passing liveness."""
            return load_web_application(configuration, lease)

    # BaseApplication.run prints arbitrary RuntimeError messages; this boundary
    # instead lets execute_runtime suppress any private exception text.
    try:
        # The master logs a worker it kills at a limit on the web's own SQL
        # login, read only when needed (web_supervisor).
        RecordingArbiter(
            Application(), database=lambda: database_settings(configuration)
        ).run()
    except HaltServer:
        # HaltServer deliberately derives directly from BaseException. In a
        # multi-worker boot failure it can escape Gunicorn's own stop path.
        raise ConfigError("Web supervisor stopped before completing startup.") from None
    return 0


def load_web_application(configuration, lease):
    """Sanitize before Gunicorn's direct stderr print for worker-start exceptions."""
    try:
        from django.core.wsgi import get_wsgi_application

        from .runtime_web import configure_web

        lease.check()
        configure_web(configuration)
        from django.conf import settings

        settings.STEWARDSHIP_WEB_LEASE = lease
        application = get_wsgi_application()
        if configuration.profile is DeploymentProfile.DEVELOPMENT:
            from django.contrib.staticfiles.handlers import StaticFilesHandler

            application = StaticFilesHandler(application)
        emit_started()
        return application
    except Exception:
        raise ConfigError(
            "Web worker admission failed; use operator health diagnostics."
        ) from None


def next_configuration_request():
    """Select one resumable intent by its latest append-only checkpoint."""
    from django.db.models import OuterRef, Subquery

    from .accounts.request_models import (
        ConfigurationChangeRequest,
        ConfigurationRequestCheckpoint,
    )

    latest = ConfigurationRequestCheckpoint.objects.filter(
        request_id=OuterRef("pk")
    ).order_by("-sequence")
    return (
        ConfigurationChangeRequest.objects.annotate(
            current_state=Subquery(latest.values("state")[:1])
        )
        .filter(
            authority="admin",
            current_state__in=("staged", "validating", "prepared", "yaml_activated"),
        )
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)
        .first()
    )


# The longest gap between an idle installer's full passes (#639). The cheap
# ``pending`` read still runs every ``wait_seconds``, so work it can see starts
# at once; this bounds only work that it cannot see.
IDLE_PASS_SECONDS = 30


def bounded_loop(
    run_once,
    *,
    lease,
    stop,
    wait_seconds=2,
    heartbeat=None,
    pending=None,
    idle_seconds=IDLE_PASS_SECONDS,
    clock=monotonic,
):
    """Retry transient work without spinning; SIGTERM stops between bounded passes.

    A failed lease is fatal rather than a retryable dependency outage. Durable
    engines retain their own request checkpoints and retries.

    The loop wakes every ``wait_seconds`` (longer after a failure) and
    publishes its heartbeat on every wake, so container health never depends
    on how often the full pass runs. Without ``pending`` every wake runs
    ``run_once``. With it (#639), a wake first asks ``pending()``, one cheap
    read that takes no lock, and runs the full pass only when that finds work
    or when the idle gap has elapsed. The gap doubles after each full pass
    that reports no work (``run_once`` returning a false value), from
    ``wait_seconds`` up to ``idle_seconds``, and drops back to
    ``wait_seconds`` once a pass does work.

    The database connection stays open between passes, so an idle loop does
    not reconnect and repeat its full admission every few seconds. Each wake
    first drops a kept connection that no longer answers (after a PostgreSQL
    restart, say), so the wake reconnects quietly instead of logging a
    failure. Any failure closes it too, so the next wake reconnects, and a
    reconnect makes the installer's admission run in full again.
    """
    from django.db import connections

    delay = wait_seconds  # Between wakes; grows only after a failure.
    gap = wait_seconds  # Between full passes while idle.
    due = clock()  # The first wake always runs a full pass.
    while not stop.is_set():
        lease.check()
        try:
            drop_unusable(connections)
            if pending is None or clock() >= due or pending():
                worked = run_once()
                if worked or pending is None:
                    gap = wait_seconds
                else:
                    gap = min(idle_seconds, gap * 2)
                due = clock() + gap
        except Exception as error:
            emit_failure(error)
            # The connection may be broken or mid-transaction: drop it so the
            # next wake reconnects (and readmits), and retry with a full pass.
            connections.close_all()
            delay = min(60, max(wait_seconds, delay * 2))
            gap, due = wait_seconds, clock()
        else:
            delay = wait_seconds
        if heartbeat is not None:
            heartbeat()
        stop.wait(delay)


def drop_unusable(connections):
    """Close each kept database connection that no longer answers (#639).

    ``is_usable`` runs one ``SELECT 1``. A connection the server ended (a
    restart, or an operator's pg_terminate_backend) fails it and is closed
    here, so the caller's next statement opens a new connection, which is
    admitted in full. If the database is still down, that new connection's
    failure is logged as usual.
    """
    for database in connections.all(initialized_only=True):
        if database.connection is not None and not database.is_usable():
            database.close()


def serve_configuration_installer(configuration, lease):
    """Run the durable configuration queue, without inventing campaign admission."""
    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_web import admit_lifecycle_mounts
    from .service_boundaries import admit_online_service

    if admit_online_service(configuration) is not ServiceRole.CONFIG_INSTALLER:
        raise ConfigError("Configuration runtime requires its isolated profile.")
    admit_lifecycle_mounts(configuration)
    configure_operator_database(configuration)
    admit_runtime_database(configuration)
    from .accounts.configuration_service import ConfigurationInstaller

    installer = ConfigurationInstaller.from_configuration(configuration)
    from .service_status import ServiceStatusReporter

    status = ServiceStatusReporter(ServiceRole.CONFIG_INSTALLER.value)

    def run_once():
        """The queue selects opaque identities, never caller-specified file paths."""
        identifier = next_configuration_request()
        if identifier is None:
            # A refused login-policy request is terminal, so the queue never
            # selects it again; its restore, if a crash cut it short, is the
            # idle pass's work.
            installer.restore_refused()
            return
        with installer_request(identifier):
            installer.run_request(identifier)

    return serve_installer_loop(run_once, lease, status=status)


def serve_credential_installer(configuration, lease):
    """Run only this target's durable queue and private-file reconciliation."""
    from .operator_commands import configure_operator_database
    from .runtime_grants import admit_runtime_database
    from .runtime_web import admit_lifecycle_mounts
    from .service_boundaries import admit_online_service

    if admit_online_service(configuration) is not ServiceRole.CREDENTIAL_INSTALLER:
        raise ConfigError("Credential runtime requires its isolated target profile.")
    admit_lifecycle_mounts(configuration)
    configure_operator_database(configuration)
    admit_runtime_database(configuration)
    from .accounts.credential_installation import CredentialInstaller
    from .accounts.setup_credential_installation import TARGETS
    from .credential_runtime import (
        validate_metrics_candidate,
        validation_unavailable,
    )
    from .provider_checks import request_validator

    validator = (
        validate_metrics_candidate
        if configuration.credential_target == "metrics"
        else validation_unavailable
    )
    installer = CredentialInstaller.from_configuration(
        configuration,
        validate=validator,
        validate_request=(
            request_validator(
                configuration.credential_target,
                check=lease.check,
                profile=configuration.profile,
            )
            if configuration.credential_target in TARGETS
            else None
        ),
    )
    from .accounts.handoff_discovery import publish_handoff
    from .service_status import ServiceStatusReporter

    status = ServiceStatusReporter(
        ServiceRole.CREDENTIAL_INSTALLER.value,
        target=configuration.credential_target,
    )
    lease.check()
    publish_handoff(installer.files.private)

    def run_once():
        """Reconcile existing files before any fallible setup-specific relay work.

        Cancellation rollback and ordinary rotations must get a turn even when
        a setup exchange is stuck. Newly staged setup input is consumed on the
        next bounded pass; no new authority or implicit retry is introduced.
        Returns whether any step found work, which keeps the loop polling
        quickly (see bounded_loop).
        """
        worked = [installer.run_once() is not None]
        if configuration.credential_target == "parishsoft":
            from .source.setup_exchange import relay_pending

            lease.check()
            worked.append(relay_pending(installer.files.private))
        elif configuration.credential_target == "google_workspace":
            from .accounts.setup_mail_exchange import relay_pending

            lease.check()
            worked.append(relay_pending(installer.files.private))
            # Off-site backup "Test access" checks need this key and egress.
            from .backup_probes import run_pending_probes

            worked.append(run_pending_probes(installer.files.path, check=lease.check))
        elif configuration.credential_target == "slack":
            from .accounts.setup_notifications import run_pending

            lease.check()
            worked.append(run_pending(installer.files.private, check=lease.check))
        if configuration.credential_target in TARGETS:
            from .accounts.secret_models import SECRET_PENDING
            from .accounts.setup_credential_installation import stage_initial_credential

            lease.check()
            # An already recorded installation returns its receipt on every
            # pass; it is work only while its request is still pending.
            staged = stage_initial_credential(installer.files)
            worked.append(staged is not None and staged.state in SECRET_PENDING)
        return any(worked)

    return serve_installer_loop(
        run_once,
        lease,
        pending=installer_pending(installer, configuration.credential_target),
        status=status,
    )


def installer_pending(installer, target):
    """Build the target's cheap work check for the idle loop (#639).

    It asks, without taking any lock, whether the queue or any of this
    target's setup or access-check steps has something to do, admitting the
    installer first (its full grant check runs here too once its interval
    is up, even while full passes are backed off).
    """
    from . import backup_probes
    from .accounts import setup_credential_installation as initial
    from .accounts import setup_mail_exchange, setup_notifications
    from .source import setup_exchange

    checks = [installer.pending]
    if target == "parishsoft":
        checks.append(setup_exchange.has_pending)
    elif target == "google_workspace":
        checks += [setup_mail_exchange.has_pending, backup_probes.has_pending]
    elif target == "slack":
        checks.append(setup_notifications.has_pending)
    if target in initial.TARGETS:
        checks.append(lambda: initial.has_pending(target))
    return lambda: any(check() for check in checks)


def serve_installer_loop(run_once, lease, *, pending=None, status=None):
    """Share bounded retry, signal restoration and socket cleanup across installers.

    ``pending``, when given, is the installer's cheap work check; see
    bounded_loop for how it lets an idle installer back off. ``status`` (a
    ``service_status.ServiceStatusReporter``) records the installer's service
    status at startup and with the heartbeat on every wake (ADM-13), on the
    loop's kept connection. It writes at most once a minute, never raises
    and touches neither the pending check nor the backoff.
    """
    stop = StopEvent()

    def stopping(signum, frame):
        """Stop promptly after the current finite atomic/recoverable unit finishes."""
        stop.set()

    previous = {
        sig: signal.signal(sig, stopping) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        from .installer_health import publish_heartbeat

        def heartbeat():
            """Publish liveness, then the status record when it is due.

            The write uses the loop's kept connection (opening one only if
            the loop has none), inside its own short transaction.
            """
            publish_heartbeat()
            if status is not None:
                status.report(connect=True)

        emit_started()
        heartbeat()
        bounded_loop(
            run_once,
            lease=lease,
            stop=stop,
            heartbeat=heartbeat,
            pending=pending,
        )
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


def independent_producer(guard, operation, *args):
    """One failed recovery owner cannot starve unrelated cleanup or hint scans.

    Scheduler ownership checks remain outside the exception boundary: losing
    the singleton session is fatal, not a recoverable sub-producer failure.
    Each operation still owns its ordinary domain admission and transaction.
    """
    from .activation_hold import activating

    guard.check()
    try:
        result = operation(*args)
    except Exception as error:
        # A configuration change between its YAML selection and database
        # activation (#429) is a WARNING; the next pass, seconds away, runs
        # the producer again. A stuck one (no installer running) stays ERROR.
        level = logging.WARNING if activating(error) else logging.ERROR
        emit_failure(error, level=level)
        result = ()
    guard.check()
    return result


def sibling_command(queue, argv=None):
    """This process's own invocation, marked as its ``queue`` sibling.

    The sibling re-executes exactly how this process was started (from
    ``sys.orig_argv``) plus ``--queue QUEUE``, rather than a separately
    constructed command, so whatever the entry point sets up before the CLI
    runs applies to both processes alike.
    """
    argv = list(sys.orig_argv if argv is None else argv)
    if "runtime" not in argv[1:] or "--queue" in argv:
        raise ConfigError(f"The {queue} consumer requires a runtime command.")
    return [sys.executable, *argv[1:], "--queue", queue]


def source_command(argv=None):
    """The worker's invocation, narrowed to the source queue (#336)."""
    return sibling_command("source", argv)


class SiblingConsumer:
    """A container's second consumer process, supervised by the first.

    The main process starts this sibling, which re-executes the same admitted
    runtime (see ``sibling_command``) with the same configuration, mounts,
    credentials and SQL login. Both share the container's lifetime: a stop
    request is forwarded at once so both drain together, and a sibling that
    exits or stops publishing liveness stops the main process, so the
    container exits (and restarts, in production) instead of silently losing
    the sibling's work. Subclasses name the sibling's ``--queue`` value, its
    heartbeat file and its reviewed timeout kind (audit.schemas).
    """

    QUEUE = None
    WHAT = None
    # Silence this long (twice the container probe's limit) stops the worker.
    STALE_LIMIT = 2 * PROBE_MAX_AGE
    # The sibling is killed this long before Docker's kill of the whole
    # container, so its durable timeout entry is written first.
    KILL_MARGIN = STOP_MARGIN_SECONDS

    def __init__(self, *, drain_seconds, argv=None):
        self.drain_seconds = drain_seconds
        self.started = monotonic()
        self.stale = 0
        # When the first stop request reached this sibling (see terminate).
        self.stop_requested = None
        # Same interpreter, environment and entry point; output joins the
        # container's log.
        self.process = subprocess.Popen(sibling_command(self.QUEUE, argv))

    @staticmethod
    def heartbeat_path():
        """The file this sibling publishes its liveness to."""
        raise NotImplementedError

    def check(self):
        """Raise when the sibling has exited or has been silent far too long.

        An exited sibling stops the worker at once. A stale heartbeat alone
        does not: one slow window (a long promotion transaction, a loaded
        host) can delay the sibling's liveness past the probe's limit, and
        restarting the container would interrupt that very work. Each stale
        observation is logged durably; only silence past ``STALE_LIMIT``
        (twice the probe's limit) stops the worker, and that is logged too.
        Before its first heartbeat the sibling's age counts from its start.
        """
        from .installer_health import MAX_AGE_SECONDS, heartbeat_age

        if self.process.poll() is not None:
            raise ConfigError(f"The {self.QUEUE} consumer exited.")
        age = heartbeat_age(self.heartbeat_path())
        if age is None:
            age = monotonic() - self.started
        if age <= MAX_AGE_SECONDS:
            self.stale = 0
            return
        self.stale += 1
        if age < self.STALE_LIMIT:
            self._record("WARNING", MAX_AGE_SECONDS, age, count=self.stale)
            return
        self._record("ERROR", self.STALE_LIMIT, age, count=self.stale)
        raise ConfigError(f"The {self.QUEUE} consumer stopped reporting progress.")

    def _record(self, level, limit, elapsed, *, count=None):
        """Log one stale-liveness observation or stop on the durable timeout log."""
        from .audit.timeouts import record_timeout

        # WHAT is a reviewed timeout kind for this kind of work's helper
        # process; reusing it keeps the SQL context vocabulary fixed.
        record_timeout(
            Event.HELPER_TIMED_OUT,
            what=self.WHAT,
            level=level,
            limit_seconds=limit,
            elapsed_seconds=elapsed,
            count=count,
        )

    def terminate(self):
        """Ask the sibling to drain; safe to repeat and to call from a signal.

        The first call notes when the stop began: under ``docker stop`` that
        is Docker's SIGTERM, forwarded here at once, and Docker's kill of the
        whole container follows ``drain_seconds`` later.
        """
        if self.stop_requested is None:
            self.stop_requested = monotonic()
        if self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.send_signal(signal.SIGTERM)

    def close(self):
        """Stop the sibling and wait for its drain, killing it only past the grace.

        The main process calls this after its own drain, so the wait is only
        what is left of the grace period since the first stop request, less
        KILL_MARGIN. A sibling still running then is logged durably (that
        deadline, the grace less KILL_MARGIN, as the limit, and the time
        since the stop request as elapsed) and only then killed, all before
        Docker's own kill could arrive.
        """
        self.terminate()
        left = self.stop_requested + self.drain_seconds - self.KILL_MARGIN
        try:
            self.process.wait(timeout=max(0.0, left - monotonic()))
        except subprocess.TimeoutExpired:
            self._record(
                "ERROR",
                max(0, self.drain_seconds - self.KILL_MARGIN),
                monotonic() - self.stop_requested,
            )
            self.process.kill()
            self.process.wait()


class SourceConsumer(SiblingConsumer):
    """The worker container's second consumer process: source work only (#336).

    ParishSoft refreshes and setup source loads take minutes. On the general
    consumer, which runs one message at a time, exports and operational
    collection waited behind them; this sibling consumes only the source
    queue.
    """

    QUEUE = "source"
    WHAT = "source_helper"

    @staticmethod
    def heartbeat_path():
        """The source consumer's own liveness file."""
        from .installer_health import SOURCE_HEARTBEAT

        return SOURCE_HEARTBEAT


class MailConsumer(SiblingConsumer):
    """Mail dispatch's second consumer process: the same mail queues.

    One mail consumer sends one message at a time, so the launch's Family
    mail took the sum of every message's preparation and SMTP time. A second
    process on the same queues, with its own batched helper (#284) and SMTP
    connection, sends another message meanwhile. The scheduler's hints go to
    whichever process takes them first; each message is still exactly one
    Task, and its claim and fences keep two processes from ever running it
    twice (tests/stewardship/database/test_mail_consumers_postgresql.py).
    """

    QUEUE = "mail"
    WHAT = "mail_helper"

    @staticmethod
    def heartbeat_path():
        """The second mail consumer's own liveness file."""
        from .installer_health import MAIL_HEARTBEAT

        return MAIL_HEARTBEAT


def split_source(configuration):
    """Whether the worker login can hold two consumer processes' connections.

    Each consumer process may hold a task connection, a lease-renewal
    connection and a short-lived timeout-log connection. The worker login's
    limit is ``rollout_overlap * 3``; Compose recreates a container by
    stopping it before starting its replacement, so with the default overlap
    of 2 those six connections serve the container's two processes. A budget
    too small for both keeps one process on every queue.
    """
    from .database_provisioning import role_limit
    from .jobs.queues import SOURCE_SPLIT_CONNECTIONS

    return role_limit(configuration, ServiceRole.WORKER) >= SOURCE_SPLIT_CONNECTIONS


def split_mail(configuration):
    """Whether mail dispatch runs a second mail consumer process.

    Two processes need the mail login's six connections, as for the worker
    (``split_source``), and the deployment must ask for two consumers
    (``mail_consumers``, default 2; 1 is the operator's fallback). A budget
    too small for both keeps one process.
    """
    from .database_provisioning import role_limit
    from .jobs.queues import MAIL_SPLIT_CONNECTIONS

    return (
        configuration.mail_consumers >= 2
        and role_limit(configuration, ServiceRole.MAIL_DISPATCH)
        >= MAIL_SPLIT_CONNECTIONS
    )


def _family_sender():
    """This mail consumer's Family mail sender state (imported once admitted)."""
    from .jobs.family_mail_delivery_tasks import family_sender_state

    return family_sender_state()


def serve_background(configuration, lease, *, source=False, mail=False):
    """Assemble one admitted queue process and retain exclusion through final drain.

    ``source`` selects the worker container's source-queue sibling, which the
    worker's main process starts (see SourceConsumer); ``mail`` selects mail
    dispatch's second mail consumer (see MailConsumer).
    """
    from uuid import uuid4

    from .consumer_runtime import publish_single_process_receipts
    from .installer_health import publish_heartbeat
    from .jobs.queues import ROLE_QUEUES, SOURCE_QUEUES
    from .runtime_background import configure_background, matching_authority

    role = configuration.service_role
    if source and role is not ServiceRole.WORKER:
        raise ConfigError("Only the worker runs a source-queue consumer.")
    if mail and role is not ServiceRole.MAIL_DISPATCH:
        raise ConfigError("Only mail dispatch runs a second mail consumer.")
    queues = None
    # The sibling this main process starts, if any; siblings start none.
    companion_type = None
    if role is ServiceRole.WORKER and not source and split_source(configuration):
        companion_type = SourceConsumer
    elif role is ServiceRole.MAIL_DISPATCH and not mail and split_mail(configuration):
        companion_type = MailConsumer
    if source:
        queues = SOURCE_QUEUES
    elif companion_type is SourceConsumer:
        queues = ROLE_QUEUES[role] - SOURCE_QUEUES
    # Both mail processes consume all of mail dispatch's queues.
    this_sibling = SourceConsumer if source else MailConsumer if mail else None
    stop = StopEvent()
    from .service_status import ServiceStatusReporter

    status = ServiceStatusReporter(
        role.value,
        process="source" if source else "mail" if mail else "main",
        sender=_family_sender if role is ServiceRole.MAIL_DISPATCH else None,
    )
    if role is ServiceRole.MAIL_DISPATCH and not mail:
        from .installer_health import MAIL_SYSTEMIC_STOP, clear_stopped

        # A restart is what lifts a SYSTEMIC stop; the second consumer starts
        # after this, so it never sees the previous run's marker.
        with suppress(OSError):
            clear_stopped(MAIL_SYSTEMIC_STOP)

    def heartbeat():
        """Long task renewal and idle loop progress both retain lifecycle evidence.

        The status record is written here only on a connection this thread
        already holds (a lease renewal's, the scheduler's or a bulk send's),
        never on a timer thread's new one; see ServiceStatusReporter.report.
        """
        lease.check()
        publish_heartbeat(
            None if this_sibling is None else this_sibling.heartbeat_path()
        )
        status.report()

    def idle_status():
        """Write the status record from an idle consumer (its own connection)."""
        status.report(connect=True)

    def stopping(signum, frame):
        """Signals request drainage; no provider work or SQL runs in this handler."""
        stop.set()

    previous = {
        sig: signal.signal(sig, stopping) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    assembled = companion = None
    try:
        lease.check()
        assembled = configure_background(
            configuration, stop=stop, heartbeat=heartbeat, queues=queues
        )
        # The process is admitted: record that it started (this thread's
        # admission connection).
        status.report(connect=True)
        # Model-dependent runtime owners may be imported only after the fresh
        # process has configured Django and admitted its SQL identity.
        from .accounts.automation_maintenance import MaintenanceProducer
        from .accounts.branding_cleanup import produce_cleanup
        from .accounts.setup_mail import recover_pending as recover_setup_mail
        from .accounts.setup_notifications import recover_pending as recover_setup_slack
        from .accounts.setup_staging import produce_setup_expiry
        from .campaigns.boundary_production import produce_boundaries
        from .campaigns.digest_schedule_planning import DigestScheduleProducer
        from .campaigns.schedule_production import FamilyScheduleProducer
        from .jobs.operational_collection import produce_collection
        from .jobs.operational_fanout import produce_fanout
        from .jobs.operational_slack_tasks import produce_slack
        from .jobs.processes import serve_consumer, serve_scheduler
        from .jobs.security_owner import SECURITY
        from .jobs.web_health import WebHealthProducer, replica_hosts
        from .reports.digest_finalization import (
            DailyDigestFinalizeProducer,
            WeeklyDigestFinalizeProducer,
        )
        from .reports.digest_ownership import DailyDigestProducer
        from .reports.export_cleanup import produce_cleanup as produce_export_cleanup
        from .reports.fact_production import produce_facts
        from .reports.verification_production import produce_verifications
        from .reports.weekly_ownership import WeeklyDigestProducer
        from .source.production import SourceProducer
        from .source.setup_cleanup import produce_setup_cleanup
        from .source.setup_final_production import produce_finalization

        if this_sibling is not None:
            # The main process owns the container's receipts and rotation
            # acknowledgement; both processes load the same mounts at
            # container start and are only ever recreated together.
            emit_started()
            return serve_consumer(
                assembled.broker,
                lease=lease,
                stop=stop,
                heartbeat=heartbeat,
                idle=idle_status,
            )
        publish_single_process_receipts(configuration, assembled.receipts)
        emit_started()
        if configuration.service_role in {
            ServiceRole.WORKER,
            ServiceRole.MAIL_DISPATCH,
        }:
            from .credential_runtime import acknowledge_rotations

            if companion_type is not None and not stop.is_set():
                companion = companion_type(
                    drain_seconds=configuration.runtime_budget.drain_seconds
                )
            receipts = dict(assembled.receipts)

            def idle():
                """Acknowledge rotations, then refresh the status record."""
                try:
                    acknowledge_rotations(configuration, receipts)
                finally:
                    idle_status()

            return serve_consumer(
                assembled.broker,
                lease=lease,
                stop=stop,
                heartbeat=heartbeat,
                idle=idle,
                companion=companion,
            )
        producer = SourceProducer(uuid4())
        # The bulk sweep only when the bulk Family send is on (#430); off,
        # the producer is built exactly as before.
        schedules = FamilyScheduleProducer(
            uuid4(), **({"bulk": True} if configuration.bulk_family_send else {})
        )
        digests = DigestScheduleProducer(uuid4())
        daily = DailyDigestProducer(uuid4())
        daily_finalization = DailyDigestFinalizeProducer(uuid4())
        weekly = WeeklyDigestProducer(uuid4())
        weekly_finalization = WeeklyDigestFinalizeProducer(uuid4())
        maintenance = MaintenanceProducer()
        # Probes web once a minute in its own thread (#392 L1); the loop only
        # records finished results, so it never waits on HTTP.
        web_health = WebHealthProducer(
            replica_hosts(configuration.runtime_budget.replicas)
        )

        def produce(guard):
            """Expire abandoned setup even while exact candidate recovery is pending.

            Expiry uses its original SQL-bound login, never selects configuration,
            and is needed to unblock a selected-but-unapplied setup abort. Normal
            source production and file cleanup still require matching authority.
            """
            operational = independent_producer(guard, produce_collection, guard)
            # Like operational intake, the web probe runs through setup and
            # activation holds: it reads no configuration or campaign data.
            independent_producer(guard, web_health, guard)
            independent_producer(guard, produce_setup_expiry, guard)
            finalization = independent_producer(
                guard, produce_finalization, assembled.store, guard
            )
            try:
                matching_authority(assembled.store)
            except ConfigError as error:
                # Imported here: this process admits itself before Django
                # models (and so the installer lock module) may load.
                from .activation_hold import activating

                if activating(error):
                    # A change is activating (#429): not a setup hold, and
                    # the next pass, seconds away, runs the ordinary producers.
                    return (*operational, *finalization)
                from .accounts.setup_startup import initial_setup_hold

                # A dead original session can still be expired above. While
                # awaiting installer rollback, no ordinary producer is admitted.
                initial_setup_hold(assembled.store)
                return (*operational, *finalization)
            operational += independent_producer(guard, produce_fanout, guard)
            operational += independent_producer(guard, produce_fanout, guard, SECURITY)
            operational += independent_producer(guard, produce_slack, guard)
            independent_producer(guard, recover_setup_mail)
            independent_producer(guard, recover_setup_slack)
            from .accounts.campaign_mail_delivery import recover_pending
            from .jobs.family_mail_test_tasks import (
                recover_pending as recover_family_tests,
            )

            independent_producer(guard, recover_pending)
            independent_producer(guard, recover_family_tests)
            return (
                *operational,
                *finalization,
                *independent_producer(guard, produce_boundaries, guard),
                *independent_producer(guard, schedules, guard),
                *independent_producer(guard, digests, guard),
                *independent_producer(guard, daily, guard),
                *independent_producer(guard, daily_finalization, guard),
                *independent_producer(guard, weekly, guard),
                *independent_producer(guard, weekly_finalization, guard),
                *independent_producer(guard, producer, guard),
                *independent_producer(guard, produce_cleanup, guard),
                *independent_producer(guard, produce_export_cleanup, guard),
                *independent_producer(guard, produce_facts, guard),
                *independent_producer(guard, produce_verifications, guard),
                *independent_producer(guard, produce_setup_cleanup, guard),
                *independent_producer(guard, maintenance, guard),
            )

        return serve_scheduler(
            assembled.broker,
            handlers=assembled.handlers,
            lease=lease,
            stop=stop,
            heartbeat=heartbeat,
            produce=produce,
            # The bulk Family send's consumers drain these task types
            # themselves (#430), so a hint only has to wake them.
            **(
                {"drained": frozenset({"family_mail_prepare", "outbox_delivery"})}
                if configuration.bulk_family_send
                else {}
            ),
        )
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if companion is not None:
            companion.close()
        if assembled is not None:
            assembled.broker.app.close()
        from django.conf import settings
        from django.db import connections

        if settings.configured:
            connections.close_all()


def execute_runtime(args):
    """Admit the operator-rendered profile and retain exclusion until final exit."""
    configure_logging()
    try:
        if args.config is None:
            raise ConfigError("Operational runtime requires explicit configuration.")
        configuration = load_deployment(args.config)
        runners = {
            ServiceRole.WEB: serve_web,
            ServiceRole.CONFIG_INSTALLER: serve_configuration_installer,
            ServiceRole.CREDENTIAL_INSTALLER: serve_credential_installer,
            ServiceRole.WORKER: serve_background,
            ServiceRole.SCHEDULER: serve_background,
            ServiceRole.MAIL_DISPATCH: serve_background,
        }
        runner = runners.get(configuration.service_role)
        if runner is None:
            raise ConfigError("This service's operational runtime is unavailable.")
        options, queue = {}, getattr(args, "queue", None)
        if queue is not None and (queue, configuration.service_role) not in {
            ("source", ServiceRole.WORKER),
            ("mail", ServiceRole.MAIL_DISPATCH),
        }:
            raise ConfigError("This service runs no such sibling consumer.")
        if configuration.service_role is ServiceRole.WORKER:
            options = {"source": queue == "source"}
        elif configuration.service_role is ServiceRole.MAIL_DISPATCH:
            options = {"mail": queue == "mail"}
        with StartupLease(
            RuntimeLayout(configuration).interlock, offline=False
        ) as lease:
            # Every runner starts by admitting its SQL login; wait, boundedly,
            # for a database that is not accepting connections yet instead of
            # exiting at once (#453). It runs under the lease, so offline work
            # still cannot start while a service is waiting.
            from .runtime_database import await_database

            await_database(configuration)
            status = runner(configuration, lease, **options)
        # After the runner's cleanup (a sibling consumer closed, the lease
        # released): a consumer stopped by a fatal failure exits 70 (#386).
        from .jobs.broker import exit_if_fatal

        return exit_if_fatal(status)
    except Exception as error:
        # The category (never exception text) says which kind of check
        # refused; the detail reaches the log only with debug logging on.
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: runtime unavailable; verify isolated mounts, credentials, "
            "database roles, migration state and offline exclusion",
            file=sys.stderr,
        )
        return 2
