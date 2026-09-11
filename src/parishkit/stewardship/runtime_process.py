"""Operational process lifetimes retain a real shared offline-exclusion lease."""

import logging
import signal
import sys
from threading import Event as StopEvent

from parishkit.config import ConfigError

from .deployment import DeploymentProfile, ServiceRole, load_deployment
from .observability import Event, configure_logging, emit
from .runtime_paths import RuntimeLayout
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
        "accesslog": None,
        "errorlog": "-",
        "forwarded_allow_ips": "",  # The application admits its one exact proxy.
        "limit_request_line": 8190,
        "limit_request_fields": 64,
        "limit_request_field_size": 8190,
    }


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
            current_state__in=("staged", "validating", "prepared", "yaml_activated")
        )
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)
        .first()
    )


def bounded_loop(run_once, *, lease, stop, wait_seconds=2):
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
        except Exception:
            emit(Event.TASK_FAILED, level=logging.ERROR)
            delay = min(60, max(wait_seconds, delay * 2))
        else:
            delay = wait_seconds
        finally:
            connections.close_all()
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
    stop = StopEvent()

    def stopping(signum, frame):
        """Stop promptly after the current finite atomic/recoverable unit finishes."""
        stop.set()

    def run_once():
        """The queue selects opaque identities, never caller-specified file paths."""
        identifier = next_configuration_request()
        if identifier is not None:
            installer.run_request(identifier)

    previous = {
        sig: signal.signal(sig, stopping) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        emit(Event.STARTUP_VALIDATED)
        bounded_loop(run_once, lease=lease, stop=stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


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
        }
        runner = runners.get(configuration.service_role)
        if runner is None:
            raise ConfigError("This service's operational runtime is unavailable.")
        with StartupLease(
            RuntimeLayout(configuration).interlock, offline=False
        ) as lease:
            return runner(configuration, lease)
    except Exception:
        emit(Event.STARTUP_REJECTED, level=logging.ERROR)
        print(
            "ERROR: runtime unavailable; verify isolated mounts, credentials, "
            "database roles, migration state and offline exclusion",
            file=sys.stderr,
        )
        return 2
