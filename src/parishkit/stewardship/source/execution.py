"""Compiled source-refresh orchestration; broker input remains one Task UUID.

Startup must supply the exact credential path and all owning reconciliation
effects. There is no default successful reconciliation and no runtime service
enablement here. Provider observation runs outside SQL transactions while the
generic dispatcher maintains both independent ownership fences.
"""

from functools import partial
from pathlib import Path

from django.db import connection

from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import verify_refresh_attempt
from .credentials import SourceCredential
from .failures import settle_failed_read
from .fallback import request_full_fallback
from .leases import acquire_source, release_source
from .outcomes import (
    _request,
    admit_refresh_metadata,
    completed_snapshot,
    fallback_state,
    recovery_plan,
)
from .refresh_models import SourceRefreshAttempt
from .refreshing import load_and_stage_attempt
from .rejection import reject_snapshot
from .snapshot_models import SourceCurrent, SourceSnapshot
from .snapshots import promote_snapshot


def refresh_handler(*, credential_path, reconcile):
    """Bind trusted startup dependencies, never caller-selected code or secrets.

    ``reconcile(snapshot, execution, source_claim)`` must apply every required
    owning-domain effect and return exactly True inside the promotion transaction.
    The service must not register this handler before those effects are wired.
    """
    if not isinstance(credential_path, Path) or not callable(reconcile):
        raise TypeError("Source handler requires its private path and real effects.")
    return Handler(
        queue=WorkQueue.GENERAL,
        admit=admit_refresh_metadata,
        execute=partial(_execute, credential_path=credential_path, reconcile=reconcile),
        recover=recovery_plan,
        scope=work_transaction,
    )


def _transition(execution, action, **options):
    """Record a proven outcome in the caller's transaction, not its control flag."""
    row = lock_task_claim(execution.claim)
    return change_run(
        run_id=row.pk,
        expected_version=row.version,
        action=action,
        actor_id=execution.claim.worker_id,
        correlation_id=execution.correlation_id,
        fence=execution.claim.fence,
        admit=admit_refresh_metadata,
        **options,
    )


def _settle(execution, action, *, claim=None):
    """Acknowledge verified historical work even after new-work admission closes."""
    with execution.control.lock:
        execution.control.check(allow_drain=True)
        with work_transaction():
            lock_task_claim(execution.claim)
            if claim is not None:
                release_source(claim)
            _transition(execution, action)
        execution.control.finished.set()


def _existing_outcome(execution):
    """A linked full result or prior promotion never starts another provider scan."""
    with execution.control.lock:
        with work_transaction():
            execution.check()
            status = _status(lock_task_claim(execution.claim))
            _request(status)
            if completed_snapshot(status) is not None:
                action = "complete"
            else:
                dependency = fallback_state(status)
                action = {
                    "failed": "permanent_failure",
                    "cancelled": "safe_cancel",
                }.get(dependency)
                if dependency == "pending":
                    _transition(execution, "retryable_failure", retry_seconds=30)
                    action = "waited"
            if action is not None and action != "waited":
                _transition(execution, action)
        if action is not None:
            execution.control.finished.set()
            return True
    return False


def _park_fallback(execution, *, claim):
    """Atomically reject an incomplete delta, link full work, release and wait.

    This is new work, so it requires current admission. Configuration change
    rolls everything back and the caller can only use narrow failed-read cleanup.
    """
    with execution.control.lock:
        with execution.effect():
            attempt = SourceRefreshAttempt.objects.get(
                task_id=execution.claim.run_id, task_fence=execution.claim.fence
            )

            def admit(action, snapshot):
                """Only this live concrete attempt may become a rejected delta."""
                current = verify_refresh_attempt(attempt.pk, execution, claim)
                return action == "reject" and snapshot.pk == current.snapshot_id

            reject_snapshot(attempt.snapshot_id, claim, admit=admit)
            request_full_fallback(execution, attempt_id=attempt.pk)
            release_source(claim)
            _transition(execution, "retryable_failure", retry_seconds=30)
        execution.control.finished.set()


def _prepare(execution):
    """Resolve current scope or atomically park an unseeded delta before reads."""
    with execution.control.lock:
        with execution.effect():
            status = _status(lock_task_claim(execution.claim))
            request = _request(status)
            missing_base = (
                request.kind == "delta"
                and SourceCurrent.objects.get(singleton=True).snapshot_id is None
            )
            if missing_base:
                request_full_fallback(execution)
                _transition(execution, "retryable_failure", retry_seconds=30)
        if missing_base:
            execution.control.finished.set()
            return None
        return request


def _observe(execution, claim, credential, reconcile):
    """Promote only a validated exact attempt and its required atomic effects."""
    snapshot = load_and_stage_attempt(execution, claim, credential)
    _retire_drained_staging(execution, claim, snapshot)
    total = sum(snapshot.counts.values())
    execution.progress(total, total, phase=TaskPhase.PROMOTING)
    with execution.effect():
        attempt = SourceRefreshAttempt.objects.get(snapshot=snapshot)

        def admit(action, candidate):
            """Repeat current tenant/window/key and exact attempt fence checks."""
            current = verify_refresh_attempt(attempt.pk, execution, claim)
            return action == "promote" and candidate.pk == current.snapshot_id

        return promote_snapshot(
            snapshot.pk,
            claim,
            admit=admit,
            reconcile=lambda current: reconcile(current, execution, claim),
        )


def _retire_drained_staging(execution, claim, snapshot):
    """Reject older unpromoted observations in bounded fenced cleanup batches.

    Successful acquisition proves every lower source fence has drained. Retain
    each old manifest and its original provenance; never reuse its payload or
    mark the old Task complete. Current attempt/key admission is still required.
    """
    with execution.effect():
        attempt = SourceRefreshAttempt.objects.get(snapshot=snapshot)
    while True:
        with execution.effect():
            verify_refresh_attempt(attempt.pk, execution, claim)
            candidates = list(
                SourceSnapshot.objects.filter(
                    state__in=("staging", "ready"), source_fence__lt=claim.fence
                )
                .order_by("source_fence", "id")
                .values_list("pk", flat=True)[:100]
            )
            for identifier in candidates:

                def admit(action, previous):
                    """A later live bound read may retire only older drained staging."""
                    verify_refresh_attempt(attempt.pk, execution, claim)
                    return action == "reject" and previous.source_fence < claim.fence

                reject_snapshot(identifier, claim, admit=admit)
        if len(candidates) < 100:
            return


def _execute(execution, *, credential_path, reconcile):
    """Run one maintained claim; uncertain faults remain for fenced recovery."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Source execution requires a maintained lifetime.")
    with correlation(execution.correlation_id):
        if _existing_outcome(execution):
            return
        claim = None
        try:
            request = _prepare(execution)
            if request is None:
                return
            credential = SourceCredential.read(credential_path)
            with execution.effect():
                claim = acquire_source(
                    task_id=execution.claim.run_id,
                    task_fence=execution.claim.fence,
                    worker_id=execution.claim.worker_id,
                    phase=request.kind,
                )
            with execution.maintain_source(claim):
                try:
                    _observe(execution, claim, credential, reconcile)
                except ChangeFeedIncomplete:
                    _park_fallback(execution, claim=claim)
                    return
            # Renewal no longer includes the source lease before release.
            # A crash here preserves promoted proof for metadata-only recovery.
            _settle(execution, "complete", claim=claim)
        except Exception as error:
            # Unknown/invariant/lost-ownership errors propagate. Fatal helper
            # drainage is a BaseException and deliberately never enters here.
            settle_failed_read(execution, error, source_claim=claim)
