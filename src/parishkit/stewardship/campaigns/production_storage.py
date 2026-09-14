"""Internal go-live journal transactions, with no activation or cleanup worker.

The later BG-03/ADM-05 owners verify exact inventory/readiness, Admin intent and
deletion scope. These primitives require their callbacks under ordered locks;
no callable, UUID or state name is accepted by an HTTP or queue entry point.
"""

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID, uuid4

from django.utils import timezone

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_validation import identifier
from parishkit.stewardship.jobs.storage import change_run, enqueue
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .credential_models import CampaignCredentialState
from .models import Campaign
from .production_models import (
    ProductionCleanupCheckpoint,
    ProductionTransitionEvent,
    ProductionTransitionRequest,
    TestingAggregate,
)
from .production_states import ProductionAction, ProductionState, production_target
from .rehearsals import invalidate_rehearsal, release_rehearsal_gate
from .work_locks import work_transaction


def _digest(value):
    """Require a fingerprint, never arbitrary private source data in its place."""
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Invalid Production evidence fingerprint.")


def _counts(value):
    """Freeze bounded owner category counts, excluding IDs, notes and row payloads."""
    if type(value) not in (dict, MappingProxyType) or len(value) > 100:
        raise ValueError("Invalid cleanup counts.")
    result = dict(value)
    for name, count in result.items():
        if (
            type(name) is not str
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None
            or type(count) is not int
            or count < 0
        ):
            raise ValueError("Invalid cleanup counts.")
    if sum(result.values()) > 9223372036854775807:
        raise ValueError("Cleanup counts exceed storage bounds.")
    return MappingProxyType(result)


@dataclass(frozen=True)
class CleanupInventory:
    """The inventory owner supplies exact category counts and its manifest digest."""

    digest: str
    counts: Mapping[str, int]

    def __post_init__(self):
        """Detach and validate the non-sensitive summary before touching SQL."""
        _digest(self.digest)
        object.__setattr__(self, "counts", _counts(self.counts))

    @property
    def total(self):
        return sum(self.counts.values())


@dataclass(frozen=True)
class TestingSummary:
    """Pre-cleanup totals; no Family identity or email detail survives in this row."""

    __test__ = False
    readiness_digest: str
    submissions: int
    families: int
    messages: int
    delivered: int
    failed: int
    cancelled: int

    def __post_init__(self):
        """Only exact terminal message totals qualify as aggregate evidence."""
        _digest(self.readiness_digest)
        values = asdict(self)
        values.pop("readiness_digest")
        _counts(values)
        if (
            self.families > self.submissions
            or self.messages != self.delivered + self.failed + self.cancelled
        ):
            raise ValueError("Invalid Testing aggregate counts.")


@dataclass(frozen=True)
class ProductionStatus:
    """Minimal immutable admission/status view with no inventory detail or PII."""

    request_id: UUID
    campaign_id: UUID
    state: ProductionState
    version: int
    task_id: UUID
    run_id: UUID | None
    task_fence: int | None
    worker_id: UUID | None
    processed_count: int
    inventory_total: int
    checkpoint_sequence: int


def _status(request):
    """Freeze current control metadata, never pass a mutable ORM row to an owner."""
    return ProductionStatus(
        request.pk,
        request.campaign_id,
        ProductionState(request.state),
        request.version,
        request.task_id,
        request.run_id,
        request.task_fence,
        request.worker_id,
        request.processed_count,
        request.inventory_total,
        request.checkpoint_sequence,
    )


def _admit(callback, action, campaign, status):
    """Every replay also needs fresh current-scope permission and readiness proof."""
    if not callable(callback):
        raise TypeError("Production admission callback is required.")
    if callback(action, campaign, status) is not True:
        raise PermissionError("Production operation is not admitted.")


def begin_transition(
    *,
    campaign_id,
    request_key,
    actor_id,
    correlation_id,
    inventory,
    summary,
    acknowledged_at,
    reauthenticated_at,
    admit,
):
    """Atomically record intent, invalidate rehearsal authority and own the gate."""
    for value in (campaign_id, request_key, actor_id, correlation_id):
        identifier(value)
    if not isinstance(inventory, CleanupInventory) or not isinstance(
        summary, TestingSummary
    ):
        raise TypeError("Typed Production inventory and summary are required.")
    for value in (acknowledged_at, reauthenticated_at):
        if not isinstance(value, datetime) or timezone.is_naive(value):
            raise ValueError("Production acknowledgement requires aware timestamps.")
    with correlation(correlation_id), work_transaction():
        runtime = SystemConfiguration.objects.select_for_update().get()
        campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
        scope = CampaignCredentialState.objects.select_for_update().get(
            campaign=campaign
        )
        existing = ProductionTransitionRequest.objects.filter(
            campaign=campaign, request_key=request_key
        ).first()
        if existing is not None:
            if (
                existing.initiated_by_id != actor_id
                or existing.inventory_digest != inventory.digest
                or existing.inventory_counts != dict(inventory.counts)
                or existing.acknowledged_at != acknowledged_at
                or existing.reauthenticated_at != reauthenticated_at
                or any(
                    getattr(existing.aggregate, name) != value
                    for name, value in asdict(summary).items()
                )
            ):
                raise ValueError("Production request key is already bound.")
            _admit(admit, "create_replay", campaign, _status(existing))
            return _status(existing)
        if (
            runtime.mode != "testing"
            or runtime.current_campaign_id != campaign.pk
            or scope.go_live_gate
        ):
            raise StorageInvariantError(
                "Production cleanup is not currently available."
            )
        _admit(admit, "create", campaign, None)
        epoch_id = invalidate_rehearsal(
            campaign_id=campaign_id,
            admit=lambda row: admit("invalidate_rehearsal", row, None),
        )
        scope.refresh_from_db()
        aggregate = TestingAggregate.objects.create(
            campaign=campaign,
            inventory_digest=inventory.digest,
            actor_id=actor_id,
            correlation_id=correlation_id,
            **asdict(summary),
        )
        request_id = uuid4()
        task = enqueue(
            task_type="production_cleanup",
            domain_request_id=request_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=request_id,
            admit=lambda action, status: admit("create_task", campaign, None),
        )
        request = ProductionTransitionRequest.objects.create(
            id=request_id,
            campaign=campaign,
            initiated_by_id=actor_id,
            request_key=request_key,
            configuration_id=runtime.active_configuration_id,
            aggregate=aggregate,
            inventory_digest=inventory.digest,
            inventory_counts=dict(inventory.counts),
            inventory_total=inventory.total,
            gate_version=scope.version,
            invalidated_epoch_id=epoch_id,
            task_id=task.run_id,
            acknowledged_at=acknowledged_at,
            reauthenticated_at=reauthenticated_at,
            command_id=request_key,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        return _status(request)


def change_transition(
    *,
    request_id,
    action,
    command_id,
    expected_version,
    actor_id,
    correlation_id,
    admit,
    run_id=None,
    task_fence=None,
    failure_reason="",
):
    """Advance cleanup state only; activation needs the later complete owner."""
    _ids(request_id, command_id, actor_id, correlation_id, expected_version)
    if not isinstance(action, ProductionAction):
        raise TypeError("A typed Production action is required.")
    if action is ProductionAction.ACTIVATE:
        raise StorageInvariantError(
            "Production activation is not exposed by this journal."
        )
    claim_action = action in (ProductionAction.START, ProductionAction.RECOVER)
    if claim_action:
        identifier(run_id)
        if type(task_fence) is not int or task_fence < 1:
            raise ValueError("Invalid cleanup task fence.")
    elif run_id is not None or task_fence is not None:
        raise ValueError("Unexpected cleanup claim binding.")
    if type(failure_reason) is not str or (
        failure_reason and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", failure_reason) is None
    ):
        raise ValueError("Invalid cleanup failure reason.")
    if bool(failure_reason) != (
        action in (ProductionAction.FAIL, ProductionAction.RETRY_LATER)
    ):
        raise ValueError(
            "A cleanup failure reason belongs only to a failed or delayed operation."
        )
    with correlation(correlation_id), work_transaction():
        request, campaign = _locked(request_id)
        previous = ProductionTransitionEvent.objects.filter(
            request=request, command_id=command_id
        ).first()
        if previous is not None:
            if (
                previous.action != action.value
                or previous.actor_id != actor_id
                or previous.version != expected_version + 1
                or previous.snapshot["failure_reason"] != failure_reason
                or (
                    claim_action
                    and (
                        previous.snapshot["run_id"] != str(run_id)
                        or previous.snapshot["task_fence"] != task_fence
                    )
                )
            ):
                raise ValueError("Production command is already bound.")
            _admit(admit, "transition_replay", campaign, _status(request))
            return _status(request)
        if request.version != expected_version:
            raise StaleRecordError("Production request version changed.")
        target = production_target(ProductionState(request.state), action)
        _admit(admit, action, campaign, _status(request))
        if action is ProductionAction.CANCEL:
            current = (
                TaskRun.objects.filter(root_id=request.task_id)
                .order_by("-retry_sequence")
                .first()
            )
            if current.state in ("queued", "retry_wait"):
                change_run(
                    run_id=current.pk,
                    action="safe_cancel",
                    expected_version=current.version,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    admit=lambda kind, status: admit(
                        "cancel_task", campaign, _status(request)
                    ),
                )
        values = dict(
            state=target.value,
            action=action.value,
            command_id=command_id,
            version=request.version + 1,
            actor_id=actor_id,
            correlation_id=correlation_id,
            failure_reason=failure_reason,
        )
        if claim_action:
            TaskRun.objects.select_for_update().get(pk=run_id)
            values.update(run_id=run_id, task_fence=task_fence, worker_id=actor_id)
        ProductionTransitionRequest.objects.filter(pk=request_id).update(**values)
        request.refresh_from_db()
        if action is ProductionAction.CANCEL:
            release_rehearsal_gate(
                campaign_id=campaign.pk,
                admit=lambda row: admit("release_gate", row, _status(request)),
            )
        return _status(request)


def checkpoint_cleanup(
    *,
    request_id,
    command_id,
    expected_version,
    actor_id,
    correlation_id,
    batch,
    admit,
    apply_batch,
):
    """Commit one owner-selected Testing batch and its checkpoint together.

    apply_batch(status, batch) must recheck exact inventory membership and scope,
    perform bounded database deletions only, and return exactly True. A failure,
    stale worker, invalid count or missing progress rolls back all those effects.
    """
    _ids(request_id, command_id, actor_id, correlation_id, expected_version)
    if (
        not isinstance(batch, CleanupInventory)
        or not 1 <= batch.total <= 1000
        or not callable(apply_batch)
    ):
        raise ValueError(
            "Cleanup requires a bounded batch and its owning deletion operation."
        )
    with correlation(correlation_id), work_transaction():
        request, campaign = _locked(request_id)
        previous = ProductionCleanupCheckpoint.objects.filter(
            request=request, command_id=command_id
        ).first()
        if previous is not None:
            if (
                previous.actor_id != actor_id
                or previous.batch_digest != batch.digest
                or previous.counts != dict(batch.counts)
            ):
                raise ValueError("Cleanup command is already bound.")
            _admit(admit, "checkpoint_replay", campaign, _status(request))
            return _status(request)
        if request.version != expected_version:
            raise StaleRecordError("Production request version changed.")
        if request.state != "cleanup_running":
            raise StorageInvariantError("Cleanup requires a running request.")
        _admit(admit, "checkpoint", campaign, _status(request))
        if apply_batch(_status(request), batch) is not True:
            raise PermissionError("Cleanup batch is not admitted.")
        ProductionCleanupCheckpoint.objects.create(
            request=request,
            command_id=command_id,
            sequence=request.checkpoint_sequence + 1,
            counts=dict(batch.counts),
            deleted_count=batch.total,
            batch_digest=batch.digest,
            run_id=request.run_id,
            task_fence=request.task_fence,
            worker_id=request.worker_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        ProductionTransitionRequest.objects.filter(pk=request_id).update(
            action="checkpoint",
            command_id=command_id,
            version=request.version + 1,
            checkpoint_sequence=request.checkpoint_sequence + 1,
            processed_count=request.processed_count + batch.total,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        request.refresh_from_db()
        return _status(request)


def _ids(request_id, command_id, actor_id, correlation_id, version):
    """Validate attribution and optimistic tokens before obtaining any database lock."""
    for value in (request_id, command_id, actor_id, correlation_id):
        identifier(value)
    if type(version) is not int or version < 1:
        raise ValueError("Invalid Production request version.")


def _locked(request_id):
    """Join campaign and retry-root order before locking the domain request."""
    initial = ProductionTransitionRequest.objects.get(pk=request_id)
    campaign = Campaign.objects.select_for_update().get(pk=initial.campaign_id)
    TaskRun.objects.select_for_update().get(pk=initial.task_id)
    request = ProductionTransitionRequest.objects.select_for_update().get(pk=request_id)
    return request, campaign
