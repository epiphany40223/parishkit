"""Bounded, provider-free Family schedule allocation and semantic coalescing.

This owner does not render or allocate outbox messages. It prepares BG-06
deliverability recovery using retained eligibility/occurrence identities; the
later delivery owner must recheck them before dispatch. Every invocation owns
one complete Family group (at most the 100
configured definitions); it never selects a message from a partial group.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.ownership import TaskClaim, lock_task_claim
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.storage import StorageInvariantError

from .credential_models import CampaignCredentialState, FamilyCampaign, RehearsalEpoch
from .deliverability_recovery import prepare_initial_recovery
from .models import ActivationCatchUpDemand, CampaignWorkGate, RestoreDeliveryHold
from .schedule_evaluation import SchedulePlan
from .schedule_models import ScheduleDefinition, ScheduleFulfillment, ScheduleOccurrence
from .schedule_recovery import RecoverySlot, plan_recovery
from .schedules import occurrence_key
from .work_locks import require_work_order, work_transaction

FAMILY_FIELDS = (
    "id",
    "campaign_id",
    "active",
    "email_eligible",
    "email_deliverable",
    "effective_submission_id",
)


@dataclass(frozen=True)
class FamilyPlanningResult:
    """Count-only progress plus opaque selected occurrence; no recipient details."""

    family_id: UUID
    created: int = 0
    coalesced: int = 0
    skipped: int = 0
    selected: UUID | None = None
    held: bool = False
    reason: str = ""
    examined: int = 0


def plan_family(guard, *, family_id, worker_id):
    """Recheck current scope and atomically prepare one complete Family group.

    Until BG-06 installs the delivery owner, work already bound to a task or
    outbox is conservatively held here; only unallocated pending rows are safe
    planning inputs. Revision replacement has its separate journal-aware owner.
    The selected pending row is not provider permission or a task execution hint.
    """
    if (
        not isinstance(guard, (SchedulerGuard, TaskClaim))
        or not isinstance(family_id, UUID)
        or not isinstance(worker_id, UUID)
    ):
        raise TypeError("Family planning requires actual schedule ownership.")
    catchup = isinstance(guard, TaskClaim)
    if not catchup and connection.in_atomic_block:
        raise StorageInvariantError("Family planning must own its transaction.")
    if catchup:
        require_work_order()
    check = (lambda: lock_task_claim(guard)) if catchup else guard.check
    check()
    with nullcontext() if catchup else work_transaction():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
        if catchup:
            from .catchup_ownership import claim_event
            from .catchup_tasks import eligible as catchup_eligible
            from .catchup_tasks import owned_demand

            demand = owned_demand(_status(check()))
            if (
                worker_id != guard.worker_id
                or demand.completed_at is not None
                or not catchup_eligible(demand)
            ):
                raise PermissionError("Catch-up Family preparation is held.")
            scope, epoch = _scope(demand.campaign_id), None
            through = min(demand.cutoff, scope.instant)
            correlation_id = claim_event(guard)
        else:
            try:
                scope, epoch = _planning_scope(campaign_id, postclose=True)
            except PermissionError:
                return FamilyPlanningResult(family_id, held=True, reason="scope_held")
            through, correlation_id = scope.instant, family_id
        family = (
            FamilyCampaign.objects.filter(pk=family_id, campaign_id=campaign_id)
            .values(*FAMILY_FIELDS)
            .first()
        )
        if family is None:
            raise PermissionError("Family is outside the current planning scope.")
        definitions = list(
            ScheduleDefinition.objects.select_for_update(of=("self",))
            .select_related("current_revision")
            .filter(
                campaign_id=campaign_id,
                current_revision__isnull=False,
                kind__in=("initial", "reminder"),
            )
            .order_by("id")[:101]
        )
        if len(definitions) > 100:
            raise StorageInvariantError(
                "Family schedule group exceeds configuration bounds."
            )
        target, mode = f"family:{family_id}", scope.runtime.mode
        closed = scope.instant >= scope.campaign.active_configuration.ends_at
        responded = (
            bool(family["effective_submission_id"])
            if mode == "production"
            else Submission.objects.filter(
                family_id=family_id,
                mode="test",
                rehearsal_epoch_id=epoch.pk,
            ).exists()
        )
        eligible = family["active"] and family["email_eligible"]
        covered = set(
            ScheduleFulfillment.objects.filter(
                definition_id__in=[row.pk for row in definitions],
                mode=mode,
                target=target,
            ).values_list("definition_id", "slot")
        )
        held = set(
            RestoreDeliveryHold.objects.filter(
                definition_id__in=[row.pk for row in definitions],
                mode=mode,
                target=target,
                state__in=("unreviewed", "assumed_delivered"),
            ).values_list("definition_id", "slot")
        )
        rows, created = [], 0
        existing = {
            row.revision_id: row
            for row in ScheduleOccurrence.objects.filter(
                revision_id__in=[row.current_revision_id for row in definitions],
                mode=mode,
                target=target,
                slot="once",
            )
            .order_by("revision_id", "-recovery_generation")
            .distinct("revision_id")
        }
        # Work-order serialization fences all occurrence writes. DISTINCT ON
        # keeps one current attempt per semantic slot without loading an
        # unbounded recovery history; PostgreSQL cannot combine it with FOR UPDATE.
        excluded = covered | held
        for definition in definitions:
            check()
            if (definition.pk, "once") in excluded:
                continue
            revision = definition.current_revision
            page = SchedulePlan.from_values(
                revision.values, scope.campaign.active_configuration.values
            ).page(through=through)
            if not page.slots:
                continue
            due = page.slots[0]
            if due.due_at != revision.due_at:
                raise StorageInvariantError(
                    "Schedule revision has inconsistent due time."
                )
            key = occurrence_key(revision.pk, mode, target, due.key)
            row = existing.get(revision.pk)
            if row is None and (catchup or (eligible and not responded)):
                row = ScheduleOccurrence.objects.create(
                    definition=definition,
                    revision=revision,
                    mode=mode,
                    routing="production"
                    if mode == "production"
                    else "testing_override",
                    target=target,
                    slot=due.key,
                    due_at=due.due_at,
                    occurrence_key=key,
                    pause_version=scope.campaign.pause_version
                    if scope.campaign.delivery_paused and mode == "production"
                    else None,
                    actor_id=worker_id,
                    correlation_id=correlation_id,
                )
                created += 1
            elif (
                row is not None
                and definition.kind == "initial"
                and eligible
                and not responded
                and family["email_deliverable"]
                and not closed
            ):
                recovered = prepare_initial_recovery(
                    row,
                    family_id=family_id,
                    worker_id=worker_id,
                    correlation_id=correlation_id,
                    pause_version=scope.campaign.pause_version
                    if scope.campaign.delivery_paused and mode == "production"
                    else None,
                )
                created += recovered.pk != row.pk
                row = recovered
            if row is not None:
                rows.append((row, definition.kind))
        decision = plan_recovery(
            tuple(
                RecoverySlot(
                    occurrence_id=row.pk,
                    definition_id=row.definition_id,
                    kind=kind,
                    mode=row.mode,
                    target=row.target,
                    slot=row.slot,
                    due_at=row.due_at,
                    state=row.state,
                    safely_cancellable=(
                        row.state == "pending"
                        and row.task_id is None
                        and row.outbox_id is None
                    ),
                )
                for row, kind in rows
            ),
            cutoff=through,
            closed=closed,
            eligible=eligible,
            responded=responded,
            deliverable=family["email_deliverable"],
        )
        if not decision.blocked:
            _persist_decision(
                rows,
                decision,
                worker_id=worker_id,
                correlation_id=correlation_id,
                check=check,
            )
        if catchup and not decision.blocked:
            from .catchup_family_coverage import forward_family_coverage

            forward_family_coverage(demand, guard, family_id, decision.selected)
        check()
        return FamilyPlanningResult(
            family_id,
            created,
            len(decision.coalesced),
            len(decision.skipped),
            decision.selected,
            decision.blocked,
            decision.reason,
            len(rows),
        )


def _planning_scope(campaign_id, *, postclose=False):
    """Planning waits for current mode/epoch and ordinary lifecycle admission."""
    scope = _scope(campaign_id)
    campaign, runtime = scope.campaign, scope.runtime
    if (
        campaign is None
        or runtime.current_campaign_id != campaign_id
        or runtime.restore_review_required
        or not (
            campaign.active_configuration.starts_at
            <= scope.instant
            < campaign.active_configuration.ends_at
            or (
                postclose
                and runtime.mode == "production"
                and campaign.state == "closed"
                and scope.instant >= campaign.active_configuration.ends_at
            )
        )
        or (runtime.mode == "testing" and campaign.state != "draft")
        or (
            runtime.mode == "production"
            and campaign.state
            not in (
                {"scheduled", "active", "closed"}
                if postclose
                else {"scheduled", "active"}
            )
        )
        or CampaignWorkGate.objects.filter(campaign_id=campaign_id)
        .exclude(state="released")
        .exists()
        or (
            runtime.mode == "production"
            and ActivationCatchUpDemand.objects.filter(
                campaign_id=campaign_id, completed_at__isnull=True
            ).exists()
        )
    ):
        raise PermissionError("Ordinary schedule planning is held.")
    credentials = (
        CampaignCredentialState.objects.select_for_update()
        .filter(campaign_id=campaign_id, go_live_gate=False)
        .first()
    )
    if credentials is None:
        raise PermissionError("Schedule planning requires current credential scope.")
    epoch = None
    if runtime.mode == "testing":
        epoch = RehearsalEpoch.objects.filter(
            pk=credentials.rehearsal_epoch_id, campaign_id=campaign_id, state="active"
        ).first()
        if epoch is None:
            raise PermissionError("Testing planning requires an active rehearsal.")
    return scope, epoch


def _persist_decision(rows, decision, *, worker_id, correlation_id, check):
    """Commit every covered semantic slot in the same transaction as its outcome."""
    for row, _ in rows:
        if row.pk not in decision.coalesced and row.pk not in decision.skipped:
            continue
        coalesced = row.pk in decision.coalesced
        check()
        ScheduleOccurrence.objects.filter(pk=row.pk, version=row.version).update(
            state="coalesced" if coalesced else "skipped",
            reason=decision.reason,
            replacement_id=decision.selected if coalesced else None,
            version=row.version + 1,
            actor_id=worker_id,
            correlation_id=correlation_id,
        )
        if coalesced:
            check()
            ScheduleFulfillment.objects.create(
                definition_id=row.definition_id,
                mode=row.mode,
                target=row.target,
                slot=row.slot,
                disposition="coalesced",
                occurrence_id=decision.selected,
                actor_id=worker_id,
                correlation_id=correlation_id,
            )
