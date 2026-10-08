"""Materialize bounded read-only refresh slots under the actual scheduler session."""

import logging
from typing import NamedTuple
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from parishkit.stewardship.accounts.configuration_models import (
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import ContextKind
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.go_live_sequencing import refreshes_held
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import WorkScope, require_source_refresh
from parishkit.stewardship.jobs.loop_settings import LoopSettings
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

from .cadence import (
    catch_up_slot,
    due_slots,
    listed_due_slots,
    refresh_settings,
    skips_around_emails,
)
from .data_age import catch_up_at, source_timezone
from .outcomes import scope_fingerprint
from .refresh_models import SourceRefreshTick, SourceSlotDecision
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

    def __call__(self, guard, *, settings=None):
        """Retire a bounded stale page before producing current-scope cadence slots.

        ``settings`` is the scheduler loop's LoopSettings (see produce_refreshes).
        """
        _, self.cursor = sweep_superseded_refreshes(
            guard, worker_id=self.worker_id, cursor=self.cursor
        )
        return produce_refreshes(
            guard, skipped=self.skipped, refused=self.refused, settings=settings
        )


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


class _Cadence(NamedTuple):
    """The applied refresh inputs and the current regular slots (``_cadence``)."""

    window: object
    settings: dict
    schedule: dict
    timezone: str
    fingerprint: str
    now: object
    slots: tuple


def _cadence(scope, organization, now):
    """The applied refresh inputs and the current regular slots at ``now``.

    Plain reads and pure slot arithmetic, so the same computation runs on the
    loop's snapshot rows before the work-order lock (``_ticked``) and again
    under it (#715).
    """
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
    fingerprint = scope_fingerprint(organization, window.digest)
    slots = due_slots(
        now=now, timezone=timezone, scope_fingerprint=fingerprint, **schedule
    )
    return _Cadence(
        window, integration.settings, schedule, timezone, fingerprint, now, slots
    )


def _listed(cadence):
    """Every due slot of today and yesterday, oldest first (``_decide``'s input)."""
    schedule = cadence.schedule
    return listed_due_slots(
        now=cadence.now,
        timezone=cadence.timezone,
        nightly_time=schedule["nightly_time"],
        scope_fingerprint=cadence.fingerprint,
        full_refresh_times=schedule["full_refresh_times"],
        quick_refresh_times=schedule["quick_refresh_times"],
    )


def _ticked(settings):
    """The current slots' receipts when the locked pass has nothing to do, else None.

    The locked pass below creates a command only for a current slot with no
    tick; when every current slot has one it can only return their receipts
    (or nothing, while the source scope is held). This computes the same
    slots from the loop's snapshot rows and a fresh clock without the
    work-order lock (#715). The current slots include the schedule-change
    catch-up whenever the locked pass would request it (#632), so a pending
    catch-up always goes to the locked pass. For a schedule that skips
    refreshes around Family emails, the locked pass may also record a
    decision for an older due slot (``_decide``); when one may be recorded
    (``_may_decide``) this returns None too. Anything it cannot compute (no
    applied configuration, an unconfigured organization, a missing row or an
    invalid window) returns None, so the locked pass decides as before.
    """
    runtime, campaign = settings.runtime, settings.campaign
    if (
        runtime is None
        or runtime.active_configuration_id is None
        or (settings.campaign_id is not None and campaign is None)
    ):
        return None
    scope = WorkScope(runtime, campaign, None)
    try:
        cadence = _cadence(scope, _organization(scope), database_now())
        catch_up = _catch_up(_catch_up_at(), cadence.timezone, cadence.fingerprint)
    except (PermissionError, ValueError, ObjectDoesNotExist):
        return None
    slots = cadence.slots
    if catch_up is not None:
        # In the locked pass's order, so the receipts match it.
        slots = (*slots[:1], catch_up, *slots[1:])
    ticks = {
        tick.slot_key: tick
        for tick in SourceRefreshTick.objects.select_related("command__request").filter(
            slot_key__in=[slot.slot_key for slot in slots]
        )
    }
    # A current slot without a tick (including one recorded as skipped, whose
    # older held slot may run in its place) is the locked pass's to decide.
    if any(slot.slot_key not in ticks for slot in slots):
        return None
    if skips_around_emails(cadence.settings) and _may_decide(cadence):
        return None
    return tuple(_receipt(ticks[slot.slot_key].command) for slot in slots)


def _may_decide(cadence):
    """Whether ``_decide`` could record a decision for an older due slot now.

    ``_decide`` writes only for an undecided candidate (``_undecided``) that
    is inside a Family email's window (skipped) or held by a bulk send
    (held); any other undecided slot stays undecided and writes nothing.
    This makes the same test without the lock, on a fresh clock, so a
    decision is never left unrecorded by the idle shortcut (#715).
    """
    from .data_age import skip_setting_since
    from .send_windows import current_windows, window_cause

    candidates = _listed(cadence)
    keys = [slot.slot_key for slot in candidates]
    undecided = _undecided(
        candidates,
        ticked=set(
            SourceRefreshTick.objects.filter(slot_key__in=keys).values_list(
                "slot_key", flat=True
            )
        ),
        decisions=set(
            SourceSlotDecision.objects.filter(slot_key__in=keys).values_list(
                "slot_key", flat=True
            )
        ),
        nightly_time=cadence.schedule["nightly_time"],
        skip_since=skip_setting_since(),
    )
    if not undecided:
        return False
    if delta_held(cadence.now):
        return True
    windows = current_windows(cadence.now)
    return any(window_cause(windows, slot.due_at) is not None for slot in undecided)


def _undecided(candidates, *, ticked, decisions, nightly_time, skip_since):
    """The candidates ``_decide`` may record a decision for, oldest first.

    Shared by ``_decide`` and the idle shortcut's ``_may_decide`` so both
    select the same slots: no refresh and no decision yet, a slot a bulk
    send would hold (never the nightly refresh), and due no earlier than
    when the skip setting last took effect.
    """
    return [
        slot
        for slot in candidates
        if slot.slot_key not in ticked
        and slot.slot_key not in decisions
        and _waits_for_send(slot, nightly_time)
        and (skip_since is None or slot.due_at >= skip_since)
    ]


def produce_refreshes(guard, *, skipped=None, refused=None, settings=None):
    """Create at most three current slots, without network or a new SQL connection.

    The scheduler retains its pinned session throughout. Restore/purge and absent
    configuration are holds, not consumed slots. Each cadence command and its
    provenance commit atomically; a lost insertion hint is recovered by the
    ordinary Task scanner. Existing slots never retry terminal work implicitly.

    While a go-live attempt holds refreshes (#462), every not yet created
    slot, full or quick, is held the same way and logs
    ``go_live_refresh_held`` once (``_log_go_live_hold``).

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

    ``settings`` is the scheduler loop's LoopSettings. When _ticked() shows
    the locked pass has nothing to create or record (every current slot,
    including any schedule-change catch-up, has its tick, and no older slot
    awaits a skip-around-emails decision), the work-order lock is skipped
    and the slots' receipts are returned (#715).
    """
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Refresh production requires actual scheduler ownership.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Refresh production must own its slot transaction.")
    guard.check()
    with transaction.atomic():
        receipts = _ticked(LoopSettings() if settings is None else settings)
    if receipts is not None:
        # As the locked pass leaves them when no slot waits or is refused.
        if skipped is not None:
            skipped.clear()
        if refused is not None:
            refused.clear()
        return receipts
    # Decide before joining the work-order lock: a send is what saturates it,
    # and this read takes no lock, so the lock is not held any longer for it.
    # The schedule-change catch-up's reads (about five small queries) are
    # also made here, outside the lock; inside, the slot is rebuilt from the
    # locked inputs and the tick guard checks the instant again. So are the
    # Family email windows of a schedule that skips around them (#632).
    with transaction.atomic():
        instant = database_now()
        held = delta_held(instant)
        # A go-live attempt holds every scheduled slot, full or quick (#462).
        go_live = refreshes_held(instant)
        effective_at = _catch_up_at()
        planning = _skip_planning(instant)
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
        cadence = _cadence(scope, organization, database_now())
        window, timezone, fingerprint = (
            cadence.window,
            cadence.timezone,
            cadence.fingerprint,
        )
        nightly_time = cadence.schedule["nightly_time"]
        slots = cadence.slots
        catch_up = _catch_up(effective_at, timezone, fingerprint)
        if catch_up is not None:
            # A full slot of the previous schedule was already overdue when
            # this one took effect: request the schedule-change catch-up
            # after the scheduled full slot, so either can absorb the other.
            slots = (*slots[:1], catch_up, *slots[1:])
        result = []
        waiting, rejected = set(), set()
        decisions = None
        # A slot recorded as skipped never runs, whatever the schedule says
        # now: turning the setting off later must not make the scheduler
        # create a tick the guard refuses (which would roll back the loop).
        recorded_skips = set(
            SourceSlotDecision.objects.filter(
                slot_key__in=[slot.slot_key for slot in slots], decision="skipped"
            ).values_list("slot_key", flat=True)
        )
        if (
            planning is not None
            and skips_around_emails(cadence.settings)
            # The windows were read before the lock: decide nothing if the
            # mode, the current campaign or its pause has changed since.
            and planning[2] == _delivery_state()
        ):
            # Skip refreshes around Family emails (#632): every due,
            # undecided slot is decided first, and the slot decision record
            # then governs the latest slots below.
            record = _recorder(
                scope.runtime.active_configuration_id,
                timezone,
                nightly_time,
                refused,
                rejected,
            )
            windows, skip_since, _ = planning
            decisions, extra = _decide(
                windows=windows,
                skip_since=skip_since,
                candidates=_listed(cadence),
                latest=slots,
                held=held,
                nightly_time=nightly_time,
                record=record,
                skipped=skipped,
                waiting=waiting,
            )
            slots = (*slots, *extra)
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
            if slot.slot_key in recorded_skips or (
                decisions is not None and decisions.get(slot.slot_key) == "skipped"
            ):
                # Skipped around a Family email: never run, never replaced.
                continue
            if go_live:
                if skipped is None or slot.slot_key not in skipped:
                    _log_go_live_hold(slot)
                waiting.add(slot.slot_key)
                continue
            if held and _waits_for_send(slot, nightly_time):
                if decisions is not None and slot.slot_key not in decisions:
                    decisions[slot.slot_key] = "held"
                    record(slot, "held")
                # Logged once per held slot per process (``_decide`` may
                # have logged it in this loop already).
                if slot.slot_key not in waiting and (
                    skipped is None or slot.slot_key not in skipped
                ):
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


def _delivery_state():
    """The inputs the windows depend on: mode, current campaign and its pause."""
    return SystemConfiguration.objects.values_list(
        "mode", "current_campaign_id", "current_campaign__delivery_paused"
    ).first()


def _skip_planning(now):
    """``(windows, skip_since, state)`` for a schedule that skips, or None.

    Read before the work-order lock (#632): the Family email windows at
    ``now``, when the skip setting last took effect
    (``data_age.skip_setting_since``) and the delivery state they were read
    under (``_delivery_state``), which the loop re-reads under the lock: if
    the mode, the current campaign or its pause changed in between, nothing
    is decided in that loop. None when the active configuration's schedule
    does not skip around Family emails.
    """
    from .data_age import skip_setting_since
    from .send_windows import current_windows

    settings = (
        AppliedIntegration.objects.filter(
            configuration_id__in=SystemConfiguration.objects.values(
                "active_configuration_id"
            ),
            kind="parishsoft",
        )
        .values_list("settings", flat=True)
        .first()
    )
    if settings is None or not skips_around_emails(settings):
        return None
    return current_windows(now), skip_setting_since(), _delivery_state()


def _recorder(configuration_id, timezone, nightly_time, refused, rejected):
    """A function recording one slot decision, each in its own savepoint.

    ``record(slot, decision, window_cause=None)`` writes the row with the
    inputs the slot's tick would carry. A guard refusal (a defect, or inputs
    that moved since they were read) rolls back only that row, adds the key
    to ``rejected`` and is logged once per scheduler process like a refused
    catch-up (``refused`` holds the keys already logged); the loop goes on.
    Returns whether the row was written.
    """

    def record(slot, decision, window_cause=None):
        """Write the decision for ``slot``; False if the guard refused it."""
        if slot.slot_key in rejected:
            # Already refused in this loop: not retried, not logged again.
            return False
        try:
            with transaction.atomic():
                SourceSlotDecision.objects.create(
                    configuration_id=configuration_id,
                    cause=slot.cause,
                    due_at=slot.due_at,
                    timezone=timezone,
                    nightly_time=slot.nightly_time or nightly_time,
                    slot_key=slot.slot_key,
                    decision=decision,
                    window_cause=window_cause,
                    correlation_id=slot.command_id,
                )
            return True
        except IntegrityError as error:
            if getattr(error.__cause__, "sqlstate", None) != "23514":
                raise
            if refused is None or slot.slot_key not in refused:
                with correlation(slot.command_id):
                    emit(
                        Event.STARTUP_VALIDATED,
                        level=logging.WARNING,
                        failure_kind=FailureKind.REFRESH_DECISION_REFUSED,
                    )
            rejected.add(slot.slot_key)
            return False

    return record


def _decide(
    *,
    windows,
    skip_since,
    candidates,
    latest,
    held,
    nightly_time,
    record,
    skipped,
    waiting,
):
    """Decide every due, undecided slot; return the decisions and extra slots.

    For a schedule that skips refreshes around Family emails (#632), in each
    loop, for every due slot of today or yesterday that has no refresh and
    no decision (``candidates``, oldest first), other than the nightly
    refresh: inside a Family email's window it is recorded as skipped (never
    due, never run); otherwise, held by the bulk-send hold (``held``), it is
    recorded as held (still due). Any other undecided slot stays undecided:
    only the latest slot of each kind (``latest``) gets a refresh, in the
    caller's loop. ``windows`` were read before the work-order lock. Slots
    due before ``skip_since`` (when the skip setting last took effect) are
    not decided at all: one already overdue, and perhaps alarming, must not
    be turned into a skip by switching the setting on.

    Returns ``(decisions, extra)``: every candidate's and latest slot's
    recorded decision by slot key, and the held slot to run as the catch-up
    when the hold has ended and the latest due slot of its kind was skipped
    (the newest held slot of that kind, unless a later one of that kind has
    run). ``skipped`` and ``waiting`` are the caller's held-slot logging sets.
    """
    from .send_windows import window_cause

    keys = {slot.slot_key for slot in (*candidates, *latest)}
    ticked = set(
        SourceRefreshTick.objects.filter(slot_key__in=keys).values_list(
            "slot_key", flat=True
        )
    )
    decisions = dict(
        SourceSlotDecision.objects.filter(slot_key__in=keys).values_list(
            "slot_key", "decision"
        )
    )
    undecided = _undecided(
        candidates,
        ticked=ticked,
        decisions=decisions,
        nightly_time=nightly_time,
        skip_since=skip_since,
    )
    for slot in undecided:
        cause = window_cause(windows, slot.due_at)
        if cause is not None:
            if record(slot, "skipped", cause):
                decisions[slot.slot_key] = "skipped"
        elif held:
            if record(slot, "held"):
                decisions[slot.slot_key] = "held"
            if skipped is None or slot.slot_key not in skipped:
                _log_skip(slot)
            waiting.add(slot.slot_key)
    extra = []
    if not held:
        for slot in latest:
            if decisions.get(slot.slot_key) != "skipped":
                continue
            same = [c for c in candidates if c.cause == slot.cause]
            newest = max(
                (
                    c
                    for c in same
                    if c.due_at < slot.due_at
                    and decisions.get(c.slot_key) == "held"
                    and c.slot_key not in ticked
                ),
                key=lambda c: c.due_at,
                default=None,
            )
            if newest is not None and not any(
                c.slot_key in ticked and c.due_at > newest.due_at for c in same
            ):
                extra.append(newest)
    return decisions, extra


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


def _log_go_live_hold(slot):
    """Record a slot held for a go-live attempt, in the process log only.

    ``go_live_refresh_held`` at INFO, correlated to the slot's would-be
    command. Unlike the bulk-send hold it writes no durable entry: its
    evidence is the go-live request and its attempts
    (``campaigns.go_live_sequencing``), so the bulk-send health rules, which
    read the durable ``source_refresh_held`` entries, never see a go-live.
    """
    with correlation(slot.command_id):
        emit(Event.GO_LIVE_REFRESH_HELD, level=logging.INFO)
