"""Isolated installer processes and operator-driven whole-consumer confirmation.

No process controls Docker. Consumer confirmation must run inside the recreated
service, not a one-off container that only happens to reopen the current files.
Provider and key-retirement owners supply their validators in later phases.
"""

import sys
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

    from .accounts.key_files import read_private
    from .accounts.metrics_credentials import credential_receipt
    from .consumer_runtime import loaded_service_receipts
    from .runtime_web import configure_web

    if not isinstance(request_id, UUID):
        raise ConfigError("A credential request UUID is required.")
    runtime = configure_web(configuration)
    try:
        from .accounts.credential_installation import acknowledge_loaded_credential
        from .accounts.secret_models import SecretReplacementRequest

        receipts = loaded_service_receipts(configuration)
        row = SecretReplacementRequest.objects.get(pk=request_id)
        if (
            row.target not in receipts
            or receipts[row.target] != row.resulting_fingerprint
            or row.state not in {"awaiting_ack", "cleanup_pending", "applied"}
        ):
            raise ConfigError("The whole consumer has not loaded this replacement.")
        value = read_private(configuration.secrets[row.target])
        if credential_receipt(value, row.target) != receipts[row.target]:
            raise ConfigError("The mounted credential differs from worker evidence.")
        # Recheck the live cohort immediately before the durable guarded write.
        # Worker restarts retain these same read-only mounted inodes; another
        # container generation requires an explicit operator recreation.
        if loaded_service_receipts(configuration) != receipts:
            raise ConfigError("The consumer cohort changed during confirmation.")
        acknowledge_loaded_credential(
            request_id=request_id,
            consumer=ServiceRole.WEB.value,
            loaded_value=value,
        )
    finally:
        connections.close_all()
        runtime.client.connection_pool.disconnect()
        if runtime.telemetry_client is not None:
            runtime.telemetry_client.connection_pool.disconnect()


def execute_acknowledgement(args):
    """Expose a fixed outcome; never echo supplied identities or credential text."""
    configure_logging()
    try:
        if args.config is None or args.request_id is None:
            raise ConfigError("Configuration and request identity are required.")
        identifier = UUID(args.request_id)
        configuration = load_deployment(args.config)
        with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
            acknowledge_web(configuration, identifier)
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
