"""Finite digest materialization and multi-transaction coverage under one hold.

Original date rows, not a truncated list in a task payload, retain the complete
range. BG-07 pins the report facts and resolves post-close obligations before
dispatch. A completed preparation checkpoint does not assert report delivery.
"""

from datetime import date

from django.db.models import Q

from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.storage import StorageInvariantError

from .catchup_counts import digest_page_counts
from .catchup_errors import CatchUpPreparationHeld
from .catchup_ownership import claim_event
from .catchup_preparation import _checkpoint, receipt_key
from .models import RestoreDeliveryHold
from .schedule_evaluation import SchedulePlan
from .schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleRecoveryReplacement,
)
from .schedule_recovery import digest_recovery_kind
from .schedules import occurrence_key

LIMIT = 100


def _new_occurrence(claim, correlation, scope, definition, slot, due):
    """Allocate ordinary schedule identity; no outbox or execution hint is created."""
    return ScheduleOccurrence.objects.create(
        definition=definition,
        revision_id=definition.current_revision_id,
        mode="production",
        routing="production",
        target="admins",
        slot=slot,
        due_at=due,
        occurrence_key=occurrence_key(
            definition.current_revision_id, "production", "admins", slot
        ),
        pause_version=scope.campaign.pause_version
        if scope.campaign.delivery_paused
        else None,
        actor_id=claim.worker_id,
        correlation_id=correlation,
    )


def _excluded(definition):
    """Fulfilled/restore-held slots are excluded, not silently reported delivered."""
    covered = ScheduleFulfillment.objects.filter(
        definition=definition, mode="production", target="admins"
    ).values("slot")
    held = RestoreDeliveryHold.objects.filter(
        definition=definition,
        mode="production",
        target="admins",
        state__in=("unreviewed", "assumed_delivered"),
    ).values("slot")
    return Q(slot__in=covered) | Q(slot__in=held)


def prepare_digest(demand, claim, scope, definition, cursor):
    """Examine or reconcile no more than 100 original occurrence outcomes."""
    correlation = claim_event(claim)
    configuration = scope.campaign.active_configuration_id
    prefix = configuration.hex + ":"
    parts = cursor.split(":")
    if len(parts) == 3 and parts[0] == definition.pk.hex:
        stage, after = parts[1:]
    else:
        stage, after = "dates", ""
    if stage == "dates":
        plan = SchedulePlan.from_values(
            definition.current_revision.values,
            scope.campaign.active_configuration.values,
        )
        page = plan.page(
            through=min(demand.cutoff, scope.instant),
            after=date.fromisoformat(after) if after else None,
            limit=LIMIT,
        )
        slots = {slot.key for slot in page.slots}
        excluded = set(
            ScheduleFulfillment.objects.filter(
                definition=definition,
                mode="production",
                target="admins",
                slot__in=slots,
            ).values_list("slot", flat=True)
        ) | set(
            RestoreDeliveryHold.objects.filter(
                definition=definition,
                mode="production",
                target="admins",
                slot__in=slots,
                state__in=("unreviewed", "assumed_delivered"),
            ).values_list("slot", flat=True)
        )
        # Revisited configurations inherit old semantic coverage page by page,
        # without scanning the complete date history in a final transaction.
        prior_coalesced = ScheduleFulfillment.objects.filter(
            definition=definition,
            mode="production",
            target="admins",
            slot__in=slots,
            disposition="coalesced",
        ).count()
        existing = set(
            ScheduleOccurrence.objects.filter(
                revision_id=definition.current_revision_id,
                mode="production",
                target="admins",
                slot__in=slots,
            ).values_list("slot", flat=True)
        )
        excluded |= existing
        created = 0
        for slot in page.slots:
            lock_task_claim(claim)
            if slot.key not in excluded:
                _new_occurrence(
                    claim, correlation, scope, definition, slot.key, slot.due_at
                )
                created += 1
        # Cover retains the exhausted date cursor as the SQL page-chain proof;
        # selection resumption itself uses outstanding semantic outcomes.
        next_date = page.cursor.isoformat() if page.cursor else ""
        stage = "cover" if page.exhausted else "dates"
        return _checkpoint(
            demand,
            claim,
            key=prefix + f"page:{definition.pk}:{next_date or 'empty'}:{stage}",
            cursor=prefix + f"digests:{definition.pk.hex}:{stage}:{next_date}",
            phase="digests",
            items=created,
            outcome_counts=digest_page_counts(prior_coalesced),
        )
    if stage != "cover":
        raise StorageInvariantError("Digest cursor has no compiled continuation.")
    original = (
        ScheduleOccurrence.objects.select_for_update()
        .filter(
            revision_id=definition.current_revision_id,
            mode="production",
            target="admins",
            due_at__lte=demand.cutoff,
        )
        .exclude(slot__startswith="recovery:")
        .exclude(_excluded(definition))
    )
    if original.filter(
        Q(state__in=("running", "delivery_unknown"))
        | (Q(state="pending") & (Q(task_id__isnull=False) | Q(outbox_id__isnull=False)))
    ).exists():
        raise CatchUpPreparationHeld("Digest preparation requires delivery recovery.")
    pending = original.filter(state="pending")
    # Immutable old coverage can already point at a safely cancelled aggregate.
    # Forward that graph explicitly instead of deleting fulfillment, changing
    # old outcomes, or silently omitting its dates from the new report inputs.
    predecessors = (
        ScheduleOccurrence.objects.filter(
            definition=definition,
            mode="production",
            target="admins",
            state="skipped",
            reason="schedule_replaced",
            recovery_replacement__isnull=True,
        )
        .filter(
            Q(
                pk__in=ScheduleFulfillment.objects.filter(
                    definition=definition,
                    mode="production",
                    target="admins",
                    disposition="coalesced",
                ).values("occurrence_id")
            )
            | Q(
                pk__in=ScheduleRecoveryReplacement.objects.filter(demand=demand).values(
                    "replacement_id"
                )
            )
        )
        .exclude(revision_id=definition.current_revision_id)
    )
    previous = list(predecessors.order_by("id")[:LIMIT])
    count = pending.count()
    choice = digest_recovery_kind(definition.kind, count)
    recovery_slot = f"recovery:{demand.pk}"
    selected = ScheduleOccurrence.objects.filter(
        revision_id=definition.current_revision_id,
        mode="production",
        target="admins",
        slot=recovery_slot,
    ).first()
    created = 0
    if selected is None and (choice == "aggregate" or previous):
        latest = pending.order_by("-due_at", "-id").first()
        lock_task_claim(claim)
        selected = _new_occurrence(
            claim,
            correlation,
            scope,
            definition,
            recovery_slot,
            max([row.due_at for row in previous] + ([latest.due_at] if latest else [])),
        )
        created = 1
    elif selected is None and choice == "latest":
        selected = pending.order_by("-due_at", "-id").first()
    if selected is not None:
        if selected.state != "pending" or selected.task_id or selected.outbox_id:
            raise CatchUpPreparationHeld("Selected digest requires owning recovery.")
        pending = pending.exclude(pk=selected.pk)
    previous = previous[: LIMIT - created]
    for row in previous:
        lock_task_claim(claim)
        ScheduleRecoveryReplacement.objects.create(
            demand=demand,
            previous=row,
            replacement=selected,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    rows = list(pending.order_by("id")[: LIMIT - created - len(previous)])
    for row in rows:
        lock_task_claim(claim)
        ScheduleOccurrence.objects.filter(pk=row.pk, version=row.version).update(
            state="coalesced",
            replacement=selected,
            reason="missed_daily_recovery"
            if definition.kind == "daily_digest"
            else "missed_weekly_recovery",
            version=row.version + 1,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
        ScheduleFulfillment.objects.create(
            definition=definition,
            mode="production",
            target="admins",
            slot=row.slot,
            disposition="coalesced",
            occurrence=selected,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    more = pending.exists() or predecessors.exists()
    return _checkpoint(
        demand,
        claim,
        key=(
            prefix + f"cover:{definition.pk}:{demand.groups_completed + 1}"
            if more
            else receipt_key(configuration, "digest", definition.pk)
        ),
        cursor=prefix + f"digests:{definition.pk.hex}:cover:{after}"
        if more
        else prefix + "digests:",
        phase="digests",
        items=created + len(rows) + len(previous),
        outcome_counts=digest_page_counts(len(rows)),
    )
