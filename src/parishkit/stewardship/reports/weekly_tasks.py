"""Provider-free weekly preparation with resumable pages and maintained leases."""

from functools import partial

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .weekly_capture import capture_weekly_snapshot
from .weekly_fanout import (
    WeeklyCoverageChanged,
    compile_weekly_page,
    load_weekly_page,
    retain_weekly_page,
)
from .weekly_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .weekly_planning import cover_dates, discover_dates


def disposition(row):
    """Old revision/epoch is terminal; temporary campaign gates remain retryable."""
    require_work_order()
    if row.phase in {"complete", "cancelled"}:
        return "complete" if row.phase == "complete" else "safe_cancel"
    runtime = SystemConfiguration.objects.get()
    if (
        runtime.current_campaign_id != row.campaign_id
        or runtime.mode != row.mode
        or not ScheduleDefinition.objects.filter(
            pk=row.definition_id, current_revision_id=row.revision_id
        ).exists()
        or (
            row.mode == "testing"
            and not CampaignCredentialState.objects.filter(
                campaign_id=row.campaign_id, rehearsal_epoch_id=row.rehearsal_epoch_id
            ).exists()
        )
        or (
            row.occurrence_id is not None
            and not ScheduleOccurrence.objects.filter(
                pk=row.occurrence_id, state="pending"
            ).exists()
        )
    ):
        return "safe_cancel"
    current_preparation(row)
    return None


def recover_weekly(status):
    """Replay incomplete private work; durable completion alone proves success."""
    row = bound_preparation(status)
    if status.state != "abandoned":
        return None
    try:
        outcome = disposition(row)
    except PermissionError:
        return None
    if outcome is not None:
        return RecoveryPlan(
            "recovery_complete" if outcome == "complete" else "recovery_cancel"
        )
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(60 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_weekly(action, status):
    """Every effect binds the durable root and exact claim; expiry may be fenced."""
    row = bound_preparation(status)
    if action == "lease_expired":
        return True
    if action == "recovery_hint":
        return status.state == "running" or recover_weekly(status) is not None
    if action.startswith("recovery_"):
        plan = recover_weekly(status)
        return plan is not None and plan.action == action
    outcome = disposition(row)
    if action in {"complete", "safe_cancel"}:
        return outcome == action
    return action in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
    } or (
        outcome is None
        and action
        in {
            "explicit_retry",
            "explicit_retry_replay",
            "permanent_failure",
            "retryable_failure",
        }
    )


def _execute(execution, *, public_origin):
    """Keep discovery/capture/retention short, rendering outside lifecycle locks."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Weekly preparation requires maintained worker lifetime."
        )
    while True:
        page = None
        with execution.effect():
            status = _status(lock_task_claim(execution.claim))
            row = bound_preparation(status)
            outcome = disposition(row)
            if outcome is not None:
                if outcome == "safe_cancel" and row.phase != "cancelled":
                    checkpoint_preparation(execution.claim, phase="cancelled")
                execution.transition(outcome)
                return
            phase = {
                "dates": TaskPhase.PREPARING,
                "cover": TaskPhase.RECONCILING,
                "capture": TaskPhase.STAGING,
                "fanout": TaskPhase.RENDERING,
            }[row.phase]
            if status.phase != phase:
                execution.progress(0, 0, phase=phase)
            if row.phase == "dates":
                discover_dates(execution.claim)
            elif row.phase == "cover":
                cover_dates(execution.claim)
            elif row.phase == "capture":
                capture_weekly_snapshot(execution.claim)
            elif row.phase == "fanout":
                page = load_weekly_page(execution.claim)
        if page is not None:
            execution.check()
            contents = compile_weekly_page(page, public_origin=public_origin)
            try:
                with execution.effect():
                    retain_weekly_page(execution.claim, page, contents)
            except WeeklyCoverageChanged:
                # A new accepted proof is a reason to recompute the next page,
                # not permission to resend it or fail the durable preparation.
                continue


def weekly_handler(*, public_origin=None, scheduler=False):
    """Metadata-only scheduler admission cannot compile or retain private reports."""
    if type(scheduler) is not bool or (
        not scheduler and not isinstance(public_origin, str)
    ):
        raise TypeError(
            "Weekly preparation requires an admitted runtime role and origin."
        )

    def unavailable(execution):
        """Reading queued ownership never confers private execution authority."""
        raise PermissionError("The scheduler cannot prepare Administrator digests.")

    return Handler(
        WorkQueue.GENERAL,
        admit_weekly,
        unavailable if scheduler else partial(_execute, public_origin=public_origin),
        recover=recover_weekly,
        scope=work_transaction,
    )
