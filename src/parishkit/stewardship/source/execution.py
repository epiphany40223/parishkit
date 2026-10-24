"""Compiled source-refresh orchestration; broker input remains one Task UUID.

Startup must supply the exact credential path and all owning reconciliation
effects. There is no default successful reconciliation and no runtime service
enablement here. Provider observation runs outside SQL transactions while the
generic dispatcher maintains both independent ownership fences.
"""

import json
import logging
from functools import partial
from pathlib import Path
from time import monotonic

from django.db import connection

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler
from parishkit.stewardship.jobs.lifetime import extend_lease
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, change_run
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StorageInvariantError

from .attempts import begin_refresh_attempt, source_step, verify_refresh_attempt
from .compaction import compact_before_refresh
from .credentials import SourceCredential
from .drop_acceptance import require_live
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
from .snapshots import mark_retired, promote_snapshot

# The task and source leases committed before a promotion: longer than its
# 120-second statement budget, as boundary work's 300 (#386, M4).
PROMOTION_LEASE_SECONDS = 300


def refresh_handler(*, credential_path, reconcile):
    """Bind trusted startup dependencies, never caller-selected code or secrets.

    ``reconcile(snapshot, execution, source_claim)`` must apply every required
    owning-domain effect and return exactly True inside the promotion transaction.
    The service must not register this handler before those effects are wired.
    """
    if not isinstance(credential_path, Path) or not callable(reconcile):
        raise TypeError("Source handler requires its private path and real effects.")
    return Handler(
        queue=WorkQueue.SOURCE,
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
    """Atomically reject a delta's empty attempt, link full work, release and wait.

    Quick updates (delta refreshes) were removed (#1147): nothing creates a
    delta request any more, but one queued before the upgrade still runs
    here. It reads nothing from ParishSoft: its attempt is rejected at once
    and it waits for one full refresh, through the fallback edge the
    database already guards, and completes when that full refresh promotes.

    This is new work, so it requires current admission. Configuration change
    rolls everything back and the caller can only use narrow failed-read cleanup.
    """
    with execution.control.lock:
        with execution.effect():
            attempt = SourceRefreshAttempt.objects.get(
                task_id=execution.claim.run_id, task_fence=execution.claim.fence
            )

            def admit(action, snapshot):
                """Only this live concrete attempt may become a retired delta."""
                current = verify_refresh_attempt(attempt.pk, execution, claim)
                return (
                    action in ("stage", "reject") and snapshot.pk == current.snapshot_id
                )

            # Marked first, so data age never reads this as a failed read.
            mark_retired(attempt.snapshot_id, claim, admit=admit)
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
    prepared = _prepare_promotion(execution, claim, snapshot, reconcile)
    total = sum(snapshot.counts.values())
    execution.progress(total, total, phase=TaskPhase.PROMOTING)
    # The promotion's statement budget (120 s) is longer than what is left of
    # the 60-second task and source leases, and the renewal thread waits on
    # the control lock this effect holds: commit leases that outlast the
    # promotion first (#386, M4), as boundary work does. The lock (an RLock)
    # is held from the extension through the effect, so no renewal can slip
    # in between; later renewals never ask for less than the extension left.
    lock = execution.control.lock
    with lock:
        extend_lease(execution, PROMOTION_LEASE_SECONDS)
        started = monotonic()
        promoted = _promote(execution, claim, snapshot, reconcile, prepared)
    _promotion_timing(started)
    return promoted


def _prepare_promotion(execution, claim, snapshot, reconcile):
    """Derive promotion's read-only inputs in a source step, off the lock (#895).

    Only a reconciler with a ``prepare`` hook (the compiled effects) has
    any; a plain reconcile callable gets None and promotes as before. The
    step is the fenced, admitted, lock-free step staging and validation use,
    so its row locks are task root, task run, lease, then the snapshot.
    """
    prepare = getattr(reconcile, "prepare", None)
    if prepare is None:
        return None
    with source_step(execution):
        return prepare(snapshot.pk, claim)


def _promote(execution, claim, snapshot, reconcile, prepared=None):
    """Promote the staged snapshot in one effect (see ``_observe``).

    ``prepared`` (from ``_prepare_promotion``) is handed to the reconciler,
    which uses it only for this very snapshot.
    """
    with execution.effect():
        attempt = SourceRefreshAttempt.objects.get(snapshot=snapshot)

        def admit(action, candidate):
            """Repeat current tenant/window/key and exact attempt fence checks.

            A refresh loaded under an Administrator's one-time acceptance
            (#530) is promoted only while that acceptance is still live,
            checked here under the work-order lock.
            """
            current = verify_refresh_attempt(attempt.pk, execution, claim)
            require_live(candidate.cursor)
            return action == "promote" and candidate.pk == current.snapshot_id

        return promote_snapshot(
            snapshot.pk,
            claim,
            admit=admit,
            reconcile=lambda current: (
                reconcile(current, execution, claim)
                if prepared is None
                else reconcile(current, execution, claim, prepared=prepared)
            ),
        )


def _promotion_timing(started):
    """Debug-log how long the promotion's transaction took, commit included.

    Observation only, like the lease renewal's timing line: a DEBUG line,
    built only when debug logging is enabled (as for a rollout check), and
    any failure is swallowed.
    """
    try:
        debug = logging.getLogger("parishkit.stewardship.debug")
        if debug.isEnabledFor(logging.DEBUG):
            debug.debug(
                "source promotion timing: %s",
                json.dumps({"elapsed_ms": round((monotonic() - started) * 1000)}),
            )
    except Exception:  # noqa: S110 - observation must never affect the task
        pass


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
            # Reclaim corpora the previous refreshes superseded before taking
            # this refresh's own lease. At this point every earlier provider
            # request's safety deadline has long passed, so the compaction
            # lease is available; retention never fails the refresh.
            compact_before_refresh(execution)
            with execution.effect():
                claim = acquire_source(
                    task_id=execution.claim.run_id,
                    task_fence=execution.claim.fence,
                    worker_id=execution.claim.worker_id,
                    phase=request.kind,
                )
            with execution.maintain_source(claim):
                if request.kind == "delta":
                    # A delta request from before #1147 (see _park_fallback).
                    begin_refresh_attempt(execution, claim, credential)
                    _park_fallback(execution, claim=claim)
                    return
                _observe(execution, claim, credential, reconcile)
            # Renewal no longer includes the source lease before release.
            # A crash here preserves promoted proof for metadata-only recovery.
            _settle(execution, "complete", claim=claim)
        except Exception as error:
            # Unknown/invariant/lost-ownership errors propagate. Fatal helper
            # drainage is a BaseException and deliberately never enters here.
            settle_failed_read(execution, error, source_claim=claim)
