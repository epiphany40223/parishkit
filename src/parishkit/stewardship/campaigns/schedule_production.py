"""Fair bounded Family sweeps with PostgreSQL-owned outcomes, not broker state."""

from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.observability import emit_failure
from parishkit.stewardship.storage import StorageInvariantError

from .credential_models import FamilyCampaign
from .family_schedule_planning import plan_family


class FamilyScheduleProducer:
    """Only traversal is in memory; restart repeats durable idempotent planning."""

    def __init__(self, worker_id, *, limit=20):
        """Bound each sweep and attribute effects to the actual scheduler process."""
        if (
            not isinstance(worker_id, UUID)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError(
                "Family schedule sweeps require a bounded process identity."
            )
        self.worker_id, self.limit = worker_id, limit
        self.campaign_id, self.cursor = None, None

    def __call__(self, guard):
        """Visit independent complete groups without retaining locks across them."""
        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Schedule production requires actual scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError(
                "Schedule production must own its transactions."
            )
        guard.check()
        current = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
        if current != self.campaign_id:
            self.campaign_id, self.cursor = current, None
        if current is None:
            return ()
        rows = FamilyCampaign.objects.filter(campaign_id=current)
        if self.cursor is not None:
            rows = rows.filter(id__gt=self.cursor)
        identifiers = list(
            rows.order_by("id").values_list("id", flat=True)[: self.limit]
        )
        results = []
        for identifier in identifiers:
            guard.check()
            try:
                results.append(
                    plan_family(guard, family_id=identifier, worker_id=self.worker_id)
                )
            except PermissionError:
                # Current campaign/Family may change between enumeration and
                # locked admission. No earlier scope survives that boundary.
                pass
            except StorageInvariantError as error:
                # A malformed group must not starve independent Families.
                emit_failure(error)
            self.cursor = identifier
        if len(identifiers) < self.limit:
            self.cursor = None
        guard.check()
        return tuple(results)
