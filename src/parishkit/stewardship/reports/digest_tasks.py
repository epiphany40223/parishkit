"""Provider-free daily preparation execution with bounded, resumable effects."""

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

from .daily_digest import render_daily_digest
from .digest_building import (
    admit_daily_facts,
    begin_daily_facts,
    load_daily_document,
    retain_daily_content,
)
from .digest_capture import capture_daily_snapshot
from .digest_fanout import fanout_daily
from .digest_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .digest_planning import cover_dates, discover_dates
from .facts import FactUnavailable
from .materialization import materialize_fact_set


def disposition(row):
    """Distinguish permanent obsolescence from temporary restore/work holds."""
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


def recover_daily(status):
    """Only durable phase completion proves success; no provider is involved."""
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


def admit_daily(action, status):
    """Every effect rechecks its root; expired ownership may always be fenced."""
    row = bound_preparation(status)
    if action == "lease_expired":
        return True
    if action == "recovery_hint":
        return status.state == "running" or recover_daily(status) is not None
    if action.startswith("recovery_"):
        plan = recover_daily(status)
        return plan is not None and plan.action == action
    outcome = disposition(row)
    if action in {"complete", "safe_cancel"}:
        return outcome == action
    if action not in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
        "permanent_failure",
        "retryable_failure",
    }:
        return False
    return outcome is None or action in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
    }


def _build(execution, public_origin):
    """Retain capture before waiting for a shared exact generation to become ready."""
    with execution.effect():
        capture_daily_snapshot(execution.claim)
    with execution.effect():
        facts = begin_daily_facts(execution.claim)
    if facts.state != "ready":
        materialize_fact_set(
            facts.pk, execution.claim, admit=partial(admit_daily_facts, execution.claim)
        )
    with execution.effect():
        document = load_daily_document(execution.claim, facts.pk)
    execution.check()
    # The detached compiler writes only bounded memory. The next effect must
    # recheck admission before any content or recipient handoff is committed.
    content = render_daily_digest(document, public_origin=public_origin)
    with execution.effect():
        retain_daily_content(execution.claim, document, content)


def _execute(execution, *, public_origin):
    """Advance finite pages under maintained lifetime, without a provider call."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Daily preparation requires maintained worker lifetime."
        )
    while True:
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
                "facts": TaskPhase.RENDERING,
                "fanout": TaskPhase.STAGING,
            }[row.phase]
            if status.phase != phase:
                # Unknown totals stay unknown; phase/heartbeat evidence is more
                # useful than an invented percentage during finite discovery.
                execution.progress(0, 0, phase=phase)
            if row.phase == "dates":
                discover_dates(execution.claim)
            elif row.phase == "cover":
                cover_dates(execution.claim)
            elif row.phase == "fanout":
                fanout_daily(execution.claim)
        if row.phase == "facts":
            try:
                _build(execution, public_origin)
            except FactUnavailable:
                with execution.effect():
                    status = _status(lock_task_claim(execution.claim))
                    execution.transition(
                        "permanent_failure"
                        if status.attempt >= 5
                        else "retryable_failure",
                        **(
                            {}
                            if status.attempt >= 5
                            else {
                                "retry_seconds": min(
                                    60 * 2 ** max(status.attempt - 1, 0), 600
                                )
                            }
                        ),
                    )
                return


def daily_handler(*, scheduler=False, public_origin=None):
    """Scheduler admission is metadata-only; only a configured worker may build."""
    if type(scheduler) is not bool or (
        not scheduler and not isinstance(public_origin, str)
    ):
        raise TypeError(
            "Daily preparation requires an admitted runtime role and origin."
        )

    def unavailable(execution):
        """Reading a preparation root confers no report or mail execution authority."""
        raise PermissionError("The scheduler cannot prepare Administrator digests.")

    return Handler(
        WorkQueue.GENERAL,
        admit_daily,
        unavailable if scheduler else partial(_execute, public_origin=public_origin),
        recover=recover_daily,
        scope=work_transaction,
    )
