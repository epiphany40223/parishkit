"""A report send's slots and coverage, before any input is recorded (#1786).

The report pipeline's ``dates`` and ``cover`` phases (report-emails spec,
"Report pipeline"), as the Daily digest's (``digest_planning``): pages of at
most 100 candidate sends through the report's ``ReportPlan`` give every due
slot its occurrence, then the latest pending occurrence is chosen and the
earlier ones are coalesced into it, so missed sends become one recovery
send, never a burst. The database's page proof checks every page.
"""

from django.db import connection
from django.db.models import Q

from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.campaigns.postclose_coverage import resolved_slots
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

from .report_email_models import ReportEmailPreparation
from .report_email_ownership import (
    TARGET,
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)

LIMIT = 100
RECOVERY_REASON = "missed_report_recovery"


def _bound(claim, phase):
    """Require one live ordered effect and its exact persisted phase."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(row)
    if row.phase != phase:
        raise StorageInvariantError("Report preparation is in a different phase.")
    return row, scope


def _occurrence(row, scope, claim, correlation, slot, due_at):
    """Create one pending occurrence of this preparation's report revision."""
    cycle = _cycle(row, scope)
    return ScheduleOccurrence.objects.create(
        definition_id=row.definition_id,
        revision_id=row.revision_id,
        mode=row.mode,
        production_cycle=cycle,
        routing="production" if row.mode == "production" else "testing_override",
        target=TARGET,
        slot=slot,
        due_at=due_at,
        occurrence_key=occurrence_key(
            row.revision_id, row.mode, TARGET, slot, production_cycle=cycle
        ),
        pause_version=scope.campaign.pause_version
        if row.mode == "production" and scope.campaign.delivery_paused
        else None,
        actor_id=claim.worker_id,
        correlation_id=correlation,
    )


def _cycle(row, scope):
    """The occurrence namespace: Production's activation cycle, else 0."""
    return scope.campaign.production_cycle if row.mode == "production" else 0


def _excluded(row, scope, keys):
    """The slot keys among ``keys`` already fulfilled, held, resolved or planned."""
    excluded = set(
        ScheduleFulfillment.objects.filter(
            definition_id=row.definition_id, mode=row.mode, target=TARGET, slot__in=keys
        ).values_list("slot", flat=True)
    ) | set(
        RestoreDeliveryHold.objects.filter(
            definition_id=row.definition_id,
            mode=row.mode,
            target=TARGET,
            slot__in=keys,
            state__in=("unreviewed", "assumed_delivered"),
        ).values_list("slot", flat=True)
    )
    excluded.update(
        item["slot"] for item in resolved_slots(row.definition_id, row.mode, slots=keys)
    )
    excluded.update(
        ScheduleOccurrence.objects.filter(
            revision_id=row.revision_id,
            mode=row.mode,
            target=TARGET,
            slot__in=keys,
            production_cycle=_cycle(row, scope),
        ).values_list("slot", flat=True)
    )
    return excluded


def _covered(row):
    """Slots that are no new obligation: fulfilled, restore-held or resolved."""
    fulfilled = ScheduleFulfillment.objects.filter(
        definition_id=row.definition_id, mode=row.mode, target=TARGET
    ).values("slot")
    held = RestoreDeliveryHold.objects.filter(
        definition_id=row.definition_id,
        mode=row.mode,
        target=TARGET,
        state__in=("unreviewed", "assumed_delivered"),
    ).values("slot")
    return (
        Q(slot__in=fulfilled)
        | Q(slot__in=held)
        | Q(slot__in=resolved_slots(row.definition_id, row.mode))
    )


def discover_dates(claim):
    """Give each due slot of one page its occurrence, keeping the cursor.

    The cutoff is frozen at the first executed page, not at allocation, as a
    digest's is: a hint may wait in a queue, and later pages keep it.
    """
    row, scope = _bound(claim, "dates")
    definition = ScheduleDefinition.objects.select_related("current_revision").get(
        pk=row.definition_id
    )
    plan = SchedulePlan.from_values(
        definition.current_revision.values, scope.campaign.active_configuration.values
    )
    if row.version == 1:
        row = checkpoint_preparation(claim, phase="dates", cutoff=scope.instant)
    page = plan.page(through=row.cutoff, after=row.cursor, limit=LIMIT)
    correlation = claim_event(claim)
    excluded = _excluded(row, scope, [slot.key for slot in page.slots])
    for slot in page.slots:
        if slot.key not in excluded:
            lock_task_claim(claim)
            _occurrence(row, scope, claim, correlation, slot.key, slot.due_at)
    return checkpoint_preparation(
        claim, phase="cover" if page.exhausted else "dates", cursor=page.cursor
    )


def _pending(row, scope):
    """This revision's open, unowned, uncovered scheduled occurrences by the cutoff.

    Latest first; a manual send's occurrence belongs to its own preparation.
    """
    owned = ReportEmailPreparation.objects.exclude(pk=row.pk).filter(
        occurrence_id__isnull=False
    )
    return list(
        ScheduleOccurrence.objects.filter(
            revision_id=row.revision_id,
            mode=row.mode,
            target=TARGET,
            state="pending",
            production_cycle=_cycle(row, scope),
            task_id__isnull=True,
            outbox_id__isnull=True,
            due_at__lte=row.cutoff,
        )
        .exclude(slot__startswith="manual:")
        .exclude(_covered(row))
        .exclude(pk__in=owned.values("occurrence_id"))
        .order_by("-due_at", "-id")
    )


def cover_dates(claim):
    """Coalesce one finite page; only exhaustion moves on to the inputs.

    The latest pending occurrence is chosen (or a recovery occurrence when
    only a replaced revision's sends are owed); earlier ones are coalesced
    into it with a ``coalesced`` fulfillment each. An occurrence another
    preparation owns is never reclaimed. With nothing to send, the
    preparation completes.
    """
    row, scope = _bound(claim, "cover")
    correlation = claim_event(claim)
    pending = _pending(row, scope)
    if row.occurrence_id:
        chosen = [item for item in pending if item.pk == row.occurrence_id]
        selected = chosen[0] if chosen else None
    else:
        selected = pending[0] if pending else None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id,due_at FROM stewardship_schedule_occurrence "
            "WHERE id IN (SELECT stewardship_report_email_predecessors_v1(%s,%s,%s)) "
            "ORDER BY due_at DESC,id LIMIT %s",
            [row.definition_id, row.mode, row.rehearsal_epoch_id, LIMIT],
        )
        previous = cursor.fetchall()
    if selected is None and previous and not row.occurrence_id:
        selected = _occurrence(
            row, scope, claim, correlation, f"recovery:{row.pk}", previous[0][1]
        )
        # Keep allocation and forwarded outcomes within one page's budget.
        previous = previous[: LIMIT - 1]
    if selected is None:
        if row.occurrence_id:
            raise PermissionError("Selected report coverage is no longer available.")
        return checkpoint_preparation(claim, phase="complete")
    for identifier, _due in previous:
        lock_task_claim(claim)
        ScheduleRecoveryReplacement.objects.create(
            preparation_id=row.pk,
            previous_id=identifier,
            replacement=selected,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    earlier = [item for item in pending if item.pk != selected.pk]
    for item in sorted(earlier, key=lambda value: value.pk)[: LIMIT - len(previous)]:
        lock_task_claim(claim)
        updated = ScheduleOccurrence.objects.filter(
            pk=item.pk, version=item.version
        ).update(
            state="coalesced",
            replacement=selected,
            reason=RECOVERY_REASON,
            version=item.version + 1,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
        if updated != 1:
            raise StorageInvariantError("Report coverage lost its occurrence version.")
        ScheduleFulfillment.objects.create(
            definition_id=row.definition_id,
            mode=row.mode,
            target=TARGET,
            slot=item.slot,
            disposition="coalesced",
            occurrence=selected,
            actor_id=claim.worker_id,
            correlation_id=correlation,
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM "
            "stewardship_report_email_predecessors_v1(%s,%s,%s))",
            [row.definition_id, row.mode, row.rehearsal_epoch_id],
        )
        unfinished = cursor.fetchone()[0]
    remaining = len(earlier) > LIMIT - len(previous)
    return checkpoint_preparation(
        claim,
        phase="cover" if unfinished or remaining else "inputs",
        occurrence_id=selected.pk,
    )
