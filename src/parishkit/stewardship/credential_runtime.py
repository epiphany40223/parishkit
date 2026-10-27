"""Isolated installer processes and whole-consumer credential confirmation.

No process controls Docker. Rotatable integration credentials are confirmed by
the running consumer itself once its directory mount shows the replacement.
Other credentials still need the operator command, run inside the recreated
service rather than a one-off container that only reopens the current files.
Provider and key-retirement owners supply their validators in later phases.
"""

import logging
import sys
from datetime import timedelta
from threading import Event as StopEvent
from uuid import UUID

from parishkit.config import ConfigError

from .deployment import ServiceRole, load_deployment
from .observability import Event, FailureKind, configure_logging, emit, emit_failure
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


# A request still unacknowledged this long after its installation is overdue
# (#2228): the consumer logs it, at most once an hour per request, and
# Finishing setup asks for the server operator (setup_finishing.OVERDUE_AFTER
# is the same instant).
PICKUP_OVERDUE_AFTER = timedelta(minutes=10)
PICKUP_ALERT_EVERY = timedelta(hours=1)
# When this process last logged each overdue request; in memory only, so a
# restarted consumer logs an overdue request again at once.
_overdue_alerted = {}


def admission_check(target, value):
    """Parse ``value`` as this consumer's re-admission will, or raise.

    Only ParishSoft has an admission-time parse (runtime_background's
    SourceCredential check); a consumer must not acknowledge bytes that its
    next start would refuse (#2228).
    """
    if target == "parishsoft":
        from .source.credentials import SourceCredential

        SourceCredential(value)


def _alert_overdue(row):
    """Log a request this consumer still cannot pick up, once overdue.

    ``row.updated_at`` is the installation instant: an ``awaiting_ack``
    request is not written again until it leaves that state. The line names
    no request, path or credential, only the safe category.
    """
    from .accounts.sessions import database_now

    # The database clock, as Finishing setup's own overdue point uses.
    now = database_now()
    if now - row.updated_at < PICKUP_OVERDUE_AFTER:
        return
    last = _overdue_alerted.get(row.pk)
    if last is not None and now - last < PICKUP_ALERT_EVERY:
        return
    _overdue_alerted[row.pk] = now
    emit(
        Event.INSTALLER_REQUEST_FAILED,
        level=logging.ERROR,
        failure_kind=FailureKind.CREDENTIAL_PICKUP_OVERDUE,
    )


def acknowledge_rotations(configuration, receipts, deferred=frozenset()):
    """Acknowledge replacements this running consumer now reads from its mount.

    Rotatable integration credentials are mounted as their target's read-only
    directory and every task rereads the file, so once the mounted bytes match
    an installed request this process is already using them. Recording the
    acknowledgement here replaces the old recreate-and-run-a-command step,
    for setup's first keys as for later replacements (#2228). The SQL owner
    still binds the consumer's own database login and fingerprint.
    ``receipts`` is this process's published inventory, updated in place so
    the operator command reports the same loaded credentials; ``deferred``
    is what the process was admitted without (BackgroundRuntime.deferred).

    Each request is handled on its own: bytes that are missing, unreadable,
    not yet the installed ones or that would fail re-admission are never
    acknowledged, and once the request is overdue that is logged; a refused
    acknowledgement (an expired request, say) is logged too, and the pass
    goes on to the next request. A deferred or optional credential whose
    file is gone again (setup rolled back) leaves the published receipts.
    """
    from .accounts.credential_installation import acknowledge_loaded_credential
    from .accounts.cryptography import CryptographicError
    from .accounts.key_files import read_private
    from .accounts.metrics_credentials import credential_receipt
    from .accounts.secret_models import (
        CredentialConsumerAcknowledgement,
        SecretReplacementRequest,
    )
    from .consumer_runtime import publish_single_process_receipts
    from .service_boundaries import OPTIONAL_SECRETS, rotating_directories

    consumer = configuration.service_role.value
    targets = rotating_directories(configuration).keys()
    rows = SecretReplacementRequest.objects.filter(
        target__in=targets,
        state="awaiting_ack",
        required_consumers__contains=[consumer],
    ).exclude(
        pk__in=CredentialConsumerAcknowledgement.objects.filter(
            consumer=consumer
        ).values("request_id")
    )
    acknowledged = []
    for row in rows:
        try:
            value = read_private(configuration.secrets[row.target])
            fingerprint = credential_receipt(value, row.target)
            if fingerprint == row.resulting_fingerprint:
                admission_check(row.target, value)
        except CryptographicError:
            fingerprint = None
        if fingerprint != row.resulting_fingerprint:
            # Usually the installer has not renamed this candidate into place
            # yet; past the overdue point it is logged.
            _alert_overdue(row)
            continue
        try:
            acknowledge_loaded_credential(
                request_id=row.pk, consumer=consumer, loaded_value=value
            )
        except Exception as error:
            emit_failure(error, event=Event.INSTALLER_REQUEST_FAILED)
            continue
        receipts[row.target] = fingerprint
        publish_single_process_receipts(configuration, receipts, deferred)
        acknowledged.append(row.pk)
    # After a rollback the installer removed the candidate; this process no
    # longer holds it (operations "Setup credential pickup", item 4). Only a
    # credential that may be absent is dropped; a required one stays and is
    # the admission's to refuse on the next start.
    gone = [
        name
        for name in receipts.keys() & (deferred | OPTIONAL_SECRETS)
        if not configuration.secrets[name].exists()
    ]
    if gone:
        for name in gone:
            del receipts[name]
        publish_single_process_receipts(configuration, receipts, deferred)
    return acknowledged


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
