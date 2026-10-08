"""Atomically reject a failed read and settle only its exact live Task claim.

Cleanup may finish after ordinary source admission closes, but it can only
reject this claim's unpromoted observation, release its lease and record a
verified failure/wait. It cannot load, stage, promote or use a new credential.
"""

import logging
from dataclasses import dataclass

import requests
from django.db import connection

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_pagination import (
    IncompleteSourceCollection,
    ShiftedSourceScan,
    SourceLoadBudgetExceeded,
)
from parishkit.parishsoft_source import SourceOrganizationMismatch
from parishkit.parishsoft_transport import InvalidSourceResponse, SourceTransportError
from parishkit.retry import RetryError, TransientRetryError
from parishkit.stewardship.accounts.authority import AuthorityChanging
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.observability import (
    Event,
    correlation,
    debug_logging_enabled,
    emit,
)
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import _bindings
from .canonical import InvalidSourcePayload
from .drop_counts import record_drop_counts
from .errors import (
    SourceAuthorityChanging,
    SourceCredentialChanged,
    SourceScopeChanged,
)
from .leases import SourceLeaseUnavailable, release_source, verify_source
from .loading import DestructiveSourceChange
from .models import SourceMutationLease
from .outcomes import (
    MAX_AUTOMATIC_ATTEMPTS,
    _request,
    completed_snapshot,
    failure_action,
    fallback_state,
    retry_delay,
)
from .refresh_models import SourceRefreshAttempt
from .rejection import reject_snapshot


@dataclass(frozen=True)
class ReadFailure:
    """Closed classification contains neither exception text nor provider payloads."""

    retry: bool
    contention: bool
    event: Event
    # What failed, a closed word (audit.schemas.FAILURES), and for a
    # ParishSoft error answer its HTTP status, so System logs can say which
    # call failed and why (#633).
    failure: str
    status: int | None = None
    # For a destructive change: the closed measure name and its before/after
    # counts, logged so the operator can see what dropped (see loading.py).
    loss: tuple | None = None
    # For a destructive change: every count the load was checked on
    # (loading.CountCheck), recorded with the refusal (ADM-13).
    checks: tuple = ()


def classify_read_failure(error, *, has_source_claim):
    """Classify known read errors only; uncertain drainage/lost ownership propagate."""
    if debug_logging_enabled():
        # The operational event records only the category; say which check failed.
        logging.getLogger("parishkit.stewardship.debug").debug(
            "source read failure", exc_info=(type(error), error, error.__traceback__)
        )
    seen = set()
    while isinstance(error, RetryError):
        if id(error) in seen:
            return None
        seen.add(id(error))
        error = error.last_exception
    if isinstance(error, SourceOrganizationMismatch):
        return ReadFailure(
            False, False, Event.SOURCE_TENANT_MISMATCH, "organization_mismatch"
        )
    if isinstance(error, DestructiveSourceChange):
        return ReadFailure(
            False,
            False,
            Event.SOURCE_DESTRUCTIVE_CHANGE,
            "destructive_change",
            loss=error.loss,
            checks=error.checks,
        )
    if isinstance(error, ShiftedSourceScan):
        # The provider's paging moved mid-scan (validated 2026-09-28: the same
        # full load failed once and passed on five immediate re-runs). Retry
        # the whole read within the bounded provider-failure allowance.
        return ReadFailure(True, False, Event.SOURCE_PROVIDER_FAILED, "shifted_scan")
    if isinstance(error, SourceLoadBudgetExceeded):
        # ParishSoft answered too slowly for the load to finish in its time
        # (#387): a provider timeout, retried within the same allowance,
        # not invalid data.
        return ReadFailure(
            True, False, Event.SOURCE_PROVIDER_FAILED, "provider_timeout"
        )
    for kind, failure in (
        (InvalidSourcePayload, "invalid_payload"),
        (IncompleteSourceCollection, "incomplete_collection"),
        (InvalidSourceResponse, "invalid_response"),
    ):
        if isinstance(error, kind):
            return ReadFailure(False, False, Event.SOURCE_INVALID, failure)
    if isinstance(error, SourceLeaseUnavailable):
        return ReadFailure(True, True, Event.SOURCE_HELD, "lease_unavailable")
    if isinstance(error, (AuthorityChanging, SourceAuthorityChanging)):
        # A configuration change that did not finish activating while this
        # read waited for it (#429). It is held like a scope change, but as
        # contention: an unfinished change is the installer's to complete,
        # and it must not use up the bounded provider-failure allowance.
        return ReadFailure(True, True, Event.SOURCE_HELD, "configuration_activating")
    if isinstance(error, SourceScopeChanged):
        return ReadFailure(True, False, Event.SOURCE_HELD, "scope_changed")
    if isinstance(error, (CryptographicError, SourceCredentialChanged)):
        # Pre-claim credential intake cannot succeed without an operator repair.
        # A post-load key-inventory rotation may instead require a bounded retry.
        return ReadFailure(
            has_source_claim,
            False,
            Event.SOURCE_CREDENTIAL_FAILED,
            "credential_changed"
            if isinstance(error, SourceCredentialChanged)
            else "credential_unreadable",
        )
    if isinstance(error, ParishSoftAPIError):
        status = error.status_code
        return ReadFailure(
            status in {429, 500, 502, 503, 504},
            False,
            Event.SOURCE_PROVIDER_FAILED,
            "provider_status",
            # Only a real HTTP status is recorded; the log refuses others.
            status=status if type(status) is int and 100 <= status <= 599 else None,
        )
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return ReadFailure(
            True, False, Event.SOURCE_PROVIDER_FAILED, "provider_timeout"
        )
    if isinstance(
        error,
        (
            SourceTransportError,
            requests.ConnectionError,
            requests.Timeout,
            TransientRetryError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return ReadFailure(
            True, False, Event.SOURCE_PROVIDER_FAILED, "provider_unreachable"
        )
    return None


def failure_context(decision, result, *, attempt, action):
    """The ``failure`` context a settled source read failure records (#633).

    ``result`` is the task's new status after ``action`` (``change_run``);
    ``attempt`` is the attempt that failed. A retry says when it runs again
    and how many automatic attempts a provider failure gets; a held read
    (contention) waits without using them. Closed words, numbers and ids
    only: no provider text.
    """
    retry = action == "retryable_failure"
    context = {
        "failure": decision.failure,
        "task_id": result.run_id,
        "version": result.version,
        "attempt": attempt,
        "outcome": Outcome.RETRY
        if retry
        else Outcome.FAILED
        if action == "permanent_failure"
        else Outcome.CANCELLED,
    }
    if retry:
        context["retry_seconds"] = retry_delay(attempt)
        if not decision.contention:
            context["attempt_limit"] = MAX_AUTOMATIC_ATTEMPTS
    if decision.status is not None:
        context["status"] = decision.status
    return context


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
            action = failure_action(
                status.attempt, retry=decision.retry, contention=decision.contention
            )
            retry = action == "retryable_failure"

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
                    {"retry_seconds": retry_delay(status.attempt)}
                    if action == "retryable_failure"
                    else {}
                ),
            )
            operational(
                decision.event,
                level=("INFO" if decision.event is Event.SOURCE_HELD else "WARNING")
                if retry
                else "CRITICAL",
                schema=ContextKind.FAILURE,
                context=failure_context(
                    decision, result, attempt=status.attempt, action=action
                ),
            )
            if decision.checks and attempt is not None:
                # Every count the refused load was checked on, with the
                # refusal itself, so the System health page and a later
                # acceptance read them from this record, not the log.
                record_drop_counts(
                    attempt.pk, decision.checks, actor_id=execution.claim.worker_id
                )
        if decision.loss is not None:
            # The durable event above has no place for this detail, so the
            # process log names the count that fell and its before and after
            # values (counts only, never parish data). It is not debug output.
            emit(
                decision.event,
                level=logging.CRITICAL,
                task_id=result.run_id,
                source_loss=decision.loss,
            )
        execution.control.finished.set()
        return result
