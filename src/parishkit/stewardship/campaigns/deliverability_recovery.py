"""Recover a terminal initial attempt only from a new durable eligibility edge.

The ordinary Family planner owns the full group transaction, lifecycle admission
and scheduler fence. This helper never revives old work, covers a semantic slot,
creates provider work or interprets unknown acceptance as failure.
"""

from .credential_models import FamilyEligibilityChange
from .schedule_models import ScheduleOccurrence
from .schedules import occurrence_key
from .work_locks import require_work_order


def prepare_initial_recovery(
    previous, *, family_id, worker_id, correlation_id, pause_version
):
    """Return a fresh generation-bound attempt, or the untouched terminal row.

    History is written atomically with source promotion (and later suppression
    changes). Reading the last false state and its first subsequent true state
    avoids mistaking unrelated eligibility-history updates for recovery. Source
    changes during an unresolved attempt do not authorize another attempt after
    it eventually fails: the qualifying edge must follow its terminal outcome.
    Both owners acquire the work-order lock before their mutation statements;
    terminal rows cannot receive a metadata-only timestamp update. Comparing
    their statement timestamps therefore follows the serialized owner order.
    """
    require_work_order()
    if previous.state not in {"failed", "skipped"} or (
        previous.state == "skipped"
        and previous.reason not in {"no_deliverable_recipient", "family_ineligible"}
    ):
        return previous
    history = FamilyEligibilityChange.objects.filter(family_id=family_id)
    last_false = (
        history.filter(email_deliverable=False)
        .order_by("-family_version")
        .values_list("family_version", flat=True)
        .first()
    )
    if last_false is None:
        return previous
    event = (
        history.filter(email_deliverable=True, family_version__gt=last_false)
        .order_by("family_version")
        .values("family_version", "created_at")
        .first()
    )
    if (
        event is None
        or event["family_version"] <= previous.recovery_generation
        or event["created_at"] <= previous.updated_at
    ):
        return previous
    generation = event["family_version"]
    return ScheduleOccurrence.objects.create(
        definition_id=previous.definition_id,
        revision_id=previous.revision_id,
        mode=previous.mode,
        routing=previous.routing,
        target=previous.target,
        slot=previous.slot,
        due_at=previous.due_at,
        recovery_generation=generation,
        occurrence_key=occurrence_key(
            previous.revision_id,
            previous.mode,
            previous.target,
            previous.slot,
            recovery_generation=generation,
        ),
        actor_id=worker_id,
        correlation_id=correlation_id,
        pause_version=pause_version,
    )
