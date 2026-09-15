"""Bounded ordinary digest-slot materialization, without mail/fact generation.

BG-07 owns recipient messages, immutable report inputs and the conditional final
weekly obligation. These pending original slots are not executable hints. Its
recovery owner must prepare the entire missed range before releasing a digest;
the scheduler never mistakes this materialization page for a complete group.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import connection
from django.db.models import Count, Sum

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.storage import StorageInvariantError

from .family_schedule_planning import _planning_scope
from .models import RestoreDeliveryHold
from .schedule_evaluation import SchedulePlan
from .schedule_models import ScheduleDefinition, ScheduleFulfillment, ScheduleOccurrence
from .schedules import occurrence_key
from .work_locks import work_transaction


@dataclass(frozen=True)
class DigestPlanningResult:
    """Opaque materialization progress, never data-as-of facts or delivery success."""

    definition_id: UUID
    created: int
    occurrences: tuple[UUID, ...]


class DigestScheduleProducer:
    """Fair per-revision keyset cursors; restarting safely rechecks original slots."""

    def __init__(self, worker_id, *, limit=100):
        """Bound examined local dates, including wholly skipped civil dates."""
        if (
            not isinstance(worker_id, UUID)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("Digest planning requires a bounded scheduler identity.")
        self.worker_id, self.limit = worker_id, limit
        self.cursors, self.last_definition = {}, None
        self.hold_versions = {}

    def __call__(self, guard):
        """Materialize one finite page, alternating definitions even during backlog."""
        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Digest planning requires actual scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError("Digest planning must own its transaction.")
        guard.check()
        with work_transaction():
            campaign_id = SystemConfiguration.objects.values_list(
                "current_campaign_id", flat=True
            ).first()
            try:
                scope, _ = _planning_scope(campaign_id, postclose=True)
            except PermissionError:
                return ()
            definitions = list(
                ScheduleDefinition.objects.select_for_update(of=("self",))
                .select_related("current_revision")
                .filter(
                    campaign_id=campaign_id,
                    current_revision__isnull=False,
                    kind__in=("daily_digest", "weekly_digest"),
                )
                .order_by("id")[:3]
            )
            if not definitions:
                self.cursors, self.last_definition = {}, None
                self.hold_versions = {}
                return ()
            if len(definitions) > 2:
                raise StorageInvariantError(
                    "Digest definitions exceed configuration bounds."
                )
            definition = next(
                (
                    row
                    for row in definitions
                    if self.last_definition is None
                    or row.pk.int > self.last_definition.int
                ),
                definitions[0],
            )
            revision, mode = definition.current_revision, scope.runtime.mode
            key = (revision.pk, mode)
            plan = SchedulePlan.from_values(
                revision.values, scope.campaign.active_configuration.values
            )
            # A released historical hold must be revisited even after its
            # original date cursor was consumed. Retained monotonic versions
            # invalidate traversal only when the inventory actually changes.
            holds = RestoreDeliveryHold.objects.filter(
                definition=definition, mode=mode, target="admins"
            )
            hold_version = holds.aggregate(count=Count("id"), versions=Sum("version"))
            after = (
                self.cursors.get(key)
                if self.hold_versions.get(key) == hold_version
                else None
            )
            page = plan.page(through=scope.instant, after=after, limit=self.limit)
            covered = set(
                ScheduleFulfillment.objects.filter(
                    definition=definition,
                    mode=mode,
                    target="admins",
                    slot__in=[slot.key for slot in page.slots],
                ).values_list("slot", flat=True)
            )
            held = set(
                holds.filter(
                    slot__in=[slot.key for slot in page.slots],
                    state__in=("unreviewed", "assumed_delivered"),
                ).values_list("slot", flat=True)
            )
            occurrences, created = [], 0
            identities = {
                slot.key: occurrence_key(revision.pk, mode, "admins", slot.key)
                for slot in page.slots
            }
            existing = {
                row.occurrence_key: row
                for row in ScheduleOccurrence.objects.filter(
                    occurrence_key__in=identities.values()
                )
            }
            excluded = covered | held
            for slot in page.slots:
                guard.check()
                if slot.key in excluded:
                    continue
                identity = identities[slot.key]
                row = existing.get(identity)
                if row is None:
                    row = ScheduleOccurrence.objects.create(
                        definition=definition,
                        revision=revision,
                        mode=mode,
                        routing="production"
                        if mode == "production"
                        else "testing_override",
                        target="admins",
                        slot=slot.key,
                        due_at=slot.due_at,
                        occurrence_key=identity,
                        pause_version=scope.campaign.pause_version
                        if scope.campaign.delivery_paused and mode == "production"
                        else None,
                        actor_id=self.worker_id,
                        correlation_id=definition.pk,
                    )
                    created += 1
                occurrences.append(row.pk)
            guard.check()
            result = DigestPlanningResult(definition.pk, created, tuple(occurrences))
        # Only committed materialization advances traversal. Losing this cursor
        # is safe because immutable keys/fulfillment remain in PostgreSQL.
        current = {(row.current_revision_id, mode) for row in definitions}
        self.cursors = {
            key: value for key, value in self.cursors.items() if key in current
        }
        self.cursors[key] = page.cursor
        self.hold_versions = {
            key: value for key, value in self.hold_versions.items() if key in current
        }
        self.hold_versions[key] = hold_version
        self.last_definition = definition.pk
        return (result,)
