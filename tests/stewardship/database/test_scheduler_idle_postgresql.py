"""An idle scheduler loop skips the global work-order lock (#715).

Each producer first asks "is there anything to do?" without the lock, on the
loop's LoopSettings rows and a fresh clock, and joins the work order only
when the answer is yes. These tests run the real compiled producer under
the scheduler's exact login on a live campaign: once nothing is due, a whole
loop takes the lock no more than once (the operational intake producer's
minute rollover); work that falls due is still started on the loop it falls
due; and the unlocked planning scope decides exactly as the locked one.
"""

from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship import runtime_process
from parishkit.stewardship.campaigns import schedule_production, work_locks
from parishkit.stewardship.campaigns.family_schedule_planning import (
    _planning_scope,
    snapshot_planning_scope,
)
from parishkit.stewardship.campaigns.models import (
    CampaignBoundaryOccurrence,
    CampaignWorkGate,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.loop_settings import LoopSettings
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import scan_once, scheduler_session
from parishkit.stewardship.runtime_background import scheduler_handlers

from .auth_builders import unguarded
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def scheduling():
    """The scheduler's own session under its exact restricted login."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        yield guard


def drain():
    """Settle the work the loop allocated, as the absent workers would have.

    These tests have no consumers, so every allocated task stays due and its
    producer would keep finding it. Marking the tasks finished, and their
    boundary occurrences applied, leaves the idle state a running parish is
    in between bursts of work. Only a superuser can write these states.
    """
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_task_run SET state='succeeded', "
            "lease_expires_at=NULL, worker_id=NULL "
            "WHERE state IN ('queued','running','retry_wait','abandoned')"
        )
        cursor.execute(
            "UPDATE stewardship_campaign_boundary SET state='skipped', "
            "completed_at=clock_timestamp() WHERE state='pending'"
        )


class Loop:
    """The compiled scheduler producer plus the due-work scan, one loop a call.

    It also counts work-order lock takes per producer, keeping the real lock.
    """

    def __init__(self, store, monkeypatch):
        """Compile the producers and wrap the lock and producer boundary."""
        self.locks, self.current = Counter(), None
        real_lock, real_independent = (
            work_locks.lock_work_order,
            runtime_process.independent_producer,
        )

        def counting():
            """Record which producer took the lock."""
            self.locks[self.current] += 1
            return real_lock()

        def tagged(guard, operation, *args):
            """Name the running producer for the lock count."""
            target = getattr(operation, "func", operation)
            self.current = getattr(target, "__name__", type(target).__name__)
            try:
                return real_independent(guard, operation, *args)
            finally:
                self.current = None

        monkeypatch.setattr(work_locks, "lock_work_order", counting)
        monkeypatch.setattr(runtime_process, "independent_producer", tagged)
        self.produce = runtime_process.scheduler_producer(store)
        self.handlers, self.cursor = scheduler_handlers(), None

    def __call__(self, guard):
        """Run one loop's producers and scan; return the producers' results."""
        result = self.produce(guard)
        self.cursor = scan_once(
            guard, handlers=self.handlers, publish=lambda hint: None, cursor=self.cursor
        ).cursor
        return result


@pytest.fixture
def live(response_service, monkeypatch):
    """A live campaign a few days after its start, with the sweep checking often."""
    harness = activate_response_service(response_service)
    complete_empty_catchup(harness.campaign, uuid4())
    monkeypatch.setattr(schedule_production, "CHANGE_CHECK_SECONDS", 0)
    return harness


def idle(loop, instant):
    """Loop at ``instant`` and drain, until loops allocate nothing (idle)."""
    for _ in range(5):
        with campaign_clock(instant), scheduling() as guard:
            for _ in range(20):
                loop(guard)
        drain()
    loop.locks.clear()


def test_an_idle_loop_takes_no_work_order_lock(live, monkeypatch):
    """With nothing due, the producers skip the lock and re-read nothing locked.

    Operational intake enqueues one task per clock minute while a source scope
    is admitted, so a minute rollover during these loops may take the lock
    once; no other producer may.
    """
    loop = Loop(live.service.store, monkeypatch)
    instant = live.campaign.active_configuration.starts_at + timedelta(days=3)
    idle(loop, instant)
    with campaign_clock(instant), scheduling() as guard:
        for _ in range(5):
            loop(guard)
    assert set(loop.locks) <= {"produce_collection"}
    assert loop.locks["produce_collection"] <= 1


def test_each_loop_reads_its_own_settings(live, monkeypatch):
    """A settings change between loops is seen by the next loop (#715).

    Every loop builds a new LoopSettings, so nothing read in one loop is used
    in the next: an unreleased work gate added between two loops holds the
    next loop's digest producers before they read anything else.
    """
    from parishkit.stewardship.reports import digest_ownership

    seen, real = [], digest_ownership.DailyDigestProducer.__call__

    def spy(self, guard, *, settings=None):
        """Record which settings object each loop passed."""
        seen.append(settings)
        return real(self, guard, settings=settings)

    monkeypatch.setattr(digest_ownership.DailyDigestProducer, "__call__", spy)
    loop = Loop(live.service.store, monkeypatch)
    instant = live.campaign.active_configuration.starts_at + timedelta(days=3)
    idle(loop, instant)
    with campaign_clock(instant), scheduling() as guard:
        loop(guard)
        assert seen[-1].gated is False
    with unguarded():
        CampaignWorkGate.objects.create(
            campaign=live.campaign, request_id=uuid4(), initiated_by_id=uuid4()
        )
    with campaign_clock(instant), scheduling() as guard:
        loop(guard)
    assert seen[-1] is not seen[-2] and seen[-1].gated is True


def test_a_boundary_falling_due_starts_on_that_loop(live, monkeypatch):
    """Clock-driven work starts on the loop it falls due, as before #715.

    The unlocked read asks the database clock afresh on every loop, so the
    first loop after the close instant takes the lock and allocates it.
    """
    loop = Loop(live.service.store, monkeypatch)
    projection = live.campaign.active_configuration
    idle(loop, projection.starts_at + timedelta(days=3))
    assert not CampaignBoundaryOccurrence.objects.filter(kind="close").exists()
    with campaign_clock(projection.ends_at + timedelta(seconds=1)), scheduling() as g:
        loop(g)
    assert loop.locks["produce_boundaries"] == 1
    close = CampaignBoundaryOccurrence.objects.get(kind="close")
    assert close.state == "pending"
    assert TaskRun.objects.filter(
        task_type="campaign_boundary", idempotency_key=str(close.pk)
    ).exists()


@pytest.mark.parametrize("mode", ["testing", "production"])
@pytest.mark.parametrize("held", [False, True])
def test_snapshot_planning_scope_decides_as_the_locked_scope(
    response_service, mode, held
):
    """The unlocked loop scope admits and refuses exactly where the locked one does.

    Both use one predicate; this checks the inputs each passes it agree in
    Testing (a draft and its rehearsal epoch) and Production, before, during
    and after the campaign's dates, with and without post-close planning,
    and with a work gate held.
    """
    harness = response_service
    if mode == "production":
        harness = activate_response_service(response_service)
        complete_empty_catchup(harness.campaign, uuid4())
    if held:
        with unguarded():
            CampaignWorkGate.objects.create(
                campaign=harness.campaign, request_id=uuid4(), initiated_by_id=uuid4()
            )

    def decide(read, *args, **kwargs):
        """The scope's campaign, mode and epoch, or the refusal."""
        try:
            scope, epoch = read(*args, **kwargs)
        except PermissionError:
            return "held"
        return scope.campaign.pk, scope.runtime.mode, epoch

    decisions = []
    starts = harness.campaign.active_configuration.starts_at
    for offset in (timedelta(days=-1), timedelta(days=3), timedelta(days=400)):
        for postclose in (False, True):
            with campaign_clock(starts + offset), scheduling():
                unlocked = decide(
                    snapshot_planning_scope, LoopSettings(), postclose=postclose
                )
                with work_locks.work_transaction():
                    locked = decide(
                        _planning_scope,
                        LoopSettings().campaign_id,
                        postclose=postclose,
                    )
            assert unlocked == locked, (offset, postclose)
            decisions.append(locked)
    # Neither branch is vacuous: an open campaign admits during its dates.
    assert ("held" in decisions) and (held or any(d != "held" for d in decisions))
