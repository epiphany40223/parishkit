"""Fair bounded Family sweeps with PostgreSQL-owned outcomes, not broker state."""

import logging
from contextlib import ExitStack, nullcontext
from datetime import datetime, time, timedelta
from itertools import islice
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.family_mail_epochs import ensure_preparation_epoch
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.observability import (
    Event,
    FailureKind,
    emit,
    emit_failure,
)
from parishkit.stewardship.storage import StorageInvariantError

from .family_schedule_planning import PREPARE_AHEAD, plan_family
from .intervals import resolve_local

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
# How often the sweep reads what changed (#640): the campaign-wide inputs,
# the clock and every Family's own inputs, two statements and no lock.
# Between reads a loop only plans Families already queued, so a change is
# queued within this long. A Family changed only by others (a response, a
# source change) is planned on the next page after that; a due time, or a
# Family planned since the previous check, waits behind what is already
# queued, which during a full pass may be most of the campaign.
CHANGE_CHECK_SECONDS = 15
# Every Family is planned at least this often, even when nothing it reads
# appears to have changed. The fingerprints are an optimization and the SQL
# guards stay authoritative; this bounds the cost of any input the
# fingerprints might not cover. About 2,700 plans an hour, against the same
# number every few minutes before #640.
FULL_SWEEP_SECONDS = 3600


class FamilyScheduleProducer:
    """Only traversal is in memory; restart repeats durable idempotent planning.

    The producer plans a Family only when something its plan reads may have
    changed (#640). Each check reads two fingerprints without the work-order
    lock: one of the campaign-wide inputs (``_campaign_inputs``) and one per
    Family (``_family_inputs``). A changed campaign-wide input, the campaign
    clock passing a due or lead-window time, the hourly safety net, a new
    campaign, a held gate or a restart queues every Family; a changed
    Family queues only that Family. Planning is idempotent and recomputes
    everything under the lock, so a Family planned for no reason costs only
    time, and the fingerprints read before planning mean a change that lands
    while a Family is planned queues it again rather than being lost.
    """

    def __init__(
        self,
        worker_id,
        *,
        limit=FAMILY_SWEEP_LIMIT,
        seconds=FAMILY_SWEEP_SECONDS,
        bulk=False,
    ):
        """Bound each sweep and attribute effects to the actual scheduler process.

        ``limit`` bounds the Families one call visits and ``seconds`` bounds
        its wall time; whichever comes first ends the call, and the next call
        continues with the Families still queued. A call after one that found
        nothing to prepare visits at most IDLE_FAMILY_SWEEP_LIMIT Families.
        ``bulk`` (the bulk Family send, #430) plans several Families per
        work-order transaction, each in its own savepoint, until the bulk
        lock-hold budget, and plans Production reminders up to
        ``PREPARE_AHEAD`` before their due time (BG-12); off, each Family
        has its own transaction and planning looks up to the current time.
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
        if type(bulk) is not bool:
            raise ValueError("The bulk sweep switch must be a boolean.")
        self.worker_id, self.limit, self.seconds = worker_id, limit, seconds
        self.bulk = bulk
        self.campaign_id = None
        # (campaign, configuration) pairs whose refresh-time check has run
        # in this process (see _warn_refresh_in_lead_window).
        self.checked = set()
        self._reset()

    def _reset(self):
        """Forget what was planned, so the next check queues every Family.

        A new producer (a restart), a new campaign and a held gate all start
        here. ``busy`` starts at the full page: a restart mid-send should not
        slow it.
        """
        # Families still to plan, in order (a dict used as an ordered set).
        self.pending = {}
        # Each Family's input fingerprint as of its last queueing.
        self.known = {}
        # The campaign-wide fingerprint as of the last full pass, the next
        # due or lead-window instant after it, and when it was queued.
        self.inputs = self.wake = self.swept_at = None
        # When the last check ran, the last Family planned, and the Families
        # planned since that check.
        self.checked_at = self.last = None
        self.recent = set()
        # Whether the last page created new preparation work.
        self.busy = True

    def __call__(self, guard):
        """Plan the queued Families without retaining locks across them.

        Each Family is planned in its own transaction (or, in bulk, its own
        savepoint), so no lock is held from one Family to the next. At least
        one queued Family is visited per call; the time budget is checked
        only before each later one. ``plan_family`` checks scheduler
        ownership itself, so the sweep checks it only at its own start and
        end.
        """
        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Schedule production requires actual scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError(
                "Schedule production must own its transactions."
            )
        guard.check()
        # One runtime read serves the campaign, the mode, the refresh-warning
        # key and the campaign-wide fingerprint.
        runtime = _runtime()
        current = None if runtime is None else runtime["current_campaign_id"]
        if current != self.campaign_id:
            # A new campaign starts again from a full pass, as a restart does.
            self.campaign_id = current
            self._reset()
        if current is None:
            return ()
        if self.bulk:
            key = (current, runtime["active_configuration_id"])
            if key not in self.checked:
                self.checked.add(key)
                _warn_refresh_in_lead_window(current)
        if runtime["mode"] == "testing":
            # Production never needs a rehearsal epoch; the helper rereads
            # the mode under the lock before creating one.
            try:
                ensure_preparation_epoch(guard, current, self.worker_id)
            except PermissionError:
                # Source/setup/lifecycle gates hold preparation without
                # creating a dummy epoch or a ticket that could later change
                # its namespace. Once the gate opens, a full pass resumes at
                # the full page.
                self._reset()
                return ()
        self._refresh(current, runtime)
        limit = self.limit if self.busy else min(self.limit, IDLE_FAMILY_SWEEP_LIMIT)
        identifiers = list(islice(self.pending, limit))
        if not identifiers:
            # Idle: nothing changed since every Family was last planned.
            return ()
        results, started, finished, found = [], monotonic(), True, False
        # In bulk mode one work-order transaction (a "chunk") covers several
        # Families until the bulk hold budget, then commits; see _Chunks.
        chunks = _Chunks(self.bulk, self._dequeue)
        try:
            for index, identifier in enumerate(identifiers):
                if index and monotonic() - started >= self.seconds:
                    # Out of time: the rest stay queued for the next loop.
                    finished = False
                    break
                chunks.next()
                try:
                    with chunks.item():
                        result = plan_family(
                            guard,
                            family_id=identifier,
                            worker_id=self.worker_id,
                            # Off, the call is exactly the one before #430.
                            # On, Production reminders are also planned a
                            # lead window ahead (BG-12, #447).
                            **({"nested": True, "ahead": True} if self.bulk else {}),
                        )
                        results.append(result)
                        if result.selected is not None and not result.held:
                            found |= _enqueue(guard, result.selected)
                except PermissionError:
                    # Current campaign/Family may change between enumeration
                    # and locked admission. No earlier scope survives that
                    # boundary.
                    pass
                except StorageInvariantError as error:
                    # A malformed group must not starve independent Families.
                    emit_failure(error)
                except BaseException:
                    # An unexpected error (a lock timeout, a lost connection)
                    # leaves this Family queued, but at the back, so one
                    # Family that keeps failing cannot block the others. A
                    # transient failure is still retried within seconds.
                    del self.pending[identifier]
                    self.pending[identifier] = None
                    raise
                # Dequeued once its outcome is durable: at once on the
                # one-at-a-time path, at its chunk's commit in bulk.
                chunks.handled(identifier)
                self.last = identifier
        except BaseException:
            # A rolled-back chunk's Families were never dequeued, so they
            # stay queued for the next loop.
            chunks.close(failed=True)
            raise
        chunks.close()
        self.busy = found
        visited = len(identifiers) if finished else index
        _report(visited, limit, finished, started, self)
        guard.check()
        return tuple(results)

    def _dequeue(self, identifier):
        """Forget a Family whose plan has committed (see _Chunks)."""
        self.pending.pop(identifier, None)
        self.recent.add(identifier)

    def _refresh(self, campaign_id, runtime):
        """Queue the Families whose planning inputs may have changed (#640).

        Runs at most every CHANGE_CHECK_SECONDS. A full pass queues every
        Family: the first check, a changed campaign-wide fingerprint, the
        campaign clock reaching the next due or lead-window instant, or
        FULL_SWEEP_SECONDS since the last full pass. Otherwise only Families
        whose own fingerprint changed are queued: ahead of the rest, unless
        planned since the last check, then behind. A full pass's Families
        start after the last one planned, behind anything already queued, so
        a pass queued again while under way still reaches its tail. Families
        no longer in the campaign are dropped.

        Edges are compared with the campaign clock, which only moves forward
        in a deployment. A test that moves the clock backward past an edge
        it has already crossed needs a new producer (or the hourly pass)
        before that edge wakes the sweep again.
        """
        clock = monotonic()
        if (
            self.checked_at is not None
            and clock - self.checked_at < CHANGE_CHECK_SECONDS
        ):
            return
        self.checked_at = clock
        # Lead windows only exist on the bulk path in Production (BG-12).
        ahead = self.bulk and runtime["mode"] == "production"
        now, inputs, edges = _campaign_inputs(campaign_id, ahead=ahead)
        if now is None:
            return
        inputs = (runtime["version"], inputs)
        families = _family_inputs(campaign_id)
        full = (
            self.swept_at is None
            or inputs != self.inputs
            or clock - self.swept_at >= FULL_SWEEP_SECONDS
            or (self.wake is not None and now >= self.wake)
        )
        if full:
            self.inputs, self.swept_at = inputs, clock
            # Strictly later: an edge at or before ``now`` is already due
            # for the planning that follows this read.
            self.wake = min((edge for edge in edges if edge > now), default=None)
        changed = sorted(
            identifier
            for identifier, fingerprint in families.items()
            if full or self.known.get(identifier) != fingerprint
        )
        self.known = families
        remaining = {row: None for row in self.pending if row in families}
        if full:
            # Fairness: a full pass continues after the last Family planned,
            # behind anything already queued, so a pass queued again while
            # under way still reaches its tail.
            if self.last is not None:
                changed = [row for row in changed if row > self.last] + [
                    row for row in changed if row <= self.last
                ]
            self.pending = remaining | dict.fromkeys(changed)
        else:
            # Latency: a Family whose own inputs changed (a response, say)
            # goes ahead of a full pass still under way. One planned since
            # the last check may have changed through its own planning (a
            # new occurrence, or one moving through a send), so it goes
            # behind rather than ahead of Families not yet planned. This
            # only orders the queue; every changed Family is still planned.
            front = [row for row in changed if row not in self.recent]
            back = [row for row in changed if row in self.recent]
            self.pending = dict.fromkeys(front) | remaining | dict.fromkeys(back)
        self.recent.clear()


class _Chunks:
    """The bulk sweep's work-order transactions (#430); a no-op when off.

    ``next()`` before each Family commits the open chunk once it has held
    the lock for the bulk hold budget and opens a new one; ``item()`` wraps
    one Family in a savepoint, so a refused or failed Family rolls back
    alone; ``close()`` commits the last chunk (or rolls it back on error).
    """

    def __init__(self, bulk, committed):
        """Remember whether chunks are on and whom to tell of a commit.

        ``committed`` receives each handled Family once its outcome is
        committed: at once when chunks are off, and at the chunk's commit
        when they are on. A rolled-back chunk's Families are never passed.
        """
        self.bulk, self.stack, self.pace = bulk, None, None
        self.committed, self.items = committed, []

    def handled(self, identifier):
        """Record a Family whose plan finished; see ``committed``."""
        if self.bulk:
            self.items.append(identifier)
        else:
            self.committed(identifier)

    def next(self):
        """Commit the chunk before a Family that would take it past budget.

        As in family_mail_bulk._run_batch: the hold so far plus the average
        Family so far must stay under the bulk hold budget (_Pace);
        otherwise the chunk commits and a new one opens for this Family.
        """
        from parishkit.stewardship.jobs.family_mail_bulk import _Pace

        from .work_locks import work_transaction

        if not self.bulk:
            return
        if self.stack is not None and not self.pace.fits():
            self.close()
        if self.stack is None:
            self.stack = ExitStack()
            self.stack.enter_context(work_transaction())
            self.pace = _Pace()

    def item(self):
        """A savepoint with remembered scopes in bulk mode; nothing otherwise."""
        if not self.bulk:
            return nullcontext()
        from parishkit.stewardship.jobs.admission import remembered_scopes

        stack, pace = ExitStack(), self.pace
        # Callbacks run last-in first-out: the timing covers the savepoint.
        stack.callback(pace.ran, monotonic())
        stack.enter_context(transaction.atomic())
        stack.enter_context(remembered_scopes())
        return stack

    def close(self, *, failed=False):
        """Commit the open chunk, if any; roll it back when ``failed``."""
        stack, self.stack = self.stack, None
        items, self.items = self.items, []
        if stack is None:
            return
        if failed:
            # Roll back: a database error may have broken the transaction,
            # and committing it would raise again and hide the first error.
            stack.__exit__(RuntimeError, RuntimeError("bulk sweep failed"), None)
        else:
            # A commit that raises leaves the items undelivered: queued.
            stack.close()
            for identifier in items:
                self.committed(identifier)


def refresh_in_lead_window(refresh_times, timezone, due_times, lead=PREPARE_AHEAD):
    """Whether a scheduled full refresh falls inside any reminder's lead window.

    ``refresh_times`` are the local ``HH:MM`` times of the daily full
    refreshes (the nightly time and any other ``full_refresh_times``, #465),
    ``timezone`` the campaign's, and ``due_times`` the reminders' UTC due
    times. A lead window runs from ``lead`` before a due time up to it. Each
    time is resolved on the due time's local day and the day before (a
    window may cross midnight) through the shared DST resolver.
    """
    for due in due_times:
        day = due.astimezone(ZoneInfo(timezone)).date()
        for value in refresh_times:
            wall = time.fromisoformat(value)
            for offset in (1, 0):
                at = resolve_local(
                    datetime.combine(day - timedelta(days=offset), wall), timezone
                )
                if due - lead <= at < due:
                    return True
    return False


def _warn_refresh_in_lead_window(campaign_id):
    """Warn when a full refresh would fall inside a reminder's lead window.

    The bulk Family send prepares a Production reminder in the two hours
    before its due time (BG-12). A full refresh's promotion marks the Family
    population dirty, so preparation pauses until the population is
    rebuilt. The nightly full refresh (02:00 by default) never waits for a
    send; the other configured daily times (``full_refresh_times``) wait
    only while one is under way, so they are checked as well, and an hourly
    or quarter-hour full refresh falls in every window. The default
    leaves an 08:00 reminder's window (from 06:00) clear. Only reminders
    still to come on the campaign clock count. The caller warns once per
    process for each campaign and configuration, on its own
    ``refresh_lead_window_conflict`` event (#584). It changes nothing, and any
    failure to check is ignored: it is advice for the operator, not a gate.
    """
    from django.db.models import DateTimeField, Func

    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
    )
    from parishkit.stewardship.source.cadence import refresh_settings

    from .models import Campaign
    from .schedule_models import ScheduleDefinition

    try:
        with transaction.atomic():
            runtime = SystemConfiguration.objects.get()
            if runtime.mode != "production":
                return
            settings = (
                AppliedIntegration.objects.filter(
                    configuration_id=runtime.active_configuration_id,
                    kind="parishsoft",
                )
                .values_list("settings", flat=True)
                .first()
            )
            timezone = (
                Campaign.objects.select_related("active_configuration")
                .get(pk=campaign_id)
                .active_configuration.timezone
            )
            due_times = list(
                ScheduleDefinition.objects.filter(
                    campaign_id=campaign_id,
                    kind="reminder",
                    current_revision__isnull=False,
                    # Only reminders still to come on the campaign clock; a
                    # past one's window no longer matters.
                    current_revision__due_at__gt=Func(
                        function="stewardship_campaign_now_v1",
                        output_field=DateTimeField(),
                    ),
                ).values_list("current_revision__due_at", flat=True)
            )
        schedule = refresh_settings(settings or {})
        times = {schedule["nightly_time"], *schedule["full_refresh_times"]}
        # An hourly or quarter-hour full refresh falls in every window.
        if (
            bool(due_times)
            if schedule["frequency"] != "daily"
            else refresh_in_lead_window(sorted(times), timezone, due_times)
        ):
            emit(
                Event.REFRESH_LEAD_WINDOW_CONFLICT,
                level=logging.WARNING,
                failure_kind=FailureKind.REFRESH_IN_LEAD_WINDOW,
            )
    except Exception as error:  # noqa: BLE001 - advice must never stop planning
        logging.getLogger("parishkit.stewardship.debug").debug(
            "refresh lead-window check skipped: %s", type(error).__name__
        )


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


def _runtime():
    """The one runtime-row read per loop: what the sweep needs, or None."""
    return SystemConfiguration.objects.values(
        "version", "mode", "current_campaign_id", "active_configuration_id"
    ).first()


# Campaign-wide planning inputs (#640), read without the work-order lock. The
# admission scope (jobs.admission._scope and _read_planning_scope) reads the
# runtime row (its version is added by the caller), the campaign (state,
# pause, cycle, configuration), the credential row's go-live gate and
# rehearsal pointer, rehearsal epochs, work gates and unfinished catch-up
# demands; plan_family also reads every schedule definition, whose version
# moves with its current revision. Mutable rows' versions are guarded to
# rise by one on every update, so a version moves on every change. The
# configuration window and the revisions' due times are the clock edges.
CAMPAIGN_INPUTS = """
SELECT stewardship_campaign_now_v1(), c.version, k.starts_at, k.ends_at,
  ARRAY(SELECT x.go_live_gate::text || ':' || coalesce(x.rehearsal_epoch_id::text, '')
        FROM stewardship_campaign_credentials x WHERE x.campaign_id = c.id),
  ARRAY(SELECT e.id::text || ':' || e.state FROM stewardship_rehearsal_epoch e
        WHERE e.campaign_id = c.id ORDER BY e.id),
  ARRAY(SELECT g.id::text || ':' || g.state FROM stewardship_campaign_work_gate g
        WHERE g.campaign_id = c.id ORDER BY g.id),
  ARRAY(SELECT a.id::text FROM stewardship_activation_catchup a
        WHERE a.campaign_id = c.id AND a.completed_at IS NULL ORDER BY a.id),
  ARRAY(SELECT d.id::text || ':' || d.version FROM stewardship_schedule_definition d
        WHERE d.campaign_id = c.id ORDER BY d.id),
  ARRAY(SELECT r.due_at FROM stewardship_schedule_definition d
        JOIN stewardship_schedule_revision r ON r.id = d.current_revision_id
        WHERE d.campaign_id = c.id AND d.kind = 'initial' AND r.due_at IS NOT NULL),
  ARRAY(SELECT r.due_at FROM stewardship_schedule_definition d
        JOIN stewardship_schedule_revision r ON r.id = d.current_revision_id
        WHERE d.campaign_id = c.id AND d.kind = 'reminder' AND r.due_at IS NOT NULL)
FROM stewardship_campaign c
JOIN stewardship_campaign_configuration k ON k.id = c.active_configuration_id
WHERE c.id = %s
"""


def _campaign_inputs(campaign_id, *, ahead):
    """Return the campaign clock, the campaign-wide fingerprint and clock edges.

    The edges are the instants at which planning's answer can change with
    no row changing: the configuration's start and end, each current
    initial or reminder revision's due time and, when ``ahead`` (the bulk
    sweep in Production, BG-12), each reminder's lead-window start. All are
    absolute UTC instants compared with the same campaign clock planning
    uses, so a daylight-saving change needs no special case.
    """
    with connection.cursor() as cursor:
        cursor.execute(CAMPAIGN_INPUTS, [campaign_id])
        row = cursor.fetchone()
    if row is None:
        # The runtime row read earlier named this campaign, and a campaign
        # row is never deleted while current; a missing row means it changed
        # between the two reads. Queue nothing; the next loop rereads the
        # runtime row and starts again.
        return None, None, ()
    now, version, starts, ends, *rows, initial, reminders = row
    # The due times need no place in the fingerprint: a new revision moves
    # its definition's version.
    inputs = (version, starts, ends, *(tuple(values) for values in rows))
    edges = (
        starts,
        ends,
        *initial,
        *reminders,
        *(due - PREPARE_AHEAD for due in reminders if ahead),
    )
    return now, inputs, edges


# Each Family's own planning inputs (#640), one row per Family of the
# campaign, read without the work-order lock and only through columns the
# scheduler role may already read. They are the Family row's planning
# columns (plan_family's FAMILY_FIELDS: eligibility, deliverability and the
# effective response), its eligibility history (initial-invitation
# recovery), its submissions (Testing responses), and the count and version
# sum of its occurrences, fulfillments and restore holds, so an occurrence
# moving through preparation or delivery queues its Family again. A source
# promotion that rewrites the Family row without changing these columns
# does not.
FAMILY_INPUTS = """
WITH definitions AS (
  SELECT id FROM stewardship_schedule_definition WHERE campaign_id = %(campaign)s
), occurrences AS (
  SELECT o.target, count(*) AS rows, sum(o.version) AS versions
  FROM stewardship_schedule_occurrence o JOIN definitions d ON d.id = o.definition_id
  GROUP BY o.target
), fulfillments AS (
  SELECT u.target, count(*) AS rows
  FROM stewardship_schedule_fulfillment u JOIN definitions d ON d.id = u.definition_id
  GROUP BY u.target
), holds AS (
  SELECT h.target, count(*) AS rows, sum(h.version) AS versions
  FROM stewardship_restore_delivery_hold h JOIN definitions d ON d.id = h.definition_id
  GROUP BY h.target
), history AS (
  SELECT e.family_id, count(*) AS rows, max(e.family_version) AS latest
  FROM stewardship_family_eligibility e
  JOIN stewardship_family_campaign f ON f.id = e.family_id
  WHERE f.campaign_id = %(campaign)s
  GROUP BY e.family_id
), submissions AS (
  SELECT s.family_id, count(*) AS rows
  FROM stewardship_submission s
  JOIN stewardship_family_campaign f ON f.id = s.family_id
  WHERE f.campaign_id = %(campaign)s
  GROUP BY s.family_id
)
SELECT f.id, f.active, f.email_eligible, f.email_deliverable,
  f.effective_submission_id, o.rows, o.versions, u.rows, h.rows, h.versions,
  e.rows, e.latest, s.rows
FROM stewardship_family_campaign f
LEFT JOIN occurrences o ON o.target = 'family:' || f.id::text
LEFT JOIN fulfillments u ON u.target = 'family:' || f.id::text
LEFT JOIN holds h ON h.target = 'family:' || f.id::text
LEFT JOIN history e ON e.family_id = f.id
LEFT JOIN submissions s ON s.family_id = f.id
WHERE f.campaign_id = %(campaign)s
"""


def _family_inputs(campaign_id):
    """Map each of the campaign's Families to its input fingerprint."""
    with connection.cursor() as cursor:
        cursor.execute(FAMILY_INPUTS, {"campaign": campaign_id})
        return {row[0]: row[1:] for row in cursor.fetchall()}
