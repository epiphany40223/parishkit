"""Materialize bounded read-only refresh slots under the actual scheduler session."""

import logging
from uuid import UUID

from django.db import connection, transaction
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
from parishkit.stewardship.observability import Event, correlation, emit
from parishkit.stewardship.storage import StorageInvariantError

from .cadence import due_slots, refresh_settings
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

    def __call__(self, guard):
        """Retire a bounded stale page before producing current-scope cadence slots."""
        _, self.cursor = sweep_superseded_refreshes(
            guard, worker_id=self.worker_id, cursor=self.cursor
        )
        return produce_refreshes(guard, skipped=self.skipped)


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


def produce_refreshes(guard, *, skipped=None):
    """Create at most two current slots, without network or a new SQL connection.

    The scheduler retains its pinned session throughout. Restore/purge and absent
    configuration are holds, not consumed slots. Each cadence command and its
    provenance commit atomically; a lost insertion hint is recovered by the
    ordinary Task scanner. Existing slots never retry terminal work implicitly.

    While a bulk Family send is in progress a not yet created delta slot, or a
    daytime full slot (a configured time other than the nightly one, #465), is
    held (``send_hold``): it creates no command, task or failure, and every
    later loop decides again. The first loop after the send creates the
    current slot's refresh, which catches up. ``skipped`` holds the slot keys
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
    with transaction.atomic():
        held = delta_held(database_now())
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
        slots = due_slots(
            now=database_now(),
            timezone=timezone,
            scope_fingerprint=scope_fingerprint(organization, window.digest),
            **schedule,
        )
        result = []
        waiting = set()
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
                # A full tick records the configured time it fell due at,
                # which the SQL guard checks against the applied list; a
                # delta tick records the nightly time, as it always has.
                nightly_time=slot.nightly_time or nightly_time,
                slot_key=slot.slot_key,
                correlation_id=slot.command_id,
            )
            result.append(receipt)
        guard.check()
    if skipped is not None:
        # Only the current slots can be skipped again; forget older ones.
        skipped.clear()
        skipped.update(waiting)
    return tuple(result)


def _waits_for_send(slot, nightly_time):
    """Whether a slot waits for an in-progress bulk Family send.

    Deltas wait (#440). So does a full refresh at any configured time other
    than the nightly one (#465): a daytime full refresh competes for the
    work-order lock just as a delta does, and the nightly refresh at
    ``nightly_time`` is the one the parish relies on to run regardless. An
    hourly or quarter-hour full refresh records the nightly time, so it is
    never held, as before.
    """
    return slot.cause == "delta" or slot.nightly_time != nightly_time


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
            else f"Full ParishSoft refresh at {slot.nightly_time}",
            slot.due_at.isoformat(),
        )
