"""Bounded complete weekly outage coverage before any report input is captured.

The daily planner is intentionally kept independent during weekly stabilization:
the two owners have different SQL proof functions and post-coverage phases.
Changes to page budgeting or lineage traversal must be checked against both
planners and their integration suites. A shared planner can follow once these
contracts stabilize; do not merge their authority namespaces merely to dedupe.
"""

from django.db import connection
from django.db.models import Q

from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.campaigns.schedule_evaluation import SchedulePlan
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleRecoveryReplacement,
)
from parishkit.stewardship.campaigns.schedules import occurrence_key
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .weekly_models import WeeklyDigestPreparation
from .weekly_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)

LIMIT = 100


def _bound(claim, phase):
    """Require one live ordered effect and its exact persisted discovery phase."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(row)
    if row.phase != phase:
        raise StorageInvariantError("Weekly preparation is in a different phase.")
    return row, scope


def _excluded(row):
    """Known fulfillment and restore holds are not new reporting obligations."""
    covered = ScheduleFulfillment.objects.filter(
        definition_id=row.definition_id, mode=row.mode, target="admins"
    ).values("slot")
    held = RestoreDeliveryHold.objects.filter(
        definition_id=row.definition_id,
        mode=row.mode,
        target="admins",
        state__in=("unreviewed", "assumed_delivered"),
    ).values("slot")
    return Q(slot__in=covered) | Q(slot__in=held)


def discover_dates(claim):
    """Materialize at most 100 dates, retaining the cursor even for skipped days."""
    row, scope = _bound(claim, "dates")
    definition = ScheduleDefinition.objects.select_related("current_revision").get(
        pk=row.definition_id
    )
    plan = SchedulePlan.from_values(
        definition.current_revision.values, scope.campaign.active_configuration.values
    )
    # A hint may sit in a queue for days. Freeze the finite recovery cutoff at
    # its first executed page, not at scheduler allocation; later pages retain it.
    if row.version == 1:
        row = checkpoint_preparation(claim, phase="dates", cutoff=scope.instant)
    page = plan.page(through=row.cutoff, after=row.cursor, limit=LIMIT)
    correlation = claim_event(claim)
    keys = [slot.key for slot in page.slots]
    excluded = set(
        ScheduleFulfillment.objects.filter(
            definition=definition, mode=row.mode, target="admins", slot__in=keys
        ).values_list("slot", flat=True)
    ) | set(
        RestoreDeliveryHold.objects.filter(
            definition=definition,
            mode=row.mode,
            target="admins",
            slot__in=keys,
            state__in=("unreviewed", "assumed_delivered"),
        ).values_list("slot", flat=True)
    )
    excluded.update(
        ScheduleOccurrence.objects.filter(
            revision_id=row.revision_id,
            mode=row.mode,
            target="admins",
            slot__in=keys,
        ).values_list("slot", flat=True)
    )
    for slot in page.slots:
        if slot.key in excluded:
            continue
        lock_task_claim(claim)
        ScheduleOccurrence.objects.create(
            definition=definition,
            revision_id=row.revision_id,
            mode=row.mode,
            routing="production" if row.mode == "production" else "testing_override",
            target="admins",
            slot=slot.key,
            due_at=slot.due_at,
            occurrence_key=occurrence_key(
                row.revision_id, row.mode, "admins", slot.key
            ),
            pause_version=scope.campaign.pause_version
            if row.mode == "production" and scope.campaign.delivery_paused
            else None,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    return checkpoint_preparation(
        claim,
        phase="cover" if page.exhausted else "dates",
        cursor=page.cursor,
    )


def cover_dates(claim):
    """Coalesce one finite page; only exhaustion exposes the selected report.

    Choose the latest pending occurrence, which may itself be an activation
    recovery aggregate. Coalesced predecessor edges retain all earlier dates.
    An occurrence already owned by another preparation is never reclaimed here,
    including messages awaiting provider acknowledgement or explicit recovery.
    """
    row, scope = _bound(claim, "cover")
    correlation = claim_event(claim)
    owned = WeeklyDigestPreparation.objects.exclude(pk=row.pk).filter(
        occurrence_id__isnull=False
    )
    pending = (
        ScheduleOccurrence.objects.filter(
            revision_id=row.revision_id,
            mode=row.mode,
            target="admins",
            state="pending",
            task_id__isnull=True,
            outbox_id__isnull=True,
            due_at__lte=row.cutoff,
        )
        .exclude(_excluded(row))
        .exclude(pk__in=owned.values("occurrence_id"))
    )
    selected = (
        pending.filter(pk=row.occurrence_id).first()
        if row.occurrence_id
        else pending.order_by("-due_at", "-id").first()
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id,due_at FROM stewardship_schedule_occurrence "
            "WHERE id IN (SELECT stewardship_weekly_digest_predecessors_v1(%s,%s,%s)) "
            "ORDER BY due_at DESC,id LIMIT %s",
            [row.definition_id, row.mode, row.rehearsal_epoch_id, LIMIT],
        )
        previous_aggregates = cursor.fetchall()
    if selected is None and previous_aggregates and not row.occurrence_id:
        slot = f"recovery:{row.pk}"
        selected = ScheduleOccurrence.objects.create(
            definition_id=row.definition_id,
            revision_id=row.revision_id,
            mode=row.mode,
            routing="production" if row.mode == "production" else "testing_override",
            target="admins",
            slot=slot,
            due_at=previous_aggregates[0][1],
            occurrence_key=occurrence_key(row.revision_id, row.mode, "admins", slot),
            pause_version=scope.campaign.pause_version
            if row.mode == "production" and scope.campaign.delivery_paused
            else None,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
        # Keep occurrence allocation plus forwarded outcomes inside the same
        # finite page budget. A remaining predecessor forces another page.
        previous_aggregates = previous_aggregates[: LIMIT - 1]
    if selected is None:
        if row.occurrence_id:
            raise PermissionError("Selected weekly coverage is no longer available.")
        return checkpoint_preparation(claim, phase="complete")
    pending = pending.exclude(pk=selected.pk)
    for identifier, _ in previous_aggregates:
        lock_task_claim(claim)
        ScheduleRecoveryReplacement.objects.create(
            preparation_id=row.pk,
            previous_id=identifier,
            replacement=selected,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    for previous in list(pending.order_by("id")[: LIMIT - len(previous_aggregates)]):
        lock_task_claim(claim)
        updated = ScheduleOccurrence.objects.filter(
            pk=previous.pk, version=previous.version
        ).update(
            state="coalesced",
            replacement=selected,
            reason="missed_weekly_recovery",
            version=previous.version + 1,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
        if updated != 1:
            raise StorageInvariantError("Weekly coverage lost its occurrence version.")
        ScheduleFulfillment.objects.create(
            definition_id=row.definition_id,
            mode=row.mode,
            target="admins",
            slot=previous.slot,
            disposition="coalesced",
            occurrence=selected,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM "
            "stewardship_weekly_digest_predecessors_v1(%s,%s,%s))",
            [row.definition_id, row.mode, row.rehearsal_epoch_id],
        )
        unfinished = cursor.fetchone()[0]
    return checkpoint_preparation(
        claim,
        phase="cover" if unfinished or pending.exists() else "capture",
        occurrence_id=selected.pk,
    )
