"""Closed checkpoint command; SQL owns target selection and private deletion."""

from uuid import UUID, uuid4

from parishkit.stewardship.jobs.ownership import (
    TaskClaim,
    TaskOwnershipLost,
    lock_task_claim,
)
from parishkit.stewardship.storage import StorageInvariantError

from .production_models import ProductionCleanupCheckpoint, ProductionCleanupManifest
from .production_storage import _locked, _status
from .work_locks import require_work_order


def apply_checkpoint(request_id, claim, *, maximum=500, command_id=None):
    """Apply or replay one exact fenced batch in the caller's admitted transaction.

    No table names, target identifiers, fingerprints or actual deleted counts
    are caller-selected. The INSERT trigger derives all of them. The returned
    status reflects committed-in-this-transaction progress, not a queue hint.
    """
    require_work_order()
    if not isinstance(claim, TaskClaim):
        raise TaskOwnershipLost("An exact task claim is required.")
    if (
        not isinstance(request_id, UUID)
        or type(maximum) is not int
        or not 2 <= maximum <= 1000
    ):
        raise ValueError("Cleanup requires a canonical request and bounded budget.")
    command_id = uuid4() if command_id is None else command_id
    if not isinstance(command_id, UUID):
        raise ValueError("Cleanup requires a canonical command identity.")
    request, _ = _locked(request_id, run_id=claim.run_id)
    task = lock_task_claim(claim)
    if (
        request.state != "cleanup_running"
        or request.run_id != task.pk
        or request.task_fence != task.fence
        or request.worker_id != task.worker_id
        or not ProductionCleanupManifest.objects.filter(request=request).exists()
    ):
        raise PermissionError("Cleanup requires its exact sealed running request.")
    previous = ProductionCleanupCheckpoint.objects.filter(
        request=request, command_id=command_id
    ).first()
    if previous is not None:
        if (
            previous.run_id != task.pk
            or previous.task_fence != task.fence
            or previous.worker_id != task.worker_id
        ):
            raise StorageInvariantError("Cleanup command belongs to another claim.")
        return _status(request)
    ProductionCleanupCheckpoint.objects.create(
        request=request,
        command_id=command_id,
        sequence=request.checkpoint_sequence + 1,
        counts={},
        deleted_count=maximum,
        batch_digest="0" * 64,
        run_id=task.pk,
        task_fence=task.fence,
        worker_id=task.worker_id,
        actor_id=task.worker_id,
        correlation_id=task.correlation_id,
    )
    request.refresh_from_db()
    lock_task_claim(claim)
    return _status(request)
