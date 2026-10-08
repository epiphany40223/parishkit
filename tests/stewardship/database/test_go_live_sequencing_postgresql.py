"""Go-live sequencing: attempts, the refresh hold and automatic link work (#462).

A go-live request whose cleanup completed, started by a real Administrator.
Start's refresh is queued with ``begin_attempt``; promotions are real source
promotions. The scheduler's producer runs as the restricted scheduler login
under a real scheduler session, and the link tasks it creates run as the
worker, so every step goes through the owners and their SQL checks.
"""

import logging
from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.campaigns import go_live_sequencing as sequencing
from parishkit.stewardship.campaigns.activation_models import (
    ProductionTokenCancellation,
    ProductionTokenPreparation,
)
from parishkit.stewardship.campaigns.activation_tasks import token_handler
from parishkit.stewardship.campaigns.activation_tokens import (
    CLEANUP_TASK_TYPE,
    TASK_TYPE,
)
from parishkit.stewardship.campaigns.go_live_sequencing import (
    ATTEMPT_CAP,
    REFRESH_HOLD,
    GoLiveProducer,
    attempts,
    begin_attempt,
    held_until,
    preparation_key,
    refresh_command_id,
    refreshes_held,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source.refresh_models import SourceRefreshCommand

from .auth_builders import signed_in, unguarded
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_scheduler_link_preparation_postgresql import cleaned_up
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def go_live(response_service, google):
    """The requester and their cleaned-up go-live request."""
    _, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get().pk
    request = ProductionTransitionRequest.objects.get(
        pk=cleaned_up(response_service, admin)
    )
    return response_service, admin, request


def start(request, admin):
    """Begin the next attempt as Start (or Refresh and prepare again) will."""
    return begin_attempt(
        request,
        actor_id=admin,
        correlation_id=uuid4(),
        authorize=lambda scope: True,
    )


def refreshed(harness):
    """Promote a real full refresh of the unchanged corpus."""
    snapshot, claim = prepare(response_source())
    return promote(snapshot, claim, harness.campaign, harness.rings)


def step():
    """One scheduler pass of the go-live producer, as the scheduler login."""
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        scheduler_session() as guard,
    ):
        return GoLiveProducer()(guard)


def run_link_task(harness, task_id, *, cleanup=False):
    """Run one link task as the worker, through the maintained dispatcher."""
    handlers = {
        TASK_TYPE: token_handler(public=harness.rings.public),
        CLEANUP_TASK_TYPE: token_handler(cleanup=True),
    }
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            task_id, queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers=handlers
        )


def current(request):
    """The request as stored now."""
    return ProductionTransitionRequest.objects.get(pk=request.pk)


def test_attempts_are_their_refresh_commands_with_bounded_holds(go_live):
    """Attempt n is refresh command uuid5(request, "refresh:n"); holds end on time."""
    harness, admin, request = go_live
    assert attempts(request) == () and not refreshes_held(database_now())
    assert start(request, admin) == 1
    command = SourceRefreshCommand.objects.get(pk=refresh_command_id(request.pk, 1))
    assert (command.cause, command.actor_id) == ("manual", admin)
    (first,) = attempts(request)
    # Before its refresh promotes, the hold lasts at most the cap.
    assert first.promoted_at is None
    assert first.hold_end == first.started_at + ATTEMPT_CAP
    assert refreshes_held(database_now())
    promoted = refreshed(harness)
    (first,) = attempts(request)
    assert first.promoted_at == promoted.promoted_at
    assert first.hold_end == promoted.promoted_at + REFRESH_HOLD
    # The data-age alarm counts a slot due inside the hold from its end.
    inside = first.started_at + timedelta(minutes=5)
    assert held_until(inside) == first.hold_end
    assert held_until(first.started_at - timedelta(minutes=1)) < first.started_at
    # A second attempt is a new command, never the first one again.
    assert start(request, admin) == 2
    assert len(attempts(request)) == 2


def test_cancelling_the_request_ends_the_hold_at_once(go_live):
    """Activation or cancellation ends the hold at its own time."""
    _, admin, request = go_live
    start(request, admin)
    stored = current(request)
    ended = database_now()
    # As the cancellation owner would record it (the guard is bypassed here).
    with unguarded():
        ProductionTransitionRequest.objects.filter(pk=request.pk).update(
            state="cancelled", version=stored.version + 1, updated_at=ended
        )
    assert not refreshes_held(database_now())
    (attempt,) = attempts(current(request))
    assert attempt.hold_end == ended


def test_the_producer_prepares_discards_and_prepares_again(go_live, caplog):
    """Prepare after the refresh; a later promotion discards and prepares again."""
    harness, admin, request = go_live
    # No attempt yet: the producer does nothing at all.
    assert step() == ()
    start(request, admin)
    # Waiting for the attempt's refresh.
    assert step() == ()
    assert not ProductionTokenPreparation.objects.exists()
    refreshed(harness)
    (prepared,) = step()
    assert (prepared.action, prepared.attempt, prepared.preparation) == (
        "prepare",
        1,
        1,
    )
    first = ProductionTokenPreparation.objects.get(
        request_key=preparation_key(request.pk, 1, 1)
    )
    assert first.actor_id == admin
    assert run_link_task(harness, first.task_id)
    # Current links: nothing to do.
    assert step() == ()
    # Another promotion makes them stale: discard, dispose, prepare again.
    refreshed(harness)
    (discarded,) = step()
    assert discarded.action == "discard"
    cancellation = ProductionTokenCancellation.objects.get(preparation=first)
    assert cancellation.actor_id == admin
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    (again,) = step()
    assert (again.action, again.preparation) == ("prepare", 2)
    assert ProductionTokenPreparation.objects.filter(
        request_key=preparation_key(request.pk, 1, 2)
    ).exists()


def test_the_producer_stops_at_the_preparation_cap(go_live, monkeypatch):
    """After the last allowed preparation goes stale, nothing more is requested."""
    harness, admin, request = go_live
    monkeypatch.setattr(sequencing, "MAX_PREPARATIONS", 1)
    start(request, admin)
    refreshed(harness)
    (prepared,) = step()
    preparation = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, preparation.task_id)
    refreshed(harness)
    (discarded,) = step()
    assert discarded.action == "discard"
    cancellation = ProductionTokenCancellation.objects.get()
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    assert step() == ()
    with work_transaction():
        assert sequencing.advance(database_now()).action == "exhausted"
    assert ProductionTokenPreparation.objects.count() == 1


def test_the_producer_stops_at_the_hold_end(go_live, monkeypatch):
    """Once the hold has ended, no preparation is requested for that attempt."""
    harness, admin, request = go_live
    monkeypatch.setattr(sequencing, "REFRESH_HOLD", timedelta(0))
    start(request, admin)
    refreshed(harness)
    assert not refreshes_held(database_now())
    assert step() == ()
    assert not ProductionTokenPreparation.objects.exists()


def test_a_refused_step_records_nothing(go_live, caplog):
    """A requester who is no longer an Administrator: refused, nothing written."""
    from django.db.models import F

    harness, admin, request = go_live
    start(request, admin)
    refreshed(harness)
    with unguarded():
        PortalUser.objects.filter(pk=admin).update(
            disabled=True, version=F("version") + 1
        )
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    assert step() == ()
    assert not ProductionTokenPreparation.objects.exists()
    assert any(r.msg is Event.GO_LIVE_STEP_REFUSED for r in caplog.records)


def test_scheduled_refreshes_wait_for_the_attempt(go_live, caplog):
    """Every scheduled slot, full or quick, waits; logging names the go-live.

    No durable bulk-send entry is written, and once the request is cancelled
    the slots are created as usual.
    """
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.source.production import produce_refreshes
    from parishkit.stewardship.source.refresh_models import SourceRefreshTick

    _, admin, request = go_live
    start(request, admin)
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    with scheduler_session() as guard:
        assert produce_refreshes(guard, skipped=set()) == ()
    assert not SourceRefreshTick.objects.exists()
    assert any(r.msg is Event.GO_LIVE_REFRESH_HELD for r in caplog.records)
    assert not any(r.msg is Event.SOURCE_HELD for r in caplog.records)
    assert not OperationalLog.objects.filter(event="source_refresh_held").exists()
    stored = current(request)
    with unguarded():
        ProductionTransitionRequest.objects.filter(pk=request.pk).update(
            state="cancelled", version=stored.version + 1, updated_at=database_now()
        )
    with scheduler_session() as guard:
        assert produce_refreshes(guard, skipped=set())
    assert SourceRefreshTick.objects.exists()
