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
from parishkit.stewardship.jobs.loop_settings import LoopSettings
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.storage import StorageInvariantError

from .family_schedule_planning import _planning_scope, snapshot_planning_scope
from .models import RestoreDeliveryHold
from .postclose_coverage import resolved_slots
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

    def _definitions(self, campaign_id, *, locked):
        """Up to three current digest definitions (more is an invariant failure)."""
        rows = ScheduleDefinition.objects
        if locked:
            rows = rows.select_for_update(of=("self",))
        return list(
            rows.select_related("current_revision")
            .filter(
                campaign_id=campaign_id,
                current_revision__isnull=False,
                kind__in=("daily_digest", "weekly_digest"),
            )
            .order_by("id")[:3]
        )

    def _forget(self):
        """No digest definition: drop every cursor and hold version."""
        self.cursors, self.last_definition = {}, None
        self.hold_versions = {}

    def _position(self, scope, definitions):
        """This call's definition (alternating), its traversal key and page.

        Plain reads and pure slot arithmetic, so the same position is computed
        on the loop's snapshot rows before the work-order lock and again under
        it (#715).
        """
        definition = next(
            (
                row
                for row in definitions
                if self.last_definition is None or row.pk.int > self.last_definition.int
            ),
            definitions[0],
        )
        revision, mode = definition.current_revision, scope.runtime.mode
        cycle = scope.campaign.production_cycle if mode == "production" else 0
        key = (revision.pk, mode, cycle)
        plan = SchedulePlan.from_values(
            revision.values, scope.campaign.active_configuration.values
        )
        # A released historical hold must be revisited even after its
        # original date cursor was consumed. Retained monotonic versions
        # invalidate traversal only when the inventory actually changes.
        holds = RestoreDeliveryHold.objects.filter(
            definition=definition, mode=mode, target="admins"
        )
        hold_version = {
            **holds.aggregate(count=Count("id"), versions=Sum("version")),
            # Date-only edits intentionally keep the revision identity.
            # Moving a draft's start earlier still introduces past slots.
            "configuration": scope.campaign.active_configuration_id,
        }
        after = (
            self.cursors.get(key)
            if self.hold_versions.get(key) == hold_version
            else None
        )
        page = plan.page(through=scope.instant, after=after, limit=self.limit)
        return definition, revision, mode, cycle, key, holds, hold_version, page

    def _advance(self, definitions, definition, mode, cycle, key, page, hold_version):
        """Record a committed (or empty) page: cursor, hold version, alternation."""
        current = {(row.current_revision_id, mode, cycle) for row in definitions}
        self.cursors = {
            key: value for key, value in self.cursors.items() if key in current
        }
        self.cursors[key] = page.cursor
        self.hold_versions = {
            key: value for key, value in self.hold_versions.items() if key in current
        }
        self.hold_versions[key] = hold_version
        self.last_definition = definition.pk

    def __call__(self, guard, *, settings=None):
        """Materialize one finite page, alternating definitions even during backlog.

        ``settings`` is the scheduler loop's LoopSettings. Before the
        work-order lock the producer computes this call's page from those
        rows and a fresh clock, with the same planning predicate and the same
        _position(). A page with no slot creates nothing, so it is recorded
        exactly as the locked pass would record it and the lock is skipped
        (#715); a page with slots goes to the locked pass, which reads
        everything again.
        """
        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Digest planning requires actual scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError("Digest planning must own its transaction.")
        guard.check()
        settings = LoopSettings() if settings is None else settings
        try:
            scope, _ = snapshot_planning_scope(settings, postclose=True)
        except PermissionError:
            return ()
        definitions = self._definitions(settings.campaign_id, locked=False)
        if not definitions:
            self._forget()
            return ()
        position = self._position(scope, definitions) if len(definitions) <= 2 else None
        if position is not None and not position[-1].slots:
            definition, _, mode, cycle, key, _, hold_version, page = position
            self._advance(definitions, definition, mode, cycle, key, page, hold_version)
            return (DigestPlanningResult(definition.pk, 0, ()),)
        guard.check()
        with work_transaction():
            campaign_id = SystemConfiguration.objects.values_list(
                "current_campaign_id", flat=True
            ).first()
            try:
                scope, _ = _planning_scope(campaign_id, postclose=True)
            except PermissionError:
                return ()
            definitions = self._definitions(campaign_id, locked=True)
            if not definitions:
                self._forget()
                return ()
            if len(definitions) > 2:
                raise StorageInvariantError(
                    "Digest definitions exceed configuration bounds."
                )
            definition, revision, mode, cycle, key, holds, hold_version, page = (
                self._position(scope, definitions)
            )
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
                slot.key: occurrence_key(
                    revision.pk, mode, "admins", slot.key, production_cycle=cycle
                )
                for slot in page.slots
            }
            existing = {
                row.occurrence_key: row
                for row in ScheduleOccurrence.objects.filter(
                    occurrence_key__in=identities.values()
                )
            }
            excluded = covered | held
            excluded.update(
                item["slot"]
                for item in resolved_slots(definition.pk, mode, slots=identities)
            )
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
                        production_cycle=cycle,
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
        self._advance(definitions, definition, mode, cycle, key, page, hold_version)
        return (result,)
