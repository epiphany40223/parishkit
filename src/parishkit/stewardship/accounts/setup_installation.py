"""Installer-only recovery of an expired setup's selected, unapplied candidate.

The web service never writes the authority mount. Its original-login cancellation
or the scheduler's verified expiry first fences SetupAttempt. This owner consumes
that durable proof under installation serialization; it cannot cancel a live
attempt or infer cancellation merely from a YAML/database mismatch.
"""

from uuid import UUID

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StorageInvariantError

from .configuration_installation import DatabaseMaterializer
from .configuration_requests import _status
from .runtime_models import ConfigurationActivation, SystemConfiguration
from .setup_models import (
    SetupAttempt,
    SetupConfigurationAbort,
    SetupConfigurationIntent,
)
from .setup_policy import SetupState


def abort_setup_configuration(store, *, attempt_id, correlation_id):
    """Journal before restoring files; a crashed restore resumes the same decision.

    This is an internal installer operation, not an authenticated cancellation
    endpoint. Original-attempt expiry is independently enforced in PostgreSQL.
    Caller UUIDs select receipts, never grant authority or choose a predecessor.
    """
    if any(not isinstance(value, UUID) for value in (attempt_id, correlation_id)):
        raise TypeError("Exact setup and correlation UUIDs are required.")
    intent = (
        SetupConfigurationIntent.objects.select_related("request__base")
        .filter(attempt_id=attempt_id)
        .first()
    )
    if intent is None:
        raise LookupError("Setup finalization is unavailable.")
    materializer = DatabaseMaterializer(
        store,
        request=intent.request,
        actor_id=intent.request.actor_id,
        correlation_id=correlation_id,
    )
    with materializer.lock():
        recovered = recover_setup_abort(materializer)
        if recovered is not None:
            return recovered
        _journal(materializer, intent)
        return recover_setup_abort(materializer)


def _journal(materializer, intent):
    """Commit exact expiry proof before touching the selected manifest."""
    materializer._check()
    if not materializer.is_prepared(intent.request.candidate_digest):
        raise StorageInvariantError("Setup abort requires exact prepared data.")
    with work_transaction():
        runtime = SystemConfiguration.objects.select_for_update().get()
        # The shared work lock serializes lifecycle changes. Once expired,
        # this record is SQL-immutable; the installer needs no UPDATE grant.
        attempt = SetupAttempt.objects.get(pk=intent.attempt_id)
        if (
            attempt.state != SetupState.EXPIRED
            or runtime.active_configuration_id != intent.request.base_id
            or attempt.base_id != intent.request.base_id
            or ConfigurationActivation.objects.filter(request=intent.request).exists()
        ):
            raise StorageInvariantError("Only expired, unapplied setup may abort.")
        selected = materializer.store.active()
        if selected is None or (selected.version_id, selected.digest) not in {
            (intent.request.base_id, intent.request.base.digest),
            (intent.request.candidate_version_id, intent.request.candidate_digest),
        }:
            raise StorageInvariantError("Setup abort has unrelated YAML authority.")
        return SetupConfigurationAbort.objects.create(
            intent=intent,
            reason=attempt.expiry_reason,
            actor_id=attempt.actor_id,
            correlation_id=materializer.correlation_id,
        )


def recover_setup_abort(materializer):
    """Honor original-attempt expiry before any ordinary forward recovery."""
    request = materializer.request
    if request is None or request.request_schema != "initial-setup-patch-v7":
        return None
    materializer._check()
    abort = (
        SetupConfigurationAbort.objects.select_related("intent__attempt")
        .filter(intent__request=request)
        .first()
    )
    if abort is None:
        intent = SetupConfigurationIntent.objects.select_related(
            "attempt", "request__base"
        ).get(request=request)
        if intent.attempt.state != SetupState.EXPIRED:
            return None
        state = _status(request).state
        if state in {"staged", "validating"} and not materializer.is_prepared(
            request.candidate_digest
        ):
            # No prepared candidate can legitimately have been selected. End
            # this queue item without inventing an abort/manifest restoration.
            selected = materializer.store.active()
            if (
                selected is None
                or selected.digest != materializer.active_digest()
                or selected.version_id == request.candidate_version_id
                or ConfigurationActivation.objects.filter(request=request).exists()
            ):
                raise StorageInvariantError(
                    "Unprepared setup has unexpected authority."
                )
            if state == "staged":
                materializer.checkpoint("validating")
            materializer.checkpoint("failed", failure_code="invalid_candidate")
            return _status(request)
        if state not in {
            "validating",
            "prepared",
            "yaml_activated",
        } or not materializer.is_prepared(request.candidate_digest):
            return None
        abort = _journal(materializer, intent)
    attempt = abort.intent.attempt
    if (
        attempt.state != SetupState.EXPIRED
        or attempt.expiry_reason != abort.reason
        or attempt.base_id != request.base_id
        or attempt.owner_id != request.actor_id
    ):
        raise StorageInvariantError("Setup abort lost its original-attempt proof.")
    materializer.restore_aborted_candidate()
    return _status(request)
