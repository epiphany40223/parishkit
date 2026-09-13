"""Atomically reject a failed read and settle only its exact live Task claim.

Cleanup may finish after ordinary source admission closes, but it can only
reject this claim's unpromoted observation, release its lease and record a
verified failure/wait. It cannot load, stage, promote or use a new credential.
"""

from dataclasses import dataclass

import requests
from django.db import connection

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_pagination import IncompleteSourceCollection
from parishkit.parishsoft_transport import SourceTransportError
from parishkit.retry import RetryError
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.observability import Event, correlation
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _bindings
from .canonical import InvalidSourcePayload
from .leases import SourceLeaseUnavailable, release_source, verify_source
from .models import SourceMutationLease
from .outcomes import (
    MAX_AUTOMATIC_ATTEMPTS,
    _request,
    completed_snapshot,
    fallback_state,
)
from .refresh_models import SourceRefreshAttempt
from .rejection import reject_snapshot


@dataclass(frozen=True)
class ReadFailure:
    """Closed classification contains neither exception text nor provider payloads."""

    retry: bool
    contention: bool
    event: Event


def classify_read_failure(error, *, has_source_claim):
    """Classify known read errors only; uncertain drainage/lost ownership propagate."""
    if isinstance(error, (InvalidSourcePayload, IncompleteSourceCollection)):
        return ReadFailure(False, False, Event.SOURCE_INVALID)
    if isinstance(error, SourceLeaseUnavailable):
        return ReadFailure(True, True, Event.SOURCE_HELD)
    if isinstance(error, PermissionError):
        return ReadFailure(True, False, Event.SOURCE_HELD)
    if isinstance(error, CryptographicError):
        # Pre-claim credential intake cannot succeed without an operator repair.
        # A post-load key-inventory rotation may instead require a bounded retry.
        return ReadFailure(has_source_claim, False, Event.SOURCE_CREDENTIAL_FAILED)
    if isinstance(error, ParishSoftAPIError):
        return ReadFailure(
            error.status_code in {429, 500, 502, 503, 504},
            False,
            Event.SOURCE_PROVIDER_FAILED,
        )
    if isinstance(error, RetryError):
        error = error.last_exception
    if isinstance(
        error, (SourceTransportError, requests.ConnectionError, requests.Timeout)
    ):
        return ReadFailure(True, False, Event.SOURCE_PROVIDER_FAILED)
    return None


def settle_failed_read(execution, error, *, source_claim=None):
    """Commit rejection, release, Task disposition and safe diagnostic as one unit.

    Known failure classification originates in the compiled worker's caught
    exception, not a broker/browser flag. A fallback dependency, promoted
    observation, lost fence or unconfirmed drain can never use this path.
    Unknown exceptions propagate without manufacturing a terminal outcome.
    """
    if not isinstance(execution, Execution) or not isinstance(error, BaseException):
        raise TypeError("Source failure requires its execution and actual exception.")
    decision = classify_read_failure(error, has_source_claim=source_claim is not None)
    if decision is None:
        raise error
    if connection.in_atomic_block:
        raise StorageInvariantError(
            "Source failure must own its settlement transaction."
        )
    if source_claim is not None:
        _bindings(execution, source_claim)
    with execution.control.lock, correlation(execution.correlation_id):
        execution.control.check(allow_drain=True)
        with work_transaction():
            row = lock_task_claim(execution.claim)
            status = _status(row)
            _request(status)
            if (
                completed_snapshot(status) is not None
                or fallback_state(status) is not None
            ):
                raise StorageInvariantError(
                    "Completed/dependent work has another outcome owner."
                )
            attempt = (
                SourceRefreshAttempt.objects.select_related("snapshot")
                .filter(
                    task=row,
                    task_fence=execution.claim.fence,
                )
                .first()
            )
            if source_claim is not None:
                verify_source(source_claim)
                if attempt is not None:

                    def admit_rejection(action, snapshot):
                        """Permit only this historical claim's rejection."""
                        _request(status)
                        return action == "reject" and snapshot.pk == attempt.snapshot_id

                    reject_snapshot(
                        attempt.snapshot_id, source_claim, admit=admit_rejection
                    )
                release_source(source_claim)
            else:
                lease = SourceMutationLease.objects.select_for_update().get(
                    singleton=True
                )
                if attempt is not None or (
                    lease.owner_id == row.pk
                    and lease.task_fence == execution.claim.fence
                ):
                    raise StorageInvariantError(
                        "Source failure must retain its source claim."
                    )
            retry = decision.retry and (
                decision.contention or status.attempt < MAX_AUTOMATIC_ATTEMPTS
            )
            action = "retryable_failure" if retry else "permanent_failure"

            def admit_failure(candidate_action, candidate):
                """Verify this classified claim and its rejected/no-effect state."""
                _request(candidate)
                return (
                    candidate_action == action
                    and candidate == status
                    and completed_snapshot(candidate) is None
                    and fallback_state(candidate) is None
                    and not SourceRefreshAttempt.objects.filter(
                        task_id=candidate.run_id,
                        task_fence=candidate.fence,
                    )
                    .exclude(snapshot__state="rejected")
                    .exists()
                )

            result = change_run(
                run_id=row.pk,
                expected_version=row.version,
                action=action,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                fence=execution.claim.fence,
                admit=admit_failure,
                **(
                    {"retry_seconds": min(30 * 2 ** min(status.attempt - 1, 5), 600)}
                    if retry
                    else {}
                ),
            )
            operational(
                decision.event,
                level=("INFO" if decision.event is Event.SOURCE_HELD else "WARNING")
                if retry
                else "CRITICAL",
                schema=ContextKind.TASK,
                context={
                    "task_id": result.run_id,
                    "version": result.version,
                    "outcome": Outcome.RETRY if retry else Outcome.FAILED,
                },
            )
        execution.control.finished.set()
        return result
