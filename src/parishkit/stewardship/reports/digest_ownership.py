"""Opaque daily preparation ownership, separate from report and mail payloads.

The scheduler allocates one finite discovery operation. Every worker page binds
to that same root and rechecks the current campaign, revision and Testing epoch.
An obsolete hint may only cancel its own metadata; it cannot revive private
observations discarded by campaign cleanup.
"""

from uuid import UUID, uuid4

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.family_schedule_planning import _planning_scope
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .digest_models import DailyDigestPreparation

TASK_TYPE = "daily_digest_prepare"
TERMINAL_PHASES = ("complete", "cancelled")


def bound_preparation(status):
    """Check persisted root/run metadata instead of trusting a broker payload."""
    require_work_order()
    row = DailyDigestPreparation.objects.filter(
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
        raise PermissionError("Daily preparation ownership is unavailable.")
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
            kind="daily_digest",
        ).exists()
    ):
        raise PermissionError("Daily preparation no longer matches current scope.")
    return scope


def checkpoint_preparation(
    claim, *, phase, cursor=None, occurrence_id=None, cutoff=None
):
    """Advance one fenced page; dates and the selected aggregate never rewind."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    if phase != "cancelled":
        current_preparation(row)
    updated = DailyDigestPreparation.objects.filter(
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
        raise StorageInvariantError("Daily preparation lost its page version.")
    return DailyDigestPreparation.objects.get(pk=row.pk)


class DailyDigestProducer:
    """Allocate one metadata root for pending daily work, never render or send."""

    def __init__(self, worker_id):
        """Require an opaque scheduler identity, not an interactive user session."""
        if not isinstance(worker_id, UUID):
            raise ValueError("Daily production requires a scheduler identity.")
        self.worker_id = worker_id

    def __call__(self, guard):
        """Original-slot production runs first; the worker completes the full range."""
        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Daily production requires scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError("Daily production must own its transaction.")
        guard.check()
        with work_transaction():
            campaign_id = SystemConfiguration.objects.values_list(
                "current_campaign_id", flat=True
            ).first()
            try:
                scope, epoch = _planning_scope(campaign_id, postclose=True)
            except PermissionError:
                return ()
            definition = ScheduleDefinition.objects.filter(
                campaign_id=campaign_id,
                kind="daily_digest",
                current_revision__isnull=False,
            ).first()
            if (
                definition is None
                or DailyDigestPreparation.objects.filter(
                    definition_id=definition.pk,
                    mode=scope.runtime.mode,
                    task_id__in=TaskRun.objects.filter(
                        state__in=NONTERMINAL_STATES
                    ).values("root_id"),
                )
                .exclude(phase__in=TERMINAL_PHASES)
                .exists()
            ):
                return ()
            # An already owned retry/unknown result is not a new reporting day.
            # Coverage holds are checked by the page owner before selection.
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT EXISTS(SELECT 1 FROM stewardship_schedule_occurrence o
                    WHERE o.revision_id=%s AND o.mode=%s AND o.target='admins'
                      AND o.state='pending'
                      AND o.task_id IS NULL AND o.outbox_id IS NULL
                      AND o.due_at<=%s
                      AND NOT EXISTS(SELECT 1
                          FROM stewardship_daily_digest_preparation p
                          WHERE p.occurrence_id=o.id)
                      AND NOT EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment f
                          WHERE f.definition_id=o.definition_id AND f.mode=o.mode
                            AND f.target=o.target AND f.slot=o.slot)
                      AND NOT EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold h
                          WHERE h.definition_id=o.definition_id AND h.mode=o.mode
                            AND h.target=o.target AND h.slot=o.slot
                            AND h.state IN ('unreviewed','assumed_delivered')))""",
                    (definition.current_revision_id, scope.runtime.mode, scope.instant),
                )
                if not cursor.fetchone()[0]:
                    return ()
            guard.check()
            identifier = uuid4()
            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                actor_id=self.worker_id,
                correlation_id=identifier,
                idempotency_key=identifier,
                admit=lambda *args: True,
            )
            DailyDigestPreparation.objects.create(
                id=identifier,
                campaign_id=campaign_id,
                definition_id=definition.pk,
                revision_id=definition.current_revision_id,
                campaign_configuration_id=scope.campaign.active_configuration_id,
                task_id=task.run_id,
                mode=scope.runtime.mode,
                rehearsal_epoch_id=epoch.pk if epoch else None,
                cutoff=scope.instant,
                actor_id=self.worker_id,
                correlation_id=identifier,
            )
            guard.check()
            return (task,)
