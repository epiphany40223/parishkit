"""Materialize bounded read-only refresh slots under the actual scheduler session."""

import logging
from uuid import UUID

from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from parishkit.stewardship.accounts.configuration_models import (
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import ContextKind
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scanning import ScanCursor
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.observability import (
    Event,
    FailureKind,
    correlation,
    emit,
)
from parishkit.stewardship.storage import StorageInvariantError

from .cadence import catch_up_slot, due_slots, refresh_settings
from .data_age import catch_up_at, source_timezone
from .outcomes import scope_fingerprint
from .refresh_models import SourceRefreshTick
from .requests import TASK_TYPE, _organization, _receipt, _window, request_refresh
from .send_hold import delta_held
from .superseding import cancel_superseded_refresh


class SourceProducer:
    """Keep only a fair in-memory scan position; all real work survives restart."""

    def __init__(self, worker_id):
        """Attribute cancellations to the startup-owned scheduler process identity."""
        if not isinstance(worker_id, UUID):
            raise TypeError("A scheduler worker UUID is required.")
        self.worker_id = worker_id
        self.cursor = None
        # Slots already logged as skipped, so each is logged once.
        self.skipped = set()
        # Catch-up slots already logged as refused by the tick guard, kept
        # apart from the held ones so each kind of line is logged once.
        self.refused = set()

    def __call__(self, guard):
        """Retire a bounded stale page before producing current-scope cadence slots."""
        _, self.cursor = sweep_superseded_refreshes(
            guard, worker_id=self.worker_id, cursor=self.cursor
        )
        return produce_refreshes(guard, skipped=self.skipped, refused=self.refused)


def sweep_superseded_refreshes(guard, *, worker_id, cursor=None, limit=100):
    """Fairly visit waiting source Tasks without impersonating running owners."""
    if (
        not isinstance(guard, SchedulerGuard)
        or not isinstance(worker_id, UUID)
        or (cursor is not None and not isinstance(cursor, ScanCursor))
        or type(limit) is not int
        or not 1 <= limit <= 1000
    ):
        raise TypeError("Source sweep requires owned bounded scheduler inputs.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Source sweep must own its short transactions.")
    guard.check()
    rows = TaskRun.objects.filter(
        task_type=TASK_TYPE, state__in=("queued", "retry_wait", "abandoned")
    )
    if cursor is not None:
        rows = rows.filter(
            Q(not_before__gt=cursor.not_before)
            | Q(not_before=cursor.not_before, id__gt=cursor.run_id)
        )
    page = list(rows.order_by("not_before", "id")[:limit])
    cancelled = 0
    for row in page:
        guard.check()
        cancelled += cancel_superseded_refresh(row.pk, worker_id=worker_id)
    guard.check()
    position = (
        ScanCursor(page[-1].not_before, page[-1].pk) if len(page) == limit else None
    )
    return cancelled, position


def produce_refreshes(guard, *, skipped=None, refused=None):
    """Create at most three current slots, without network or a new SQL connection.

    The scheduler retains its pinned session throughout. Restore/purge and absent
    configuration are holds, not consumed slots. Each cadence command and its
    provenance commit atomically; a lost insertion hint is recovered by the
    ordinary Task scanner. Existing slots never retry terminal work implicitly.

    While a bulk Family send is in progress a not yet created delta slot, or a
    daytime full slot (a configured time other than the nightly one, #465), is
    held (``send_hold``): it creates no command, task or failure, and every
    later loop decides again. The first loop after the send creates the
    current slot's refresh, which catches up. When the schedule changed while
    a full slot of the previous one was already overdue, a third slot, the
    schedule-change catch-up (#632), is requested once and held like a
    daytime full slot. ``refused`` holds the catch-up slot keys already
    logged as refused by the tick guard, apart from the held ones, so a
    slot first held and then refused (or the reverse) logs each line once.
    ``skipped`` holds the slot keys
    already logged, so each held slot logs one INFO line, and a held full slot
    also writes one durable entry, per scheduler process (#510).
    """
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Refresh production requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Refresh production must own its slot transaction.")
    guard.check()
    # Decide before joining the work-order lock: a send is what saturates it,
    # and this read takes no lock, so the lock is not held any longer for it.
    # The schedule-change catch-up's reads (about five small queries) are
    # also made here, outside the lock; inside, the slot is rebuilt from the
    # locked inputs and the tick guard checks the instant again.
    with transaction.atomic():
        held = delta_held(database_now())
        effective_at = _catch_up_at()
    guard.check()
    with work_transaction():
        campaign_id = SystemConfiguration.objects.values_list(
            "current_campaign_id", flat=True
        ).first()
        try:
            scope = require_source_refresh(campaign_id=campaign_id)
            organization = _organization(scope)
        except PermissionError:
            return ()
        window = _window(scope)
        integration = AppliedIntegration.objects.get(
            configuration_id=scope.runtime.active_configuration_id, kind="parishsoft"
        )
        timezone = (
            scope.campaign.active_configuration.timezone
            if scope.campaign is not None
            else Parish.objects.values_list("timezone", flat=True).get(
                configuration_id=scope.runtime.active_configuration_id
            )
        )
        schedule = refresh_settings(integration.settings)
        nightly_time = schedule["nightly_time"]
        fingerprint = scope_fingerprint(organization, window.digest)
        slots = due_slots(
            now=database_now(),
            timezone=timezone,
            scope_fingerprint=fingerprint,
            **schedule,
        )
        catch_up = _catch_up(effective_at, timezone, fingerprint)
        if catch_up is not None:
            # A full slot of the previous schedule was already overdue when
            # this one took effect: request the schedule-change catch-up
            # after the scheduled full slot, so either can absorb the other.
            slots = (*slots[:1], catch_up, *slots[1:])
        result = []
        waiting, rejected = set(), set()
        for slot in slots:
            guard.check()
            previous = (
                SourceRefreshTick.objects.select_related("command__request")
                .filter(slot_key=slot.slot_key)
                .first()
            )
            if previous is not None:
                result.append(_receipt(previous.command))
                continue
            if held and _waits_for_send(slot, nightly_time):
                if skipped is None or slot.slot_key not in skipped:
                    _log_skip(slot)
                waiting.add(slot.slot_key)
                continue

            def authorize(current):
                """Recheck exact applied inputs while retaining the same work order."""
                return (
                    current.runtime.active_configuration_id
                    == scope.runtime.active_configuration_id
                    and _organization(current) == organization
                    and _window(current).digest == window.digest
                )

            def create(slot=slot, authorize=authorize):
                """Request the slot's refresh and record its tick."""
                receipt = request_refresh(
                    command_id=slot.command_id,
                    cause=slot.cause,
                    actor_id=None,
                    correlation_id=slot.command_id,
                    authorize=authorize,
                )
                SourceRefreshTick.objects.create(
                    command_id=receipt.command_id,
                    configuration_id=scope.runtime.active_configuration_id,
                    due_at=slot.due_at,
                    timezone=timezone,
                    # A full tick records the configured time it fell due
                    # at, which the SQL guard checks against the applied
                    # list; a delta or catch-up tick records the nightly
                    # time, as a delta tick always has.
                    nightly_time=slot.nightly_time or nightly_time,
                    slot_key=slot.slot_key,
                    correlation_id=slot.command_id,
                )
                return receipt

            if slot.cause != "catch_up":
                result.append(create())
                continue
            receipt = _request_catch_up(create, slot, refused)
            if receipt is None:
                rejected.add(slot.slot_key)
            else:
                result.append(receipt)
        guard.check()
    if skipped is not None:
        # Only the current slots can be skipped again; forget older ones.
        skipped.clear()
        skipped.update(waiting)
    if refused is not None:
        refused.clear()
        refused.update(rejected)
    return tuple(result)


def _catch_up_at():
    """When the current schedule took effect, if it needs a catch-up (#632).

    ``data_age.catch_up_at`` in the zone refresh times resolve in; None when
    no configuration is active. Read before the work-order lock is taken.
    """
    active = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    if active is None:
        return None
    return catch_up_at(source_timezone(active))


def _catch_up(effective_at, timezone, fingerprint):
    """The schedule-change catch-up slot to request now, or None (#632).

    None unless the current schedule took effect, at ``effective_at``, while
    a full slot of the previous one was already overdue (``_catch_up_at``).
    The tick guard admits one catch-up per effective instant, so when one
    already exists at that instant under another identity (the source scope
    or time zone changed since), none is requested: it would only be refused.
    """
    if effective_at is None:
        return None
    slot = catch_up_slot(
        effective_at=effective_at, timezone=timezone, scope_fingerprint=fingerprint
    )
    if (
        SourceRefreshTick.objects.filter(command__cause="catch_up", due_at=slot.due_at)
        .exclude(slot_key=slot.slot_key)
        .exists()
    ):
        return None
    return slot


def _request_catch_up(create, slot, refused):
    """Create the catch-up in a savepoint; on a guard refusal, keep the loop.

    The catch-up is an extra the tick guard checks against the configuration
    activations, which may have moved since it was decided before the lock
    (or which a defect may read differently). A refusal (a check violation)
    rolls back only the catch-up's request and tick, so the same loop's full
    and quick slots still commit; it is logged at WARNING once per scheduler
    process (``refused`` holds the slot keys already logged), as the
    ``refresh_catch_up_refused`` category on the reviewed
    ``startup_validated`` event, so it reads as a scheduling problem rather
    than a failed refresh, with a DEBUG line saying what was refused. Any
    other error propagates as before.
    """
    try:
        with transaction.atomic():
            return create()
    except IntegrityError as error:
        if getattr(error.__cause__, "sqlstate", None) != "23514":
            raise
        if refused is None or slot.slot_key not in refused:
            with correlation(slot.command_id):
                emit(
                    Event.STARTUP_VALIDATED,
                    level=logging.WARNING,
                    failure_kind=FailureKind.REFRESH_CATCH_UP_REFUSED,
                )
                logging.getLogger("parishkit.stewardship.debug").debug(
                    "Schedule-change catch-up full ParishSoft refresh due %s "
                    "refused by the refresh-tick guard",
                    slot.due_at.isoformat(),
                )
        return None


def _waits_for_send(slot, nightly_time):
    """Whether a slot waits for an in-progress bulk Family send.

    Deltas wait (#440). So does a full refresh at any configured time other
    than the nightly one (#465): a daytime full refresh competes for the
    work-order lock just as a delta does, and the nightly refresh at
    ``nightly_time`` is the one the parish relies on to run regardless. An
    hourly or quarter-hour full refresh records the nightly time, so it is
    never held, as before. The schedule-change catch-up (#632) is a daytime
    full refresh: it is held by its cause, since its slot has no configured
    time and its tick records the nightly time like the nightly refresh's.
    """
    if slot.cause in ("delta", "catch_up"):
        return True
    return slot.nightly_time != nightly_time


def _log_skip(slot):
    """Record a held slot under its slot's command identity.

    The reviewed process log carries no free text, so the INFO line is
    ``source_refresh_held`` correlated to the slot's would-be command; with
    debug logging on, a DEBUG line says why and when the slot fell due.

    A held **full** slot also writes a durable ``source_refresh_held`` INFO
    entry with the task-free ``schedule`` context schema (#510): the health
    check's evidence that the next full refresh is the catch-up, which keeps
    the send's allowance until it promotes or fails. A quick slot writes
    none: quick updates no longer decide whether the data is current. The
    entry commits with the scheduler loop's transaction; after a restart the
    same held slot may be recorded again, which is harmless.
    """
    with correlation(slot.command_id):
        emit(Event.SOURCE_HELD, level=logging.INFO)
        if slot.cause != "delta":
            operational(Event.SOURCE_HELD, level="INFO", schema=ContextKind.SCHEDULE)
        logging.getLogger("parishkit.stewardship.debug").debug(
            "%s due %s held: a Family send is in progress",
            "Incremental ParishSoft update"
            if slot.cause == "delta"
            else "Schedule-change catch-up full ParishSoft refresh"
            if slot.cause == "catch_up"
            else f"Full ParishSoft refresh at {slot.nightly_time}",
            slot.due_at.isoformat(),
        )
