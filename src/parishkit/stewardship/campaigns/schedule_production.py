"""Fair bounded Family sweeps with PostgreSQL-owned outcomes, not broker state."""

import logging
from time import monotonic
from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.family_mail_epochs import ensure_preparation_epoch
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.observability import Event, emit, emit_failure
from parishkit.stewardship.storage import StorageInvariantError

from .credential_models import FamilyCampaign
from .family_schedule_planning import plan_family

# Most Families one scheduler loop plans while the sweep is finding mail to
# prepare. During a launch send about 40% of planned Families produce a
# message, so a larger page creates mail faster (#394).
FAMILY_SWEEP_LIMIT = 100
# Families planned per loop after a whole page found nothing to prepare (the
# limit before #394). Planning holds the global work-order lock, so an idle
# campaign keeps the smaller page and Family requests wait less for it; the
# first page that finds work switches back to the full limit.
IDLE_FAMILY_SWEEP_LIMIT = 20
# Wall-clock budget for one loop's Family sweep. Each Family is planned in
# its own global work-order lock transaction, and while that lock is
# saturated (a send or a source refresh) one Family takes about 1-3 s on the
# validation host, so under load the budget, not the page limit, ends the
# page. The budget bounds this producer's share of the scheduler loop, which
# also runs the other producers and the due-hint scans before its 90-second
# heartbeat. A shorter loop publishes the hints for newly allocated
# preparation sooner: with 40 s, the October 1 live send measured 70-80 s
# from allocation to hint per task, and the shorter page did not lower the
# measured send rate, because preparation and delivery, not planning, set
# it. It is a pacing bound, not a timeout: no transaction is interrupted,
# and the next loop resumes after the last Family planned.
FAMILY_SWEEP_SECONDS = 15
# Name of this budget in the work_budget_reached process-log line.
BUDGET_NAME = "family_sweep_budget"


class FamilyScheduleProducer:
    """Only traversal is in memory; restart repeats durable idempotent planning."""

    def __init__(
        self, worker_id, *, limit=FAMILY_SWEEP_LIMIT, seconds=FAMILY_SWEEP_SECONDS
    ):
        """Bound each sweep and attribute effects to the actual scheduler process.

        ``limit`` bounds the Families one call visits and ``seconds`` bounds
        its wall time; whichever comes first ends the call, and the next call
        resumes after the last Family visited. A call after one that found
        nothing to prepare visits at most IDLE_FAMILY_SWEEP_LIMIT Families.
        """
        if (
            not isinstance(worker_id, UUID)
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(seconds) not in {int, float}
            or not 0 < seconds <= 60
        ):
            raise ValueError(
                "Family schedule sweeps require a bounded process identity."
            )
        self.worker_id, self.limit, self.seconds = worker_id, limit, seconds
        self.campaign_id, self.cursor = None, None
        # Whether the last page created new preparation work. Start at the
        # full page: a restart mid-send should not slow it.
        self.busy = True

    def __call__(self, guard):
        """Visit independent complete groups without retaining locks across them.

        Each Family is planned in its own transaction, so no lock is held from
        one Family to the next. At least one Family is visited per call; the
        time budget is checked only before each later one.
        """
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
            # A new campaign starts at the full page, as a restart does.
            self.campaign_id, self.cursor, self.busy = current, None, True
        if current is None:
            return ()
        try:
            ensure_preparation_epoch(guard, current, self.worker_id)
        except PermissionError:
            # Source/setup/lifecycle gates hold preparation without creating a
            # dummy epoch or a ticket that could later change its namespace.
            # Once the gate opens, a held send resumes at the full page.
            self.busy = True
            return ()
        rows = FamilyCampaign.objects.filter(campaign_id=current)
        if self.cursor is not None:
            rows = rows.filter(id__gt=self.cursor)
        limit = self.limit if self.busy else min(self.limit, IDLE_FAMILY_SWEEP_LIMIT)
        identifiers = list(rows.order_by("id").values_list("id", flat=True)[:limit])
        results, started, finished, found = [], monotonic(), True, False
        for index, identifier in enumerate(identifiers):
            if index and monotonic() - started >= self.seconds:
                # Out of time: keep the cursor so the next loop resumes here.
                finished = False
                break
            guard.check()
            try:
                result = plan_family(
                    guard, family_id=identifier, worker_id=self.worker_id
                )
                results.append(result)
                if result.selected is not None and not result.held:
                    found |= _enqueue(guard, result.selected)
            except PermissionError:
                # Current campaign/Family may change between enumeration and
                # locked admission. No earlier scope survives that boundary.
                pass
            except StorageInvariantError as error:
                # A malformed group must not starve independent Families.
                emit_failure(error)
            self.cursor = identifier
        if finished and len(identifiers) < limit:
            self.cursor = None
        self.busy = found
        visited = len(identifiers) if finished else index
        _report(visited, limit, finished, started, self)
        guard.check()
        return tuple(results)


def _enqueue(guard, occurrence_id):
    """Allocate the occurrence's preparation; True only for a new ticket.

    An occurrence keeps being selected until its preparation runs, and while
    preparation is held (a paused send, say) that can last a long time.
    Such re-selections reuse the existing ticket and must not keep the
    sweep at its full page size.
    """
    from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
    from parishkit.stewardship.jobs.family_mail_tasks import enqueue_preparation

    known = set(
        FamilyMailPreparation.objects.filter(occurrence_id=occurrence_id).values_list(
            "pk", flat=True
        )
    )
    ticket = enqueue_preparation(guard, occurrence_id)
    return ticket is not None and ticket.pk not in known


def _report(visited, limit, finished, started, producer):
    """Log what one sweep did, for diagnosing send speed (#394).

    A sweep that its time budget ended is a reviewed work_budget_reached
    line, with the budget and elapsed whole seconds, in every deployment.
    Every sweep that visited a Family also writes a DEBUG line with the
    counts; it appears only with debug logging on (the pre-launch default),
    because the reviewed process log has no field for a count.
    """
    elapsed = monotonic() - started
    if not finished:
        emit(
            Event.WORK_BUDGET_REACHED,
            timeout=BUDGET_NAME,
            limit_seconds=int(producer.seconds),
            elapsed_seconds=int(elapsed),
        )
    if visited:
        logging.getLogger("parishkit.stewardship.debug").debug(
            "family schedule sweep: visited %d of a %d-Family page in %.1f s; "
            "budget %s; next page %d",
            visited,
            limit,
            elapsed,
            "ended it" if not finished else "not reached",
            producer.limit if producer.busy else IDLE_FAMILY_SWEEP_LIMIT,
        )
