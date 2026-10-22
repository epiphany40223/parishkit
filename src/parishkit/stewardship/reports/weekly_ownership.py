"""Opaque weekly preparation ownership, separate from report and mail payloads.

The scheduler allocates one finite discovery operation. Every worker page binds
to that same root and rechecks the current campaign, revision and Testing epoch.
An obsolete hint may only cancel its own metadata; it cannot revive private
observations discarded by campaign cleanup.

Report emails replaced the Weekly digest (#1786): its scheduler producer is
gone and no gate admits its task, so these helpers serve only the history
the digest left.
"""

from parishkit.stewardship.campaigns.family_schedule_planning import (
    _planning_scope,
)
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .weekly_models import WeeklyDigestPreparation

TASK_TYPE = "weekly_digest_prepare"
TERMINAL_PHASES = ("complete", "cancelled")


def bound_preparation(status):
    """Check persisted root/run metadata instead of trusting a broker payload."""
    require_work_order()
    row = WeeklyDigestPreparation.objects.filter(
        pk=status.domain_request_id, task_id=status.root_id
    ).first()
    if (
        row is None
        or status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=row.task_id,
            task_type=TASK_TYPE,
            domain_request_id=row.pk,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Weekly preparation ownership is unavailable.")
    return row


def current_preparation(row):
    """Return the admitted current scope, or reject an obsolete/held operation."""
    require_work_order()
    scope, epoch = _planning_scope(row.campaign_id, postclose=True)
    if (
        row.phase in TERMINAL_PHASES
        or row.mode != scope.runtime.mode
        or row.rehearsal_epoch_id != (epoch.pk if epoch else None)
        or not CampaignConfiguration.objects.filter(
            pk=row.campaign_configuration_id,
            record_id=row.campaign_id,
            timezone=scope.campaign.active_configuration.timezone,
            start_date=scope.campaign.active_configuration.start_date,
            end_date=scope.campaign.active_configuration.end_date,
        ).exists()
        or not ScheduleDefinition.objects.filter(
            pk=row.definition_id,
            campaign_id=row.campaign_id,
            current_revision_id=row.revision_id,
            kind="weekly_digest",
        ).exists()
    ):
        raise PermissionError("Weekly preparation no longer matches current scope.")
    return scope


def checkpoint_preparation(
    claim, *, phase, cursor=None, occurrence_id=None, cutoff=None
):
    """Advance one fenced page; dates and the selected aggregate never rewind."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    if phase != "cancelled":
        current_preparation(row)
    updated = WeeklyDigestPreparation.objects.filter(
        pk=row.pk, version=row.version
    ).update(
        phase=phase,
        cutoff=cutoff if cutoff is not None else row.cutoff,
        cursor=cursor if cursor is not None else row.cursor,
        occurrence_id=occurrence_id if occurrence_id is not None else row.occurrence_id,
        run_id=claim.run_id,
        task_fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=row.pk,
        version=row.version + 1,
    )
    if updated != 1:
        raise StorageInvariantError("Weekly preparation lost its page version.")
    return WeeklyDigestPreparation.objects.get(pk=row.pk)
