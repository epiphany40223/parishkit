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
from parishkit.stewardship.storage import StaleRecordError

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


def start(request, admin, number=None):
    """Begin the next attempt as Start (or Refresh and prepare again) will."""
    return begin_attempt(
        request,
        number=len(attempts(request)) + 1 if number is None else number,
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
    # Cleanup completed before the refresh promoted, so the hour runs from
    # the promotion.
    assert first.hold_end == promoted.promoted_at + REFRESH_HOLD
    # The data-age alarm counts a slot due inside the hold from its end.
    inside = first.started_at + timedelta(minutes=5)
    assert held_until(inside) == first.hold_end
    assert held_until(first.started_at - timedelta(minutes=1)) < first.started_at
    # A second attempt is a new command, never the first one again.
    assert start(request, admin) == 2
    assert len(attempts(request)) == 2
    # A replayed click returns the attempt it began; a stale page's number
    # cannot begin another.
    assert start(request, admin, number=2) == 2
    with pytest.raises(StaleRecordError):
        start(request, admin, number=4)
    with pytest.raises(StaleRecordError):
        start(request, admin, number=1)
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
    producer = GoLiveProducer()
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        scheduler_session() as guard,
    ):
        assert producer(guard) == ()
        assert producer(guard) == ()
    assert not ProductionTokenPreparation.objects.exists()
    # Logged once, and the refused step was not asked again.
    refusals = [r for r in caplog.records if r.msg is Event.GO_LIVE_STEP_REFUSED]
    assert len(refusals) == 1
    assert producer.refused == {(request.pk, ("prepare", 1, 1))}


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


def test_a_second_attempt_discards_the_first_attempts_links(go_live):
    """Attempt 2 first discards attempt 1's live links, then prepares (H1)."""
    harness, admin, request = go_live
    start(request, admin)
    refreshed(harness)
    (prepared,) = step()
    first = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, first.task_id)
    # Refresh and prepare again begins attempt 2 with its own refresh.
    start(request, admin)
    refreshed(harness)
    (discarded,) = step()
    assert (discarded.action, discarded.attempt) == ("discard_prior", 2)
    cancellation = ProductionTokenCancellation.objects.get(preparation=first)
    # Waiting for disposal: nothing else is asked.
    assert step() == ()
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    (again,) = step()
    assert (again.action, again.attempt, again.preparation) == ("prepare", 2, 1)
    assert ProductionTokenPreparation.objects.filter(
        request_key=preparation_key(request.pk, 2, 1)
    ).exists()


def test_a_preparation_from_the_links_page_is_discarded_first(go_live):
    """A preparation made by hand on today's links page does not block (H1)."""
    from parishkit.stewardship.campaigns.activation_tokens import request_preparation

    harness, admin, request = go_live
    start(request, admin)
    refreshed(harness)
    manual = request_preparation(
        transition_id=request.pk,
        request_key=uuid4(),
        actor_id=admin,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    (discarded,) = step()
    assert discarded.action == "discard_prior"
    cancellation = ProductionTokenCancellation.objects.get(preparation=manual)
    assert run_link_task(harness, cancellation.task_id, cleanup=True)
    # The hand-made preparation's own task, still queued, now ends cancelled.
    run_link_task(harness, manual.task_id)
    (prepared,) = step()
    assert (prepared.action, prepared.preparation) == ("prepare", 1)


def test_held_until_reads_as_the_scheduler_and_the_worker(go_live):
    """The health check (worker) and refresh producer (scheduler) can read it."""
    harness, admin, request = go_live
    start(request, admin)
    refreshed(harness)
    (attempt,) = attempts(request)
    inside = attempt.started_at + timedelta(minutes=1)
    for role in (ServiceRole.SCHEDULER, ServiceRole.WORKER):
        with task_login(role, exact=True, reconnect=True):
            assert held_until(inside) == attempt.hold_end
            assert refreshes_held(database_now()) is True


def links_page_preparation(request, admin):
    """A preparation an Administrator made by hand on today's links page."""
    from parishkit.stewardship.campaigns.activation_tokens import request_preparation

    return request_preparation(
        transition_id=request.pk,
        request_key=uuid4(),
        actor_id=admin,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )


def test_an_exhausted_attempt_leaves_a_hand_made_preparation_alone(
    go_live, monkeypatch
):
    """With no preparation left to make, nobody else's links are discarded."""
    harness, admin, request = go_live
    monkeypatch.setattr(sequencing, "MAX_PREPARATIONS", 1)
    start(request, admin)
    refreshed(harness)
    step()
    own = ProductionTokenPreparation.objects.get()
    assert run_link_task(harness, own.task_id)
    refreshed(harness)
    step()
    assert run_link_task(
        harness, ProductionTokenCancellation.objects.get().task_id, cleanup=True
    )
    manual = links_page_preparation(request, admin)
    assert step() == ()
    assert not ProductionTokenCancellation.objects.filter(preparation=manual).exists()


def test_a_refused_next_preparation_leaves_a_hand_made_one_alone(go_live):
    """Once the attempt's next preparation was refused, nothing is discarded."""
    harness, admin, request = go_live
    start(request, admin)
    refreshed(harness)
    manual = links_page_preparation(request, admin)
    with (
        task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
        work_transaction(),
    ):
        taken = sequencing.advance(
            database_now(), refused=frozenset({("prepare", 1, 1)})
        )
    assert (taken.action, taken.of) == ("refused", "prepare")
    assert not ProductionTokenCancellation.objects.filter(preparation=manual).exists()


def test_the_latest_hold_end_is_the_last_one_already_passed(go_live, monkeypatch):
    """last_hold_end names the newest hold end at or before the instant."""
    from parishkit.stewardship.campaigns.go_live_sequencing import last_hold_end

    harness, admin, request = go_live
    assert last_hold_end(database_now()) is None
    monkeypatch.setattr(sequencing, "REFRESH_HOLD", timedelta(0))
    start(request, admin)
    refreshed(harness)
    (first,) = attempts(request)
    assert last_hold_end(first.hold_end) == first.hold_end
    assert last_hold_end(first.hold_end - timedelta(microseconds=1)) is None
    start(request, admin)
    refreshed(harness)
    first, second = attempts(request)
    assert last_hold_end(database_now()) == max(first.hold_end, second.hold_end)
