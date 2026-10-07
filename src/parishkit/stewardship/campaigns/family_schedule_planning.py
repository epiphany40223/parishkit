"""Bounded, provider-free Family schedule allocation and semantic coalescing.

This owner does not render or allocate outbox messages. The scheduler and
preparation worker both recheck complete groups here; the later delivery owner
must independently recheck them before dispatch. Every invocation owns
one complete Family group (at most the 100
configured definitions); it never selects a message from a partial group.
"""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
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

# How long before its due time a Production reminder may be planned and
# prepared on the bulk path (BG-12, #447), so only sending is left at the due
# time. The occurrence guard (stewardship_occurrence_guard_v1) admits a
# preparation claim up to two hours early and no more, so this must never
# exceed that cap; a test pins the two together.
PREPARE_AHEAD = timedelta(hours=2)

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


def plan_family(guard, *, family_id, worker_id, nested=False, ahead=False):
    """Recheck current scope and atomically prepare one complete Family group.

    Work already bound to a task or outbox is conservatively held here;
    only unallocated pending rows are safe planning inputs. Revision replacement
    has its separate journal-aware owner.
    The selected pending row is not provider permission or a task execution hint.
    ``nested`` lets the scheduler's bulk sweep (#430) plan several Families
    in one work-order transaction, each in its own savepoint.

    Planning looks up to the current time, except for Production reminders
    planned ahead (BG-12, see ``_horizon``): ``ahead`` is the scheduler's bulk
    sweep, and a preparation claim extends its own horizon when its
    occurrence is a Production reminder not yet due but due within
    PREPARE_AHEAD. A group whose initial invitation is still owed never
    looks ahead.
    """
    if (
        not isinstance(guard, (SchedulerGuard, TaskClaim))
        or not isinstance(family_id, UUID)
        or not isinstance(worker_id, UUID)
        or type(ahead) is not bool
        or (ahead and isinstance(guard, TaskClaim))
    ):
        raise TypeError("Family planning requires actual schedule ownership.")
    claimed = isinstance(guard, TaskClaim)
    if not claimed and connection.in_atomic_block and not nested:
        raise StorageInvariantError("Family planning must own its transaction.")
    if claimed:
        require_work_order()
    check = (lambda: lock_task_claim(guard)) if claimed else guard.check
    task = check()
    catchup = claimed and task.task_type == "activation_catchup"
    dispatch = claimed and task.task_type == "outbox_delivery"
    if dispatch:
        from parishkit.stewardship.jobs.family_mail_dispatch import bound_dispatch

        message = bound_dispatch(_status(task))
        if message.family_id != family_id or worker_id != guard.worker_id:
            raise PermissionError("Dispatch cannot plan another Family.")
    occurrence = None
    if claimed and not catchup and not dispatch:
        from parishkit.stewardship.jobs.family_mail_tasks import _row, owned_preparation

        ticket = owned_preparation(_status(task))
        occurrence = _row(ticket.occurrence_id)
        if (
            occurrence is None
            or occurrence.target != f"family:{family_id}"
            or worker_id != guard.worker_id
        ):
            raise PermissionError("Preparation cannot plan another Family.")
    with nullcontext() if claimed else work_transaction():
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
                # A dispatching mail worker reaches here from its submission,
                # whose admission already took these rows FOR SHARE: keep that
                # mode rather than upgrade it while a Family login holds a
                # share (#147). Planning never writes them.
                scope, epoch = _planning_scope(
                    campaign_id, postclose=True, share=dispatch
                )
            except PermissionError:
                return FamilyPlanningResult(family_id, held=True, reason="scope_held")
            through = scope.instant
            correlation_id = guard.run_id if claimed else family_id
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
        initial_delivered = (
            ScheduleFulfillment.objects.filter(
                definition__in=definitions,
                definition__kind="initial",
                mode=mode,
                target=target,
                slot="once",
                disposition="delivered",
            ).exists()
            or RestoreDeliveryHold.objects.filter(
                definition__in=definitions,
                definition__kind="initial",
                mode=mode,
                target=target,
                slot="once",
                state="assumed_delivered",
            ).exists()
        )
        if (
            dispatch
            and not initial_delivered
            and any(
                definition.kind == "initial" and (definition.pk, "once") in held
                for definition in definitions
            )
        ):
            return FamilyPlanningResult(
                family_id, held=True, reason="initial_unfulfilled"
            )
        rows, created = [], 0
        cycle = scope.campaign.production_cycle if mode == "production" else 0
        existing = {
            row.revision_id: row
            for row in ScheduleOccurrence.objects.filter(
                revision_id__in=[row.current_revision_id for row in definitions],
                mode=mode,
                target=target,
                slot="once",
                production_cycle=cycle,
            )
            .order_by("revision_id", "-recovery_generation")
            .distinct("revision_id")
        }
        # Work-order serialization fences all occurrence writes. DISTINCT ON
        # keeps one current attempt per semantic slot without loading an
        # unbounded recovery history; PostgreSQL cannot combine it with FOR UPDATE.
        excluded = covered | held
        # Reminders may be planned up to PREPARE_AHEAD early (BG-12), but only
        # in a reminder-only group: once the Family's initial invitation is
        # fulfilled (or restore-held), so it is not part of this decision.
        # With an invitation still owed, a reminder due within the window
        # would be coalesced into it early and never sent; that group plans
        # exactly as before. Catch-up and dispatch never look ahead.
        reminders = (
            through
            if catchup
            or dispatch
            or any(
                definition.kind == "initial" and (definition.pk, "once") not in excluded
                for definition in definitions
            )
            else _horizon(scope, through, ahead=ahead, occurrence=occurrence)
        )
        for definition in definitions:
            check()
            if (definition.pk, "once") in excluded:
                continue
            revision = definition.current_revision
            page = SchedulePlan.from_values(
                revision.values, scope.campaign.active_configuration.values
            ).page(through=reminders if definition.kind == "reminder" else through)
            if not page.slots:
                continue
            due = page.slots[0]
            if due.due_at != revision.due_at:
                raise StorageInvariantError(
                    "Schedule revision has inconsistent due time."
                )
            key = occurrence_key(
                revision.pk, mode, target, due.key, production_cycle=cycle
            )
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
                    production_cycle=cycle,
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
                        and (
                            (row.task_id is None and row.outbox_id is None)
                            or (dispatch and _dispatch_cancellable(row))
                        )
                    ),
                )
                for row, kind in rows
            ),
            # A reminder planned ahead counts as due here, so an older due
            # reminder not yet sent coalesces into it, up to PREPARE_AHEAD
            # earlier than at its own due time; the Family is mailed once
            # (two reminders less than PREPARE_AHEAD apart merge this way).
            # Only reminder-only groups look ahead (above), so an unsent
            # invitation never absorbs a reminder early.
            cutoff=reminders,
            closed=closed,
            eligible=eligible,
            responded=responded,
            deliverable=family["email_deliverable"],
            initial_delivered=initial_delivered,
        )
        if not decision.blocked:
            _persist_decision(
                rows,
                decision,
                worker_id=worker_id,
                correlation_id=correlation_id,
                check=check,
                dispatch_claim=guard if dispatch else None,
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


def _horizon(scope, through, *, ahead, occurrence):
    """How far ahead this planning creates and selects reminders (BG-12, #447).

    Only Production reminders are planned ahead, only PREPARE_AHEAD, and
    only once the Family's invitation is fulfilled (``plan_family`` keeps
    ``through`` for a group that still owes one):

    - the scheduler's bulk sweep (``ahead``) plans them once due within the
      lead window, so the worker prepares them before the due time;
    - a preparation claim re-plans its own occurrence before writing it.
      Its horizon follows the occurrence, not the bulk switch, so a task
      queued before the bulk send was turned off still completes: a
      Production reminder not yet due but due within the window extends it.
      Already-due work plans exactly as before.

    Everything else, including Testing, initial invitations, catch-up and
    the dispatch-time recheck, looks up to ``through`` (the current time).
    """
    if scope.runtime.mode != "production":
        return through
    limit = through + PREPARE_AHEAD
    if ahead:
        return limit
    if (
        occurrence is not None
        and occurrence.definition.kind == "reminder"
        and occurrence.mode == "production"
        and through < occurrence.due_at <= limit
    ):
        return limit
    return through


def _planning_scope(
    campaign_id, *, postclose=False, allow_missing_epoch=False, share=False
):
    """Planning scope; remembered per bulk item (jobs.admission.remembered_scopes)."""
    from parishkit.stewardship.jobs.admission import remembered

    return remembered(
        ("planning", campaign_id, postclose, allow_missing_epoch, share),
        lambda: _read_planning_scope(
            campaign_id,
            postclose=postclose,
            allow_missing_epoch=allow_missing_epoch,
            share=share,
        ),
    )


def _read_planning_scope(
    campaign_id, *, postclose=False, allow_missing_epoch=False, share=False
):
    """Planning waits for current mode/epoch and ordinary lifecycle admission.

    ``share`` takes the runtime and credential rows FOR SHARE rather than FOR
    UPDATE, for admission-only callers; see jobs.admission._scope.
    """
    scope = _scope(campaign_id, share=share)
    runtime = scope.runtime
    if _planning_held(
        campaign_id,
        scope,
        postclose=postclose,
        gated=lambda: (
            CampaignWorkGate.objects.filter(campaign_id=campaign_id)
            .exclude(state="released")
            .exists()
        ),
        catching_up=lambda: ActivationCatchUpDemand.objects.filter(
            campaign_id=campaign_id, completed_at__isnull=True
        ).exists(),
    ):
        raise PermissionError("Ordinary schedule planning is held.")
    if share:
        # A Family login takes this row FOR SHARE as well (#147). Django has
        # no FOR SHARE, so one raw statement locks and reads the row: the
        # go_live_gate filter is evaluated once, on the row that is locked.
        credentials = next(
            iter(
                CampaignCredentialState.objects.raw(
                    "SELECT * FROM stewardship_campaign_credentials"
                    " WHERE campaign_id=%s AND NOT go_live_gate"
                    " ORDER BY id LIMIT 1 FOR SHARE",
                    [campaign_id],
                )
            ),
            None,
        )
    else:
        credentials = (
            CampaignCredentialState.objects.select_for_update()
            .filter(campaign_id=campaign_id, go_live_gate=False)
            .first()
        )
    return scope, _planning_epoch(
        runtime, credentials, campaign_id, allow_missing_epoch=allow_missing_epoch
    )


def _planning_held(campaign_id, scope, *, postclose, gated, catching_up):
    """Whether ordinary planning is held for ``scope``'s runtime and campaign.

    ``gated`` and ``catching_up`` are callables that read whether the
    campaign has an unreleased work gate or unfinished activation catch-up;
    each is called only when the conditions before it pass, as before. The
    locked scope and the scheduler's unlocked loop snapshot (#715) both use
    this one predicate, so the two cannot drift apart.
    """
    campaign, runtime = scope.campaign, scope.runtime
    return (
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
        or gated()
        or (runtime.mode == "production" and catching_up())
    )


def _planning_epoch(runtime, credentials, campaign_id, *, allow_missing_epoch):
    """The active rehearsal epoch Testing planning needs, or None in Production.

    Raises PermissionError without a current credential row, or in Testing
    without an active epoch (unless ``allow_missing_epoch`` and none is set).
    """
    if credentials is None:
        raise PermissionError("Schedule planning requires current credential scope.")
    if runtime.mode != "testing":
        return None
    if credentials.rehearsal_epoch_id is None and allow_missing_epoch:
        return None
    epoch = RehearsalEpoch.objects.filter(
        pk=credentials.rehearsal_epoch_id, campaign_id=campaign_id, state="active"
    ).first()
    if epoch is None:
        raise PermissionError("Testing planning requires an active rehearsal.")
    return epoch


def snapshot_planning_scope(settings, *, postclose=False):
    """The current campaign's planning scope from one loop's LoopSettings (#715).

    It decides exactly as _read_planning_scope does, from the same predicate,
    but on rows read without the work-order lock or row locks and at most
    once per scheduler loop, and with a fresh clock reading. It is only for
    a producer's "anything to do?" read before the lock: a producer that
    finds work reads its scope again under the lock. Returns ``(scope,
    epoch)`` or raises PermissionError where planning would be held.
    """
    from parishkit.stewardship.campaigns.runtime import _now
    from parishkit.stewardship.jobs.admission import WorkScope

    runtime = settings.runtime
    if runtime is None or runtime.active_configuration_id is None:
        raise PermissionError("Background work requires applied configuration.")
    campaign_id = runtime.current_campaign_id
    scope = WorkScope(runtime, settings.campaign, _now())
    if _planning_held(
        campaign_id,
        scope,
        postclose=postclose,
        gated=lambda: settings.gated,
        catching_up=lambda: settings.catching_up,
    ):
        raise PermissionError("Ordinary schedule planning is held.")
    return scope, _planning_epoch(
        runtime, settings.credentials, campaign_id, allow_missing_epoch=False
    )


def _dispatch_cancellable(row):
    """Only definitive unsent outboxes can join a dispatch-time recovery group."""
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    if row.outbox_id is None:
        return True
    return OutboxMessage.objects.filter(
        pk=row.outbox_id, state__in=("pending", "retry_wait")
    ).exists()


def _persist_decision(
    rows, decision, *, worker_id, correlation_id, check, dispatch_claim=None
):
    """Commit every covered semantic slot in the same transaction as its outcome."""
    for row, _ in rows:
        if row.pk not in decision.coalesced and row.pk not in decision.skipped:
            continue
        coalesced = row.pk in decision.coalesced
        check()
        if dispatch_claim is not None and row.outbox_id is not None:
            from parishkit.stewardship.jobs.family_mail_dispatch import cancel_unsent

            cancel_unsent(row.outbox_id, dispatch_claim, reason=decision.reason)
        updated = ScheduleOccurrence.objects.filter(
            pk=row.pk, version=row.version
        ).update(
            state="coalesced" if coalesced else "skipped",
            reason=decision.reason,
            replacement_id=decision.selected if coalesced else None,
            version=row.version + 1,
            actor_id=worker_id,
            correlation_id=correlation_id,
        )
        if updated != 1:
            raise StorageInvariantError("Family planning lost its occurrence version.")
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
