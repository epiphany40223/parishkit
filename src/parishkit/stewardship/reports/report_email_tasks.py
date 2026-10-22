"""Provider-free report email preparation with bounded, resumable effects (#1786).

One ``report_email_prepare`` task moves its preparation through the report
pipeline's phases (report-emails spec, "Report pipeline"), each in its own
owned effect, as the Daily digest's task does (``digest_tasks``). Facts
are built and the email is compiled outside the lifecycle locks; a chart
the renderer cannot draw is retried until the last attempt, which sends the
report without its server-drawn charts rather than not at all.

The handler is registered with the worker and the scheduler, but nothing
creates its task until the conversion admits report schedules (delivery
plan item 4).
"""

from dataclasses import replace
from functools import partial

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import ScheduleRevision
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

from .chart_rendering import ChartRenderError
from .digest_comparison import withheld
from .digest_tasks import FINAL_ATTEMPT, charts_withheld
from .facts import FactUnavailable
from .materialization import materialize_fact_set
from .report_email_capture import (
    admit_report_facts,
    begin_report_facts,
    compile_report_email,
    load_report_document,
    placeholder_values,
    record_inputs,
    retain_snapshot,
)
from .report_email_fanout import CoverageUncertain, fanout_report
from .report_email_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .report_email_planning import cover_dates, discover_dates

PHASES = {
    "dates": TaskPhase.PREPARING,
    "cover": TaskPhase.RECONCILING,
    "inputs": TaskPhase.STAGING,
    "capture": TaskPhase.RENDERING,
    "fanout": TaskPhase.STAGING,
}


def disposition(row):
    """Tell permanent obsolescence (cancel) from temporary holds (retry).

    A send whose report, revision, campaign, mode or Testing epoch changed,
    or whose occurrence is no longer pending, is cancelled, as a digest's
    is. A report change is refused while a send of it is in progress
    (spec, "Review and Apply"), so a prepared snapshot is not cut short by
    an edit.
    """
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


def recover_report(status):
    """Only durable phase completion proves success; no provider is involved.

    An abandoned attempt is retried until the last attempt, then failed,
    except in ``fanout``: there the snapshot is committed and the cohort is
    owed its messages, and a fanout may be waiting, at any attempt count,
    for an earlier message to settle (#1835), so failing it would leave
    recipients unsent. It is always retried instead.
    """
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
    if status.attempt >= FINAL_ATTEMPT and row.phase != "fanout":
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(60 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_report(action, status):
    """Every effect rechecks its root; expired ownership may always be fenced."""
    row = bound_preparation(status)
    if action == "lease_expired":
        return True
    if action == "recovery_hint":
        return status.state == "running" or recover_report(status) is not None
    if action.startswith("recovery_"):
        plan = recover_report(status)
        return plan is not None and plan.action == action
    outcome = disposition(row)
    if action in {"complete", "safe_cancel"}:
        return outcome == action
    if action in {"hint", "claim", "effect", "heartbeat", "progress"}:
        return True
    return outcome is None and action in {
        "explicit_retry",
        "explicit_retry_replay",
        "permanent_failure",
        "retryable_failure",
    }


def _wait_for_settlement(execution):
    """Retry later while an earlier message to a recipient is unsettled (#1835).

    The fanout page was rolled back. This is never a permanent failure
    (nor is a fanout's lost lease, ``recover_report``): the earlier message
    settles when mail dispatch finishes it or an Administrator resolves its
    unknown outcome, and only then can the send tell whether that recipient
    already has it. Until then this report's later sends wait behind it.
    The task's retry_wait state and transition history record the wait;
    the earlier message's own unknown outcome is logged by mail dispatch.
    """
    with execution.effect():
        status = _status(lock_task_claim(execution.claim))
        execution.transition(
            "retryable_failure",
            retry_seconds=min(60 * 2 ** max(status.attempt - 1, 0), 600),
        )


def _capture(execution, public_origin):
    """Build or reuse the facts, compile the email, then commit the snapshot."""
    with execution.effect():
        facts = begin_report_facts(execution.claim)
    if facts is not None and facts.state != "ready":
        materialize_fact_set(
            facts.pk,
            execution.claim,
            admit=partial(admit_report_facts, execution.claim),
        )
    with execution.effect():
        row = bound_preparation(_status(lock_task_claim(execution.claim)))
        scope = current_preparation(row)
        document = load_report_document(execution.claim, facts)
        values = ScheduleRevision.objects.values_list("values", flat=True).get(
            pk=row.revision_id
        )
        names = placeholder_values(scope)
    execution.check()
    # The detached compiler writes only bounded memory; the next effect
    # rechecks admission before the snapshot is committed.
    compile_email = partial(
        compile_report_email, values=values, public_origin=public_origin, names=names
    )
    try:
        compiled = compile_email(document)
    except ChartRenderError as error:
        with execution.effect():
            attempt = _status(lock_task_claim(execution.claim)).attempt
        if attempt < FINAL_ATTEMPT:
            raise
        document = replace(document, comparison=withheld(document.comparison))
        compiled = compile_email(document)
        with execution.effect():
            charts_withheld(execution.claim, error, attempt)
    with execution.effect():
        retain_snapshot(execution.claim, document, compiled, facts)


def _execute(execution, *, public_origin):
    """Advance finite pages under maintained lifetime, without a provider call."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Report preparation requires maintained worker lifetime."
        )
    while True:
        try:
            with execution.effect():
                status = _status(lock_task_claim(execution.claim))
                row = bound_preparation(status)
                outcome = disposition(row)
                if outcome is not None:
                    if outcome == "safe_cancel" and row.phase != "cancelled":
                        checkpoint_preparation(execution.claim, phase="cancelled")
                    execution.transition(outcome)
                    return
                if status.phase != PHASES[row.phase]:
                    execution.progress(0, 0, phase=PHASES[row.phase])
                if row.phase == "dates":
                    discover_dates(execution.claim)
                elif row.phase == "cover":
                    cover_dates(execution.claim)
                elif row.phase == "inputs":
                    record_inputs(execution.claim)
                elif row.phase == "fanout":
                    fanout_report(execution.claim)
        except CoverageUncertain:
            _wait_for_settlement(execution)
            return
        if row.phase == "capture":
            try:
                _capture(execution, public_origin)
            except (FactUnavailable, ChartRenderError):
                with execution.effect():
                    status = _status(lock_task_claim(execution.claim))
                    final = status.attempt >= FINAL_ATTEMPT
                    execution.transition(
                        "permanent_failure" if final else "retryable_failure",
                        **(
                            {}
                            if final
                            else {
                                "retry_seconds": min(
                                    60 * 2 ** max(status.attempt - 1, 0), 600
                                )
                            }
                        ),
                    )
                return


def report_email_handler(*, scheduler=False, public_origin=None):
    """Scheduler admission is metadata-only; only a configured worker may build."""
    if type(scheduler) is not bool or (
        not scheduler and not isinstance(public_origin, str)
    ):
        raise TypeError(
            "Report preparation requires an admitted runtime role and origin."
        )

    def unavailable(execution):
        """Reading a preparation root confers no report or mail execution authority."""
        raise PermissionError("The scheduler cannot prepare report emails.")

    return Handler(
        WorkQueue.GENERAL,
        admit_report,
        unavailable if scheduler else partial(_execute, public_origin=public_origin),
        recover=recover_report,
        scope=work_transaction,
    )
