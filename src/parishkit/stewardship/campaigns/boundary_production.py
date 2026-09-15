"""Bounded durable boundary production; broker hints never own lifecycle state.

The occurrence UUID is the root TaskRun's execution key. Its domain request is
the campaign UUID, preserving the ordered executor's ability to apply overdue
start and close together. The occurrence's task/fence fields describe execution,
not allocation: they are bound only by a genuinely claimed worker.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .boundary_health import record_lag
from .boundary_revisions import current_boundary
from .credential_models import CampaignCredentialState
from .models import CampaignWorkGate
from .work_locks import work_transaction

TASK_TYPE = "campaign_boundary"


@dataclass(frozen=True)
class BoundaryWork:
    """Opaque durable allocation metadata, never a reusable execution permit."""

    occurrence_id: UUID
    task_root_id: UUID
    kind: str
    due_at: datetime


def boundary_scope(campaign_id):
    """Read current lifecycle gates under work order without using portal dates.

    A boundary must remain executable after its due instant, including after
    close. Delivery pause and activation catch-up do not hold date authority.
    Restore release retains its later owner; this ordinary scope never bypasses
    restore, purge or go-live cleanup.
    """
    scope = _scope(campaign_id)
    if (
        scope.campaign is None
        or scope.runtime.current_campaign_id != campaign_id
        or scope.runtime.mode != "production"
        or scope.runtime.restore_review_required
        or scope.campaign.state not in {"scheduled", "active", "closed"}
        or CampaignWorkGate.objects.filter(campaign_id=campaign_id)
        .exclude(state="released")
        .exists()
        or not CampaignCredentialState.objects.select_for_update()
        .filter(campaign_id=campaign_id, go_live_gate=False)
        .exists()
    ):
        raise PermissionError("Campaign boundary processing is held.")
    return scope


def produce_boundaries(guard):
    """Materialize at most two due occurrences and roots in one owned transaction.

    No queue publication or lifecycle transition occurs here. Creation and its
    execution key commit together, so a restart or lost hint cannot strand an
    occurrence. Repeated scans retain the original root and never implicitly
    retry terminal failure. The ordinary task scanner owns replay publication.
    """
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Boundary production requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Boundary production must own its transaction.")
    guard.check()
    with work_transaction():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
        try:
            scope = boundary_scope(campaign_id)
        except PermissionError:
            return ()
        if scope.campaign.state == "closed":
            return ()
        projection = scope.campaign.active_configuration
        result = []
        for kind, due_at in (
            ("start", projection.starts_at),
            ("close", projection.ends_at),
        ):
            if due_at > scope.instant:
                continue
            guard.check()
            occurrence = current_boundary(
                campaign_id=campaign_id,
                kind=kind,
                due_at=due_at,
                actor_id=None,
                correlation_id=campaign_id,
            )
            if occurrence.state != "pending":
                continue

            def admit(action, status):
                """Admit only this compiled producer's exact campaign allocation."""
                current = boundary_scope(campaign_id)
                return (
                    action == "enqueue"
                    and status.task_type == TASK_TYPE
                    and status.domain_request_id == campaign_id
                    and current.campaign.active_configuration_id == projection.pk
                )

            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=campaign_id,
                actor_id=None,
                correlation_id=occurrence.pk,
                idempotency_key=occurrence.pk,
                admit=admit,
            )
            result.append(BoundaryWork(occurrence.pk, task.root_id, kind, due_at))
            record_lag(occurrence, task, scope.instant)
        guard.check()
        return tuple(result)
