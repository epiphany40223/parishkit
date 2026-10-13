"""Provider-free daily preparation execution with bounded, resumable effects."""

from dataclasses import replace
from functools import partial

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
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
from parishkit.stewardship.observability import Event, failure_kind_of
from parishkit.stewardship.storage import StorageInvariantError

from .chart_rendering import ChartRenderError
from .daily_digest import render_daily_digest
from .digest_building import (
    admit_daily_facts,
    begin_daily_facts,
    load_daily_document,
    retain_daily_content,
)
from .digest_capture import capture_daily_snapshot
from .digest_comparison import withheld
from .digest_fanout import fanout_daily
from .digest_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)
from .digest_planning import cover_dates, discover_dates
from .facts import FactUnavailable
from .materialization import materialize_fact_set

# A compile that cannot finish is retried until this attempt, then fails
# permanently. A chart the server renderer cannot draw on this last attempt
# is withheld instead, so the digest is still sent (#477).
FINAL_ATTEMPT = 5


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
    try:
        content = render_daily_digest(document, public_origin=public_origin)
    except ChartRenderError as error:
        # Earlier attempts retry (``_execute``); the last one sends the
        # digest without its server-drawn charts rather than not at all. Its
        # tables and text part carry every number the charts drew.
        with execution.effect():
            attempt = _status(lock_task_claim(execution.claim)).attempt
        if attempt < FINAL_ATTEMPT:
            raise
        document = replace(document, comparison=withheld(document.comparison))
        content = render_daily_digest(
            document, public_origin=public_origin, charts=False
        )
        with execution.effect():
            charts_withheld(execution.claim, error, attempt)
    with execution.effect():
        retain_daily_content(execution.claim, document, content)


def charts_withheld(claim, error, attempt):
    """Record durably that this digest is sent without its server-drawn charts.

    One ERROR ``task_failed`` entry naming the chart that could not be
    drawn (a closed failure word), the failure's category, the task and the
    attempt, so System logs show why the email has no charts. ERROR is the
    level the worker login may record for ``task_failed`` below CRITICAL
    (``stewardship_operational_log_writer_v1``); only CRITICAL opens an
    incident, and the digest itself was sent. Every digest chart is drawn
    through ``chart_rendering.render_digest_chart``, which names it, so an
    unnamed failure is a programming error, refused rather than guessed.
    """
    if error.chart is None:
        raise ValueError("A withheld digest chart must be named.")
    operational(
        Event.TASK_FAILED,
        level="ERROR",
        schema=ContextKind.FAILURE,
        context={
            "failure": error.chart,
            "failure_kind": failure_kind_of(error),
            "task_id": claim.run_id,
            "attempt": attempt,
            "attempt_limit": FINAL_ATTEMPT,
            "outcome": Outcome.FAILED,
        },
    )


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
            except (FactUnavailable, ChartRenderError):
                # A server-drawn chart the renderer could not draw (#1018
                # slice 7, #477) is retried like unavailable facts; the last
                # attempt sends the digest without its charts (``_build``).
                with execution.effect():
                    status = _status(lock_task_claim(execution.claim))
                    execution.transition(
                        "permanent_failure"
                        if status.attempt >= FINAL_ATTEMPT
                        else "retryable_failure",
                        **(
                            {}
                            if status.attempt >= FINAL_ATTEMPT
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
