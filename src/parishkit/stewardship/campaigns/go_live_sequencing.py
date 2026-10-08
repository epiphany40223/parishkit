"""Go-live sequencing: attempts, the refresh hold and automatic link work (#462).

Between Start go-live and Confirm Production the system runs the steps the
Administrator used to time by hand against the quarter-hour ParishSoft
refreshes (see docs/specs/stewardship/background-processing/spec.md, "Go-live
sequencing" and "Refreshes wait for go-live").

**Attempts.** Start go-live, and each later Refresh and prepare again, begin
an *attempt*, numbered from 1 within the transition request. An attempt is
nothing but its full refresh: an ordinary manual refresh command whose id is
``uuid5(request id, "refresh:<n>")``. So attempts need no table of their own:
attempt ``n`` exists exactly when that command row does, and it began when the
command was recorded. A repeated click or a crash cannot queue a second
refresh for the same attempt, because the command id is the same.

**Hold end.** From an attempt's start, scheduled refreshes wait until its
hold end: the earlier of ``REFRESH_HOLD`` after the first full refresh that
started after the attempt began has promoted, and ``ATTEMPT_CAP`` after the
attempt began (a refresh that never promotes). Activation or cancellation of
the request ends the hold at once.

**Link work.** The scheduler's producer (``GoLiveProducer``) asks the existing
owners, as the Administrator who started go-live, for the inactive Family
link preparation once cleanup is complete and the attempt's refresh has
promoted; it discards a preparation the data has moved past and asks for the
next one, up to ``MAX_PREPARATIONS`` per attempt. Preparation ``k`` of
attempt ``n`` has request key ``uuid5(request id, "prepare:<n>:<k>")``, which
does not depend on any input, so a preparation refused for an input that
changed for good never blocks the next number. The producer never confirms
Production, never starts cleanup, never retries a task and never changes
mode or campaign lifecycle. Migration 0025 lets the scheduler record those
two kinds of rows, for that Administrator only.

Nothing here does anything without an open go-live request with at least one
attempt, so a deployment whose campaign is live in Production is unaffected.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid5

from django.db import DatabaseError, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.observability import Event, correlation, emit
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .production_models import ProductionTransitionRequest

# Scheduled refreshes wait this long after the attempt's refresh promoted ...
REFRESH_HOLD = timedelta(minutes=60)
# ... and never longer than this after the attempt began.
ATTEMPT_CAP = timedelta(hours=3)
# The first preparation of an attempt and at most three re-preparations.
MAX_PREPARATIONS = 4
# The request states that own the go-live gate.
OPEN_STATES = (
    "cleanup_queued",
    "cleanup_running",
    "cleanup_retry_wait",
    "cleanup_failed",
    "cleanup_complete",
)
# A request that left those states ended its hold when it did.
ENDED_STATES = ("activated", "cancelled")


def refresh_command_id(request_id, number):
    """The refresh command id of attempt ``number`` of ``request_id``."""
    return uuid5(request_id, f"refresh:{number}")


def preparation_key(request_id, attempt, number):
    """The request key of preparation ``number`` of ``attempt``."""
    return uuid5(request_id, f"prepare:{attempt}:{number}")


def discard_key(request_id, attempt, number):
    """The request key of the discard of preparation ``number`` of ``attempt``."""
    return uuid5(request_id, f"discard:{attempt}:{number}")


@dataclass(frozen=True)
class Attempt:
    """One go-live attempt: its number, start, refresh promotion and hold end.

    ``promoted_at`` is when the first full refresh that started at or after
    ``started_at`` promoted (None while none has); ``hold_end`` is when
    scheduled refreshes resume.
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
        .order_by("promoted_at")
        .values_list("promoted_at", flat=True)
        .first()
    )


def attempts(request):
    """Every attempt of ``request``, oldest first.

    Attempt ``n`` exists while its refresh command does; numbering has no
    gaps, because each attempt is begun only as the next number.
    """
    from parishkit.stewardship.source.refresh_models import SourceRefreshCommand

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
        end = started + ATTEMPT_CAP
        if promoted is not None:
            end = min(end, promoted + REFRESH_HOLD)
        if request.state in ENDED_STATES:
            end = min(end, request.updated_at)
        found.append(Attempt(number, started, promoted, max(end, started)))
        number += 1


def latest_request():
    """The newest go-live request of the current Testing campaign, or None.

    Production has no current Testing campaign going live, so this is None
    for a deployment whose campaign is live.
    """
    runtime = SystemConfiguration.objects.only("current_campaign_id", "mode").first()
    if runtime is None or runtime.current_campaign_id is None:
        return None
    return (
        ProductionTransitionRequest.objects.filter(
            campaign_id=runtime.current_campaign_id
        )
        .order_by("-created_at", "-id")
        .first()
    )


def open_request():
    """The current campaign's go-live request while it owns the gate, or None."""
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
    """Where a go-live moves the data-age alarm's overdue point, if it does.

    A scheduled full slot that fell due while a go-live attempt held
    refreshes counts again only from that attempt's hold end, with the
    usual lateness margin measured from there. Returns the later point, or
    ``overdue`` unchanged. Only the latest request's attempts count: an
    earlier go-live's hold has long ended, and a newer full refresh has
    moved the overdue point past it.
    """
    if overdue is None:
        return None
    request = latest_request()
    for attempt in attempts(request) if request is not None else ():
        if attempt.started_at <= overdue < attempt.hold_end:
            return attempt.hold_end
    return overdue


def begin_attempt(request, *, actor_id, correlation_id, authorize):
    """Queue the next attempt's full refresh as ``actor_id``; return its number.

    For the web's Start go-live and Refresh and prepare again, which hold
    their own admission (``authorize``, as ``request_refresh`` takes it). The
    refresh is an ordinary manual request, so it coalesces with a full load
    already waiting, as any manual refresh does.
    """
    from parishkit.stewardship.source.requests import request_refresh

    number = len(attempts(request)) + 1
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


@dataclass(frozen=True)
class Step:
    """What one producer pass did: ``action`` and the attempt and preparation.

    ``action`` is one of "prepare", "discard" or a reason it waited: "none"
    (no open request or attempt), "cleanup", "hold_ended", "refresh",
    "inputs", "current", "disposal", "exhausted" or "refused".
    """

    action: str
    attempt: int | None = None
    preparation: int | None = None


def _admit(request):
    """The owners' admission callback: only this open, cleaned-up request."""

    def admit(transition, previous):
        """Admit link work for the go-live request this pass read."""
        return transition.pk == request.pk and transition.state == "cleanup_complete"

    return admit


def advance(instant):
    """Take at most one step of go-live link work at ``instant``.

    Runs inside the caller's work-order transaction. Each request goes
    through the existing owners, which recheck everything under their own
    locks; a refusal from them or from SQL is reported as "refused" and the
    pass changes nothing.
    """
    from .activation_cleanup import disposed, request_cancellation
    from .activation_inputs import TokenPreparationInputs
    from .activation_models import ProductionTokenCancellation
    from .activation_tokens import (
        current_inputs,
        request_preparation,
    )

    request = open_request()
    attempt = current_attempt(request)
    if request is None or attempt is None:
        return Step("none")
    if request.state != "cleanup_complete":
        return Step("cleanup", attempt.number)
    if instant >= attempt.hold_end:
        # Never chase scheduled refreshes: the page offers Refresh and
        # prepare again, which begins the next attempt.
        return Step("hold_ended", attempt.number)
    if attempt.promoted_at is None:
        return Step("refresh", attempt.number)
    try:
        current = current_inputs(request)
    except StaleRecordError:
        # The population is being rebuilt after a promotion, or cleanup is
        # not complete in every respect yet: wait for the next pass.
        return Step("inputs", attempt.number)
    made = preparations(request, attempt)
    latest = made[-1] if made else None
    number = len(made)
    try:
        with transaction.atomic():
            if latest is None:
                return _prepare(request, attempt, 1, request_preparation)
            discarded = ProductionTokenCancellation.objects.filter(
                preparation=latest
            ).exists()
            if not discarded and TokenPreparationInputs.retained(latest) == current:
                return Step("current", attempt.number, number)
            if not discarded:
                request_cancellation(
                    preparation_id=latest.pk,
                    request_key=discard_key(request.pk, attempt.number, number),
                    actor_id=request.initiated_by_id,
                    correlation_id=discard_key(request.pk, attempt.number, number),
                    admit=lambda preparation, previous: _admit(request)(
                        preparation.transition, previous
                    ),
                )
                return Step("discard", attempt.number, number)
            if not disposed(latest):
                return Step("disposal", attempt.number, number)
            if number >= MAX_PREPARATIONS:
                return Step("exhausted", attempt.number, number)
            return _prepare(request, attempt, number + 1, request_preparation)
    except (DatabaseError, PermissionError, StaleRecordError) as error:
        # The owners or SQL refused (the requester is no longer a current
        # Administrator, the gate moved on): nothing was recorded.
        with correlation(request.pk):
            emit(Event.GO_LIVE_STEP_REFUSED, level=logging.INFO)
        logging.getLogger("parishkit.stewardship.debug").debug(
            "Go-live link step refused: %s", type(error).__name__
        )
        return Step("refused", attempt.number, number)


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
    """The scheduler's go-live producer: one step of link work per loop."""

    def __call__(self, guard):
        """Take at most one step under the work order; return what it did."""
        from django.db import connection

        from parishkit.stewardship.jobs.ownership import database_now
        from parishkit.stewardship.jobs.scheduler import SchedulerGuard

        from .work_locks import work_transaction

        if not isinstance(guard, SchedulerGuard):
            raise TypeError("Go-live sequencing requires actual scheduler ownership.")
        if connection.in_atomic_block:
            raise StorageInvariantError("Go-live sequencing must own its transaction.")
        guard.check()
        # Most loops have no go-live at all: decide that without the lock.
        with transaction.atomic():
            if current_attempt(open_request()) is None:
                return ()
        with work_transaction():
            step = advance(database_now())
        guard.check()
        return (step,) if step.action in {"prepare", "discard"} else ()
