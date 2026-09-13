"""Isolated installer processes and operator-driven whole-consumer confirmation.

No process controls Docker. Consumer confirmation must run inside the recreated
service, not a one-off container that only happens to reopen the current files.
Provider and key-retirement owners supply their validators in later phases.
"""

import sys
from threading import Event as StopEvent
from uuid import UUID

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .observability import Event, configure_logging, emit_failure
from .runtime_paths import RuntimeLayout
from .startup_interlock import StartupLease


def validate_metrics_candidate(value):
    """Strict local syntax and independent receipt are sufficient for this target."""
    from .accounts.metrics_credentials import MetricsCredential

    MetricsCredential.parse(value)
    return True


def validation_unavailable(value):
    """Do not substitute success for provider tests or key-retirement evidence."""
    from .accounts.credential_installation import CredentialValidationUnavailable

    raise CredentialValidationUnavailable()


def acknowledge_web(configuration, request_id):
    """Confirm every live worker after full mount, SQL and dependency admission."""
    from django.db import connections

    from .runtime_web import configure_web

    if not isinstance(request_id, UUID):
        raise ConfigError("A credential request UUID is required.")
    runtime = configure_web(configuration)
    try:
        _confirm_loaded(configuration, request_id)
    finally:
        connections.close_all()
        runtime.client.connection_pool.disconnect()
        if runtime.telemetry_client is not None:
            runtime.telemetry_client.connection_pool.disconnect()


def acknowledge_background(configuration, request_id, *, lease):
    """Confirm the live background process, never this command's newly read files.

    Admission repeats kernel/SQL/broker/configuration checks, but does not publish
    a new process receipt or start a queue consumer. The existing supervised
    process must already have published matching credentials in private tmpfs.
    """
    from django.db import connections

    from .runtime_background import configure_background

    if not isinstance(request_id, UUID) or configuration.service_role not in {
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    }:
        raise ConfigError("Background acknowledgement requires an isolated consumer.")
    stop = StopEvent()
    runtime = None
    try:
        lease.check()
        runtime = configure_background(configuration, stop=stop, heartbeat=lease.check)
        lease.check()
        _confirm_loaded(configuration, request_id)
    finally:
        stop.set()
        connections.close_all()
        if runtime is not None:
            runtime.broker.app.close()


def _confirm_loaded(configuration, request_id):
    """Bind one actual service's loaded cohort, mounted bytes and guarded SQL ACK."""
    from .accounts.credential_installation import acknowledge_loaded_credential
    from .accounts.key_files import read_private
    from .accounts.metrics_credentials import credential_receipt
    from .accounts.secret_models import SecretReplacementRequest
    from .consumer_runtime import loaded_service_receipts

    receipts = loaded_service_receipts(configuration)
    row = SecretReplacementRequest.objects.get(pk=request_id)
    if (
        row.target not in receipts
        or row.target not in configuration.secrets
        or receipts[row.target] != row.resulting_fingerprint
        or row.state not in {"awaiting_ack", "cleanup_pending", "applied"}
    ):
        raise ConfigError("The whole consumer has not loaded this replacement.")
    value = read_private(configuration.secrets[row.target])
    if credential_receipt(value, row.target) != receipts[row.target]:
        raise ConfigError("The mounted credential differs from worker evidence.")
    # Reopening current files is insufficient: the original service process(es)
    # must still be live and agree immediately before the guarded write.
    if loaded_service_receipts(configuration) != receipts:
        raise ConfigError("The consumer cohort changed during confirmation.")
    acknowledge_loaded_credential(
        request_id=request_id,
        consumer=configuration.service_role.value,
        loaded_value=value,
    )


def execute_acknowledgement(args):
    """Expose a fixed outcome; never echo supplied identities or credential text."""
    configure_logging()
    try:
        if args.config is None or args.request_id is None:
            raise ConfigError("Configuration and request identity are required.")
        identifier = UUID(args.request_id)
        configuration = load_deployment(args.config)
        with StartupLease(
            RuntimeLayout(configuration).interlock, offline=False
        ) as lease:
            if configuration.service_role is ServiceRole.WEB:
                acknowledge_web(configuration, identifier)
            elif configuration.service_role in {
                ServiceRole.WORKER,
                ServiceRole.SCHEDULER,
                ServiceRole.MAIL_DISPATCH,
            }:
                acknowledge_background(configuration, identifier, lease=lease)
            else:
                raise ConfigError("This consumer runtime is not implemented.")
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: credential acknowledgement refused; verify the request and "
            "recreate the complete consumer service before retrying",
            file=sys.stderr,
        )
        return 2
    print("Whole-service credential acknowledgement recorded")
    return 0
