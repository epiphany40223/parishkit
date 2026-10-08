"""Go-live sequencing: attempts, the refresh hold and automatic link work (#462).

Between Start go-live and Confirm Production the system runs the steps the
Administrator used to time by hand against the quarter-hour ParishSoft
refreshes (see docs/specs/stewardship/background-processing/spec.md, "Go-live
sequencing" and "Refreshes wait for go-live", which arrive with the spec in
#790).

**Attempts.** Start go-live, and each later Refresh and prepare again, begin
an *attempt*, numbered from 1 within the transition request. An attempt is
nothing but its full refresh: an ordinary manual refresh command whose id is
``uuid5(request id, "refresh:<n>")``. So attempts need no table of their own:
attempt ``n`` exists exactly when that command row does, and it began when the
command was recorded. The caller names the number it expects to begin
(``begin_attempt``), so a second click after the first committed is a replay,
never attempt ``n + 1``.

**Hold end.** From an attempt's start, scheduled refreshes wait until its
hold end (``hold_end``): ``REFRESH_HOLD`` after the later of the attempt's
refresh promoting and cleanup completing, but never later than
``ATTEMPT_CAP`` after the attempt began. The attempt's refresh is the first
full refresh that started at or after the attempt began; a later full
refresh (a manual one, say) does not restart the clock, because the data
only has to stay put for the links prepared on it. Activation or
cancellation of the request ends the hold at once.

**Link work.** The scheduler's producer (``GoLiveProducer``) asks the existing
owners, as the Administrator who started go-live, for the inactive Family
link preparation once cleanup is complete and the attempt's refresh has
promoted. It first discards any live preparation the request already has
(an earlier attempt's, or one made on the manual links page), discards a
preparation the data has moved past, and asks for the next one, up to
``MAX_PREPARATIONS`` per attempt. Preparation ``k`` of attempt ``n`` has
request key ``uuid5(request id, "prepare:<n>:<k>")``, which does not depend
on any input, so a preparation refused for an input that changed for good
never blocks the next number. The producer never confirms Production, never
starts cleanup, never retries a task and never changes mode or campaign
lifecycle. Migration 0025 lets the scheduler record those two kinds of rows,
for that Administrator only. A refusal of authority (the requester is no
longer a current Administrator) is not retried by this process; the Go live
page reads the same state and says why.

Nothing here does anything without an open go-live request with at least one
attempt in Testing mode; a deployment whose campaign is live in Production
has no open request, so the producer and the refresh hold are idle there.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid5

from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.observability import Event, correlation, emit
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .production_models import ProductionTransitionEvent, ProductionTransitionRequest
from .production_states import GATE_OWNING_PRODUCTION_STATES

# Scheduled refreshes wait this long after the attempt's refresh promoted
# (or cleanup completed, whichever is later) ...
REFRESH_HOLD = timedelta(minutes=60)
# ... and never longer than this after the attempt began.
ATTEMPT_CAP = timedelta(hours=3)
# The first preparation of an attempt and at most three re-preparations.
MAX_PREPARATIONS = 4
# The request states that own the go-live gate.
OPEN_STATES = frozenset(state.value for state in GATE_OWNING_PRODUCTION_STATES)
# SQL's refusal of authority (insufficient privilege): the intake trigger's
# requester check. Any other database error is a fault, not a refusal.
REFUSED_SQLSTATE = "42501"


def refresh_command_id(request_id, number):
    """The refresh command id of attempt ``number`` of ``request_id``."""
    return uuid5(request_id, f"refresh:{number}")


def preparation_key(request_id, attempt, number):
    """The request key of preparation ``number`` of ``attempt``."""
    return uuid5(request_id, f"prepare:{attempt}:{number}")


def discard_key(request_id, preparation_id):
    """The request key of the producer's discard of one preparation."""
    return uuid5(request_id, f"discard:{preparation_id}")


def hold_end(started_at, *, promoted_at=None, cleanup_at=None, ended_at=None):
    """When scheduled refreshes resume for an attempt begun at ``started_at``.

    ``REFRESH_HOLD`` after the later of the refresh's promotion and cleanup's
    completion, once both have happened; never later than ``ATTEMPT_CAP``
    after the start; and at once when the request ended (``ended_at``).
    Never before the start.
    """
    end = started_at + ATTEMPT_CAP
    if promoted_at is not None and cleanup_at is not None:
        end = min(end, max(promoted_at, cleanup_at) + REFRESH_HOLD)
    if ended_at is not None:
        end = min(end, ended_at)
    return max(end, started_at)


def shift(overdue, holds):
    """Move ``overdue`` past every hold window it falls in, to a fixed point.

    ``holds`` are ``(start, end)`` windows. A slot due inside one counts from
    its end; if that end lies inside another (overlapping attempts), from
    that one's end, and so on, so the alarm never sounds while refreshes are
    still held. Each move is strictly later, so this ends.
    """
    if overdue is None:
        return None
    moved = True
    while moved:
        moved = False
        for start, end in holds:
            if start <= overdue < end:
                overdue, moved = end, True
    return overdue


@dataclass(frozen=True)
class Attempt:
    """One go-live attempt: its number, start, refresh promotion and hold end.

    ``promoted_at`` is when the attempt's refresh (the first full refresh
    that started at or after ``started_at``) promoted, None while it has not;
    ``hold_end`` is when scheduled refreshes resume.
    """

    number: int
    started_at: datetime
    promoted_at: datetime | None
    hold_end: datetime

    def holds(self, instant):
        """Whether scheduled refreshes wait at ``instant``."""
        return self.started_at <= instant < self.hold_end


def _promoted_after(started_at):
    """When the first full refresh that started at or after ``started_at`` promoted."""
    from parishkit.stewardship.source.snapshot_models import SourceSnapshot

    return (
        SourceSnapshot.objects.filter(
            kind="full", started_at__gte=started_at, promoted_at__isnull=False
        )
        .order_by("started_at", "promoted_at")
        .values_list("promoted_at", flat=True)
        .first()
    )


def attempts(request):
    """Every attempt of ``request``, oldest first.

    Attempt ``n`` exists while its refresh command does; numbering has no
    gaps, because ``begin_attempt`` begins only the next number.
    """
    from parishkit.stewardship.source.refresh_models import SourceRefreshCommand

    cleanup_at = (
        ProductionTransitionEvent.objects.filter(request=request, action="complete")
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    ended_at = request.updated_at if request.state not in OPEN_STATES else None
    found, number = [], 1
    while True:
        started = (
            SourceRefreshCommand.objects.filter(
                pk=refresh_command_id(request.pk, number)
            )
            .values_list("created_at", flat=True)
            .first()
        )
        if started is None:
            return tuple(found)
        promoted = _promoted_after(started)
        end = hold_end(
            started, promoted_at=promoted, cleanup_at=cleanup_at, ended_at=ended_at
        )
        found.append(Attempt(number, started, promoted, end))
        number += 1


def latest_request():
    """The newest go-live request of the current campaign, or None.

    Its state may be terminal: the data-age alarm still measures from an
    activated or cancelled request's hold ends.
    """
    current = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()
    if current is None:
        return None
    return (
        ProductionTransitionRequest.objects.filter(campaign_id=current)
        .order_by("-created_at", "-id")
        .first()
    )


def open_request():
    """The current campaign's go-live request while it owns the gate, or None.

    Only in Testing mode: a deployment in Production has no go-live in
    progress, whatever its request history.
    """
    if SystemConfiguration.objects.values_list("mode", flat=True).first() != "testing":
        return None
    request = latest_request()
    return request if request is not None and request.state in OPEN_STATES else None


def current_attempt(request):
    """The latest attempt of ``request``, or None before Start queued one."""
    found = attempts(request) if request is not None else ()
    return found[-1] if found else None


def refreshes_held(instant):
    """Whether a go-live holds scheduled refreshes at ``instant``.

    On only while the current Testing campaign's request owns the go-live
    gate and the current attempt's hold end has not passed. Read with no
    lock, before the refresh producer joins the work order.
    """
    attempt = current_attempt(open_request())
    return attempt is not None and attempt.holds(instant)


def held_until(overdue):
    """Where go-live holds move the data-age alarm's overdue point.

    A scheduled full slot that fell due while an attempt held refreshes
    counts again only from that hold's end (chained through overlapping
    attempts, ``shift``), with the usual lateness margin measured from
    there. Only the latest request's attempts count: an earlier go-live's
    holds have long ended, and newer full refreshes have moved the overdue
    point past them.
    """
    if overdue is None:
        return None
    request = latest_request()
    holds = [
        (attempt.started_at, attempt.hold_end)
        for attempt in (attempts(request) if request is not None else ())
    ]
    return shift(overdue, holds)


def last_hold_end(instant):
    """The latest go-live hold end at or before ``instant``, or None.

    The connection line measures its "not checked" gap from here as well as
    from the last success, so it does not flash after a hold ends before the
    first refresh after it has run.
    """
    request = latest_request()
    ends = [
        attempt.hold_end
        for attempt in (attempts(request) if request is not None else ())
        if attempt.hold_end <= instant
    ]
    return max(ends, default=None)


def begin_attempt(request, *, number, actor_id, correlation_id, authorize):
    """Queue attempt ``number``'s full refresh as ``actor_id``; return the number.

    For the web's Start go-live and Refresh and prepare again, which hold
    their own admission (``authorize``, as ``request_refresh`` takes it) and
    pass the number the page offered. A replay of the attempt already begun
    returns it; any other number than the next is refused, so a second
    click after the first committed never begins another attempt. A replay
    returns without calling ``authorize`` and changes nothing; the caller's
    own admission still ran for the click. The
    refresh is an ordinary manual request, so it coalesces with a full load
    already waiting, as any manual refresh does.
    """
    from parishkit.stewardship.source.requests import request_refresh

    if type(number) is not int or number < 1:
        raise ValueError("An attempt number starts at 1.")
    begun = len(attempts(request))
    if begun == number:
        return number
    if begun != number - 1:
        raise StaleRecordError("This go-live attempt was already begun or moved on.")
    request_refresh(
        command_id=refresh_command_id(request.pk, number),
        cause="manual",
        actor_id=actor_id,
        correlation_id=correlation_id,
        authorize=authorize,
    )
    return number


def preparations(request, attempt):
    """The preparations the producer made for ``attempt``, in order.

    They are found by their request keys, counted from 1 until a key has no
    row; a refused request created no row and so used no number.
    """
    from .activation_models import ProductionTokenPreparation

    found, number = [], 1
    while True:
        row = ProductionTokenPreparation.objects.filter(
            transition_id=request.pk,
            request_key=preparation_key(request.pk, attempt.number, number),
        ).first()
        if row is None:
            return tuple(found)
        found.append(row)
        number += 1


def live_preparations(request):
    """The request's preparations that still block a new one, oldest first.

    The same test as ``activation_tokens.preparation_available``, per row: a
    preparation task not yet finished, or staging or tokens not yet disposed.
    """
    from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun

    from .activation_models import ProductionTokenPreparation
    from .credential_models import FamilyAccessToken, FamilyAccessTokenGeneration

    rows = ProductionTokenPreparation.objects.filter(transition=request).order_by(
        "created_at", "id"
    )
    live = []
    for row in rows:
        if (
            TaskRun.objects.filter(
                task_type="production_tokens",
                domain_request_id=row.pk,
                state__in=NONTERMINAL_STATES,
            ).exists()
            or FamilyAccessTokenGeneration.objects.filter(
                operation_id=row.pk, state__in=("building", "ready", "active")
            ).exists()
            or FamilyAccessToken.objects.filter(
                generation__operation_id=row.pk, destroyed_at__isnull=True
            ).exists()
        ):
            live.append(row)
    return tuple(live)


@dataclass(frozen=True)
class Step:
    """What one producer pass did: ``action`` and the attempt and preparation.

    ``action`` is "prepare", "discard" (the attempt's own stale preparation)
    or "discard_prior" (a live one from an earlier attempt or the links page)
    when it requested something, else a
    reason it waited: "none" (no open request or attempt), "cleanup",
    "hold_ended", "refresh", "inputs", "current", "disposal", "exhausted" or
    "refused".
    """

    action: str
    attempt: int | None = None
    preparation: int | None = None
    # For "refused": the action that was refused.
    of: str | None = None

    @property
    def key(self):
        """The step's identity: the action asked for, or the one refused."""
        return (self.of or self.action, self.attempt, self.preparation)


def _admit(request):
    """The owners' admission callback: only this open, cleaned-up request."""

    def admit(transition, previous):
        """Admit link work for the go-live request this pass read."""
        return transition.pk == request.pk and transition.state == "cleanup_complete"

    return admit


def precheck(instant):
    """Decide, with no lock, whether this pass has anything to do.

    Returns the waiting Step, or None when ``advance`` must look under the
    work order. ``advance`` decides the same things again under the lock.
    """
    request = open_request()
    attempt = current_attempt(request)
    if request is None or attempt is None:
        return Step("none")
    if request.state != "cleanup_complete":
        return Step("cleanup", attempt.number)
    if instant >= attempt.hold_end:
        return Step("hold_ended", attempt.number)
    if attempt.promoted_at is None:
        return Step("refresh", attempt.number)
    return None


def _refused(error):
    """Whether ``error`` is an owner's or SQL's refusal of authority."""
    if isinstance(error, PermissionError):
        return True
    return (
        isinstance(error, DatabaseError)
        and getattr(error.__cause__, "sqlstate", None) == REFUSED_SQLSTATE
    )


def advance(instant, *, refused=frozenset()):
    """Take at most one step of go-live link work at ``instant``.

    Runs inside the caller's work-order transaction. Each request goes
    through the existing owners, which recheck everything under their own
    locks. A refusal of authority is reported as "refused" and changes
    nothing; a step already refused in this process (``refused``, by step
    key) is not asked again. Inputs that are momentarily unavailable (a
    population rebuild after a promotion) wait as "inputs". Any other error
    is a fault and propagates.
    """
    from .activation_cleanup import disposed, request_cancellation
    from .activation_inputs import TokenPreparationInputs
    from .activation_models import ProductionTokenCancellation
    from .activation_tokens import current_inputs, request_preparation

    waiting = precheck(instant)
    if waiting is not None:
        return waiting
    request = open_request()
    attempt = current_attempt(request)
    try:
        current = current_inputs(request)
    except StaleRecordError:
        return Step("inputs", attempt.number)
    made = preparations(request, attempt)
    latest = made[-1] if made else None
    number = len(made)

    def discarded(row):
        """Whether ``row`` already has a discard."""
        return ProductionTokenCancellation.objects.filter(preparation=row).exists()

    def discard(row):
        """Discard ``row`` as the go-live requester."""
        key = discard_key(request.pk, row.pk)
        request_cancellation(
            preparation_id=row.pk,
            request_key=key,
            actor_id=request.initiated_by_id,
            correlation_id=key,
            admit=lambda preparation, previous: _admit(request)(
                preparation.transition, previous
            ),
        )
        return Step("discard", attempt.number, number)

    # Decide the step first, then take it, so a refused step is recognized
    # by its key before anything is asked of an owner.
    if latest is not None and not discarded(latest):
        if TokenPreparationInputs.retained(latest) == current:
            return Step("current", attempt.number, number)
        step, act = Step("discard", attempt.number, number), lambda: discard(latest)
    else:
        # Before preparing, any live preparation the request has (an earlier
        # attempt's, or one made on the manual links page) is discarded and
        # its disposal awaited: the owner admits only one at a time.
        #
        # Someone else's live preparation is discarded only when the attempt
        # can replace it: not once the attempt's preparations have run out,
        # and not once its next preparation has been refused here.
        if number >= MAX_PREPARATIONS:
            return Step("exhausted", attempt.number, number)
        following = Step("prepare", attempt.number, number + 1)
        if following.key in refused:
            return Step(
                "refused", following.attempt, following.preparation, of="prepare"
            )
        live = live_preparations(request)
        pending = [row for row in live if not discarded(row)]
        if pending:
            row = pending[0]

            def act():
                """Discard the live preparation that blocks this attempt."""
                discard(row)
                return Step("discard_prior", attempt.number, number)

            # Its own refusal key, apart from discarding the attempt's own.
            step = Step("discard_prior", attempt.number, number)
        elif live or (latest is not None and not disposed(latest)):
            return Step("disposal", attempt.number, number)
        else:
            step = Step("prepare", attempt.number, number + 1)

            def act():
                """Request the attempt's next preparation."""
                return _prepare(request, attempt, number + 1, request_preparation)

    if step.key in refused:
        return Step("refused", step.attempt, step.preparation, of=step.action)
    try:
        with transaction.atomic():
            return act()
    except (DatabaseError, PermissionError, StaleRecordError) as error:
        if isinstance(error, StaleRecordError):
            # The owner found the inputs moved under its own lock: next pass.
            return Step("inputs", attempt.number, number)
        if not _refused(error):
            raise
        return Step("refused", step.attempt, step.preparation, of=step.action)


def _prepare(request, attempt, number, request_preparation):
    """Request preparation ``number`` of ``attempt`` as the go-live requester."""
    key = preparation_key(request.pk, attempt.number, number)
    request_preparation(
        transition_id=request.pk,
        request_key=key,
        actor_id=request.initiated_by_id,
        correlation_id=key,
        admit=_admit(request),
    )
    return Step("prepare", attempt.number, number)


class GoLiveProducer:
    """The scheduler's go-live producer: one step of link work per loop.

    It remembers, for this process only, the steps refused for lack of
    authority (so they are not asked again every loop) and the stops it has
    logged (so each is logged once). A restarted scheduler may ask a refused
    step once more, which is harmless.
    """

    def __init__(self):
        self.refused = set()
        self.logged = set()

    def __call__(self, guard):
        """Take at most one step under the work order; return what it did."""
        from parishkit.stewardship.jobs.ownership import database_now
        from parishkit.stewardship.jobs.scheduler import SchedulerGuard

        from .work_locks import work_transaction

        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Go-live sequencing requires actual scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError("Go-live sequencing must own its transaction.")
        guard.check()
        # Most loops have no go-live at all, or only wait: decide that
        # without the lock.
        with transaction.atomic():
            waiting = precheck(database_now())
        if waiting is not None:
            self._log(waiting)
            return ()
        with work_transaction():
            request = open_request()
            step = advance(database_now(), refused=self._scoped(request))
        guard.check()
        if step.action == "refused" and request is not None:
            self.refused.add((request.pk, step.key))
        self._log(step, request)
        return (step,) if step.action in {"prepare", "discard", "discard_prior"} else ()

    def _scoped(self, request):
        """This request's refused step keys."""
        if request is None:
            return frozenset()
        return frozenset(key for owner, key in self.refused if owner == request.pk)

    def _log(self, step, request=None):
        """Log a stop or refusal once per request and step, in the process log."""
        event = {
            "refused": Event.GO_LIVE_STEP_REFUSED,
            "exhausted": Event.GO_LIVE_SEQUENCING_STOPPED,
            "hold_ended": Event.GO_LIVE_SEQUENCING_STOPPED,
        }.get(step.action)
        if event is None:
            return
        if request is None:
            with transaction.atomic():
                request = open_request()
        if request is None or (request.pk, step.action, step.key) in self.logged:
            return
        self.logged.add((request.pk, step.action, step.key))
        with correlation(request.pk):
            emit(event, level=logging.INFO)
