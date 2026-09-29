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
    emit,
    emit_failure,
    installer_request,
)
from .runtime_paths import RuntimeLayout, private_directory
from .startup_interlock import StartupLease


def gunicorn_options(configuration):
    """Only validated finite topology values control workers, threads and drainage."""
    budget = configuration.runtime_budget
    return {
        "bind": ["0.0.0.0:8000"],
        "workers": budget.web_processes,
        "threads": budget.web_threads,
        "worker_class": "gthread",
        "preload_app": False,
        "reload": configuration.profile is DeploymentProfile.DEVELOPMENT,
        "reload_engine": "poll",
        "timeout": budget.server_timeout_seconds,
        "graceful_timeout": budget.drain_seconds,
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

        publish_worker_receipts(worker)
        observer = PeriodicAuthenticationHealth(
            settings.STEWARDSHIP_AUTH_RUNTIME.limiter,
            check=settings.STEWARDSHIP_WEB_LEASE.check,
            active=lambda: worker.alive,
            retire=lambda: worker.handle_exit(signal.SIGTERM, None),
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

    The master never loads Django application modules or opens dependency sockets.
    Development worker replacement therefore imports changed source afresh instead
    of inheriting stale preloaded code. Every child retains the lifecycle lease.
    """
    from gunicorn.app.base import BaseApplication
    from gunicorn.arbiter import Arbiter
    from gunicorn.errors import HaltServer
    from gunicorn.glogging import Logger

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
        Arbiter(Application()).run()
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
        emit(Event.STARTUP_VALIDATED)
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


def bounded_loop(run_once, *, lease, stop, wait_seconds=2, heartbeat=None):
    """Retry transient work without spinning; SIGTERM stops between bounded passes.

    A failed lease is fatal rather than a retryable dependency outage. Database
    sockets close after each pass so idle installers do not consume interactive
    headroom. Durable engines retain their own request checkpoints and retries.
    """
    from django.db import connections

    delay = wait_seconds
    while not stop.is_set():
        lease.check()
        try:
            run_once()
        except Exception as error:
            emit_failure(error)
            delay = min(60, max(wait_seconds, delay * 2))
        else:
            delay = wait_seconds
        finally:
            connections.close_all()
        if heartbeat is not None:
            heartbeat()
        stop.wait(delay)


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

    return serve_installer_loop(run_once, lease)


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
            request_validator(configuration.credential_target, check=lease.check)
            if configuration.credential_target
            in {"parishsoft", "google_workspace", "slack"}
            else None
        ),
    )
    from .accounts.handoff_discovery import publish_handoff

    lease.check()
    publish_handoff(installer.files.private)

    def run_once():
        """Reconcile existing files before any fallible setup-specific relay work.

        Cancellation rollback and ordinary rotations must get a turn even when
        a setup exchange is stuck. Newly staged setup input is consumed on the
        next bounded pass; no new authority or implicit retry is introduced.
        """
        installer.run_once()
        if configuration.credential_target == "parishsoft":
            from .source.setup_exchange import relay_pending

            lease.check()
            relay_pending(installer.files.private)
        elif configuration.credential_target == "google_workspace":
            from .accounts.setup_mail_exchange import relay_pending

            lease.check()
            relay_pending(installer.files.private)
            # Off-site backup "Test access" checks need this key and egress.
            from .backup_probes import run_pending_probes

            run_pending_probes(installer.files.path, check=lease.check)
        elif configuration.credential_target == "slack":
            from .accounts.setup_notifications import run_pending

            lease.check()
            run_pending(installer.files.private, check=lease.check)
        if configuration.credential_target in {
            "parishsoft",
            "google_workspace",
            "slack",
        }:
            from .accounts.setup_credential_installation import stage_initial_credential

            lease.check()
            stage_initial_credential(installer.files)

    return serve_installer_loop(run_once, lease)


def serve_installer_loop(run_once, lease):
    """Share bounded retry, signal restoration and socket cleanup across installers."""
    stop = StopEvent()

    def stopping(signum, frame):
        """Stop promptly after the current finite atomic/recoverable unit finishes."""
        stop.set()

    previous = {
        sig: signal.signal(sig, stopping) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        from .installer_health import publish_heartbeat

        emit(Event.STARTUP_VALIDATED)
        publish_heartbeat()
        bounded_loop(run_once, lease=lease, stop=stop, heartbeat=publish_heartbeat)
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
    guard.check()
    try:
        result = operation(*args)
    except Exception as error:
        emit_failure(error)
        result = ()
    guard.check()
    return result


class SourceConsumer:
    """The worker container's second consumer process: source work only (#336).

    ParishSoft refreshes and setup source loads take minutes. On the general
    consumer, which runs one message at a time, exports and operational
    collection waited behind them. The worker process starts this sibling,
    which runs the same admitted runtime (``runtime --queue source``) with the
    same configuration, mounts, credentials and SQL login but consumes only
    the source queue. Both share the container's lifetime: a stop request is
    forwarded at once so both drain together, and a sibling that exits or
    stops publishing liveness stops the worker, so the container exits (and
    restarts, in production) instead of silently losing source work.
    """

    def __init__(self, config_path, *, drain_seconds):
        self.drain_seconds = drain_seconds
        self.started = monotonic()
        # Same interpreter and environment; output joins the container's log.
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "parishkit.stewardship",
                "runtime",
                "--config",
                str(config_path),
                "--queue",
                "source",
            ]
        )

    def check(self):
        """Raise unless the sibling is running and, after startup, reporting."""
        from .installer_health import MAX_AGE_SECONDS, SOURCE_HEARTBEAT, healthcheck

        if self.process.poll() is not None:
            raise ConfigError("The source consumer exited.")
        if (
            monotonic() - self.started > MAX_AGE_SECONDS
            and healthcheck(SOURCE_HEARTBEAT) != 0
        ):
            raise ConfigError("The source consumer stopped reporting progress.")

    def terminate(self):
        """Ask the sibling to drain; safe to repeat and to call from a signal."""
        if self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.send_signal(signal.SIGTERM)

    def close(self):
        """Stop the sibling and wait for its drain, killing it only past the grace."""
        self.terminate()
        try:
            self.process.wait(timeout=self.drain_seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def split_source(configuration):
    """Whether the worker login can hold two consumer processes' connections.

    Each consumer process needs one task and one renewal connection. The
    worker login's limit is ``rollout_overlap * 2``; Compose recreates a
    container by stopping it before starting its replacement, so with the
    default overlap of 2 those four connections serve the container's two
    processes. A budget too small for both keeps one process on every queue.
    """
    from .database_provisioning import role_limit

    return role_limit(configuration, ServiceRole.WORKER) >= 4


def serve_background(configuration, lease, *, source=False, config_path=None):
    """Assemble one admitted queue process and retain exclusion through final drain.

    ``source`` selects the worker container's source-queue sibling; the
    worker's main process starts it from ``config_path`` (see SourceConsumer).
    """
    from uuid import uuid4

    from .consumer_runtime import publish_single_process_receipts
    from .installer_health import SOURCE_HEARTBEAT, publish_heartbeat
    from .jobs.queues import ROLE_QUEUES, SOURCE_QUEUES
    from .runtime_background import configure_background, matching_authority

    role = configuration.service_role
    if source and role is not ServiceRole.WORKER:
        raise ConfigError("Only the worker runs a source-queue consumer.")
    queues = None
    sibling = role is ServiceRole.WORKER and not source and split_source(configuration)
    if source:
        queues = SOURCE_QUEUES
    elif sibling:
        queues = ROLE_QUEUES[role] - SOURCE_QUEUES
    stop = StopEvent()

    def heartbeat():
        """Long task renewal and idle loop progress both retain lifecycle evidence."""
        lease.check()
        publish_heartbeat(SOURCE_HEARTBEAT if source else None)

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
        # Model-dependent runtime owners may be imported only after the fresh
        # process has configured Django and admitted its SQL identity.
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

        if source:
            # The main worker process owns the container's receipts and
            # rotation acknowledgement; both processes load the same mounts
            # at container start and are only ever recreated together.
            emit(Event.STARTUP_VALIDATED)
            return serve_consumer(
                assembled.broker, lease=lease, stop=stop, heartbeat=heartbeat
            )
        publish_single_process_receipts(configuration, assembled.receipts)
        emit(Event.STARTUP_VALIDATED)
        if configuration.service_role in {
            ServiceRole.WORKER,
            ServiceRole.MAIL_DISPATCH,
        }:
            from .credential_runtime import acknowledge_rotations

            if sibling and not stop.is_set():
                companion = SourceConsumer(
                    config_path,
                    drain_seconds=configuration.runtime_budget.drain_seconds,
                )
            receipts = dict(assembled.receipts)
            return serve_consumer(
                assembled.broker,
                lease=lease,
                stop=stop,
                heartbeat=heartbeat,
                idle=lambda: acknowledge_rotations(configuration, receipts),
                companion=companion,
            )
        producer = SourceProducer(uuid4())
        schedules = FamilyScheduleProducer(uuid4())
        digests = DigestScheduleProducer(uuid4())
        daily = DailyDigestProducer(uuid4())
        daily_finalization = DailyDigestFinalizeProducer(uuid4())
        weekly = WeeklyDigestProducer(uuid4())
        weekly_finalization = WeeklyDigestFinalizeProducer(uuid4())

        def produce(guard):
            """Expire abandoned setup even while exact candidate recovery is pending.

            Expiry uses its original SQL-bound login, never selects configuration,
            and is needed to unblock a selected-but-unapplied setup abort. Normal
            source production and file cleanup still require matching authority.
            """
            operational = independent_producer(guard, produce_collection, guard)
            independent_producer(guard, produce_setup_expiry, guard)
            finalization = independent_producer(
                guard, produce_finalization, assembled.store, guard
            )
            try:
                matching_authority(assembled.store)
            except ConfigError:
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
            )

        return serve_scheduler(
            assembled.broker,
            handlers=assembled.handlers,
            lease=lease,
            stop=stop,
            heartbeat=heartbeat,
            produce=produce,
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
        if configuration.service_role is ServiceRole.WORKER:
            options = {"source": queue == "source", "config_path": args.config}
        elif queue is not None:
            raise ConfigError("Only the worker runs a source-queue consumer.")
        with StartupLease(
            RuntimeLayout(configuration).interlock, offline=False
        ) as lease:
            return runner(configuration, lease, **options)
    except Exception:
        emit(Event.STARTUP_REJECTED, level=logging.ERROR)
        print(
            "ERROR: runtime unavailable; verify isolated mounts, credentials, "
            "database roles, migration state and offline exclusion",
            file=sys.stderr,
        )
        return 2
