"""Actual cross-connection scheduler exclusion and lost broker/connection safety."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections

from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import (
    HintPublicationUnavailable,
    SchedulerBusy,
    SchedulerOwnershipLost,
    scan_once,
    scheduler_session,
)

from .test_dispatch_postgresql import queued

pytestmark = pytest.mark.django_db(transaction=True)


def contender():
    """Acquire using another backend, never advisory-lock reentrancy on this one."""
    try:
        with scheduler_session() as guard:
            guard.check()
            return True
    finally:
        connections.close_all()


def test_one_scheduler_session_excludes_another_until_release():
    """A second process cannot start scanning while the first owns its session."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        with scheduler_session() as guard:
            guard.check()
            future = pool.submit(contender)
            with pytest.raises(SchedulerBusy):
                future.result()
        assert pool.submit(contender).result()
    with pytest.raises(SchedulerOwnershipLost):
        guard.check()


def test_connection_loss_cannot_silently_continue_after_reconnect():
    """The retained guard cannot transfer to a fresh unlocked PostgreSQL session."""
    with scheduler_session() as guard:
        connection.close()
        connection.ensure_connection()
        with pytest.raises(SchedulerOwnershipLost):
            guard.check()
    with scheduler_session() as replacement:
        replacement.check()


def test_publish_failure_does_not_strand_or_claim_durable_work():
    """A failed publication replays safely and never runs under a database tx."""
    task, seen = queued(), []
    handlers = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None
        )
    }

    def failed(hint):
        """Model acceptance followed by an ambiguous transport response."""
        assert not connection.in_atomic_block
        seen.append(hint.run_id)
        raise HintPublicationUnavailable("synthetic broker failure")

    with scheduler_session() as guard:
        result = scan_once(guard, handlers=handlers, publish=failed)
        assert result.unconfirmed == 1 and result.published == 0
        result = scan_once(
            guard, handlers=handlers, publish=lambda hint: seen.append(hint.run_id)
        )
        assert result.published == 1 and result.unconfirmed == 0
    assert seen == [task.run_id, task.run_id]
    assert TaskRun.objects.get().state == "queued"


def test_one_unpublishable_hint_does_not_starve_later_pages():
    """Transport failure advances paging without deleting the failed operation."""
    first, second, seen = queued(), queued(), []
    handlers = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None
        )
    }

    def publish(hint):
        """Only the first synthetic route fails to confirm publication."""
        if hint.run_id == first.run_id:
            raise HintPublicationUnavailable("synthetic queue unavailable")
        seen.append(hint.run_id)

    with scheduler_session() as guard:
        result = scan_once(guard, handlers=handlers, publish=publish, limit=1)
        assert result.unconfirmed == 1 and result.cursor is not None
        result = scan_once(
            guard, handlers=handlers, publish=publish, cursor=result.cursor, limit=1
        )
        assert result.published == 1 and seen == [second.run_id]
    assert TaskRun.objects.filter(state="queued").count() == 2
