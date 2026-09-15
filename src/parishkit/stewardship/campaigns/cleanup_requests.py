"""Atomic ADM-05 integration port; no public readiness or activation bypass."""

from dataclasses import fields

from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import change_run, retry_failed
from parishkit.stewardship.storage import StaleRecordError

from .cleanup_catalog import iter_inventory, summarize_targets
from .cleanup_manifest import seal_manifest, testing_summary
from .production_models import (
    ProductionCleanupCancellation,
    ProductionCleanupManifest,
    ProductionTransitionEvent,
    ProductionTransitionRequest,
)
from .production_states import ProductionAction as Action
from .production_storage import (
    CleanupInventory,
    ProductionCommand,
    TestingSummary,
    _ids,
    _locked,
    _status,
    begin_transition,
    change_transition,
)
from .work_locks import work_transaction


def begin_cleanup(
    *,
    campaign_id,
    request_key,
    actor_id,
    correlation_id,
    readiness_digest,
    acknowledged_at,
    reauthenticated_at,
    admit,
):
    """Commit acknowledgement, gate, actual aggregate, manifest and Task together.

    The later readiness workflow owns Admin authorization and evidence freshness
    through its admission callback under these locks. It cannot supply invented
    counts or arbitrary deletion targets. Replays use the original aggregate,
    including after deletion, but still recheck that owner's current admission.
    """
    with work_transaction():
        previous = ProductionTransitionRequest.objects.filter(
            campaign_id=campaign_id, request_key=request_key
        ).first()
        if previous is None:
            inventory = summarize_targets(iter_inventory(campaign_id))
            summary = testing_summary(campaign_id, readiness_digest=readiness_digest)
        else:
            inventory = CleanupInventory(
                previous.inventory_digest, previous.inventory_counts
            )
            aggregate = previous.aggregate
            summary = TestingSummary(
                **{
                    field.name: readiness_digest
                    if field.name == "readiness_digest"
                    else getattr(aggregate, field.name)
                    for field in fields(TestingSummary)
                }
            )
        status = begin_transition(
            campaign_id=campaign_id,
            request_key=request_key,
            actor_id=actor_id,
            correlation_id=correlation_id,
            inventory=inventory,
            summary=summary,
            acknowledged_at=acknowledged_at,
            reauthenticated_at=reauthenticated_at,
            admit=admit,
        )
        seal_manifest(status.request_id)
        return status


def request_cancellation(
    *, request_id, command_id, expected_version, actor_id, correlation_id, admit
):
    """Record immutable Admin intent; the running worker stops between batches.

    No Admin impersonates a live worker or rewinds its fence. Waiting/terminal
    tasks can finish immediately; running/abandoned tasks use their ordinary
    worker/recovery owner. The caller rechecks real Admin authority on every
    operation, including replays, through the later web workflow's admission.
    """
    _ids(request_id, command_id, actor_id, correlation_id, expected_version)
    proposal = ProductionCommand(
        Action.CANCEL, command_id, expected_version, actor_id, None, None, ""
    )
    with work_transaction():
        request, campaign = _locked(request_id)
        existing = ProductionCleanupCancellation.objects.filter(request=request).first()
        if admit("request_cancel", campaign, _status(request), proposal) is not True:
            raise PermissionError("Cleanup cancellation is not admitted.")
        if existing is not None:
            if (existing.command_id, existing.actor_id, existing.expected_version) != (
                command_id,
                actor_id,
                expected_version,
            ):
                raise ValueError("Cleanup cancellation already has different intent.")
            if request.state == "cancelled":
                return _status(request)
        else:
            if request.version != expected_version:
                raise StaleRecordError(
                    "Cleanup changed before cancellation was requested."
                )
            ProductionCleanupCancellation.objects.create(
                request=request,
                command_id=command_id,
                expected_version=expected_version,
                actor_id=actor_id,
                correlation_id=correlation_id,
            )
        # Replaying intent also finishes a task that became terminal after the
        # first request. The original intent remains immutable and authoritative.
        task = (
            TaskRun.objects.filter(root_id=request.task_id)
            .order_by("-retry_sequence")
            .first()
        )
        if task.state in {"queued", "retry_wait"}:
            change_run(
                run_id=task.pk,
                action="safe_cancel",
                expected_version=task.version,
                actor_id=actor_id,
                correlation_id=correlation_id,
                admit=lambda kind, status: admit(
                    "cancel_task", campaign, _status(request), proposal
                ),
            )
            request.refresh_from_db()
        elif task.state in {"succeeded", "failed", "cancelled"}:
            return change_transition(
                request_id=request_id,
                action=Action.CANCEL,
                command_id=command_id,
                expected_version=request.version,
                actor_id=actor_id,
                correlation_id=correlation_id,
                admit=admit,
            )
        return _status(request)


def retry_cleanup(
    *, request_id, command_id, expected_version, actor_id, correlation_id, admit
):
    """Allocate an explicit retry child without losing immutable prior progress."""
    _ids(request_id, command_id, actor_id, correlation_id, expected_version)
    proposal = ProductionCommand(
        Action.RETRY_FAILED, command_id, expected_version, actor_id, None, None, ""
    )
    with work_transaction():
        request, campaign = _locked(request_id)
        if ProductionCleanupCancellation.objects.filter(request=request).exists():
            raise PermissionError("A cancelled cleanup cannot be resumed.")
        if admit("retry_cleanup", campaign, _status(request), proposal) is not True:
            raise PermissionError("Explicit cleanup retry is not admitted.")
        if not ProductionCleanupManifest.objects.filter(request=request).exists():
            raise PermissionError("Cleanup retry requires a sealed request.")
        if not ProductionTransitionEvent.objects.filter(
            request=request, command_id=command_id
        ).exists():
            retry_failed(
                run_id=request.run_id,
                command_id=command_id,
                actor_id=actor_id,
                correlation_id=correlation_id,
                admit=lambda kind, status: admit(
                    "retry_task", campaign, _status(request), proposal
                ),
            )
        return change_transition(
            request_id=request_id,
            action=Action.RETRY_FAILED,
            command_id=command_id,
            expected_version=expected_version,
            actor_id=actor_id,
            correlation_id=correlation_id,
            admit=admit,
        )
