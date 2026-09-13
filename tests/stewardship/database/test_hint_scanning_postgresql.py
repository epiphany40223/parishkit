"""Lost-hint replay is fair, repeatable and has no durable execution side effects."""

import pytest

from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scanning import collect_hints

from .test_dispatch_postgresql import queued

pytestmark = pytest.mark.django_db(transaction=True)


def test_broker_loss_replays_same_task_without_new_run_or_attempt():
    """Discarding a collected page emulates broker loss, not a missing operation."""
    task = queued()
    handlers = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None
        )
    }
    first, cursor = collect_hints(handlers=handlers)
    repeated, _ = collect_hints(handlers=handlers)
    assert first == repeated and first[0].run_id == task.run_id
    assert cursor is None
    run = TaskRun.objects.get(pk=task.run_id)
    assert run.state == "queued" and run.attempt == 0 and run.events.count() == 1


def test_large_held_prefix_does_not_starve_later_eligible_work():
    """Pages advance across denied work; another queue need not wait for its release."""
    held, wanted = queued(), queued()
    handler = Handler(
        WorkQueue.GENERAL,
        lambda action, status: status.run_id == wanted.run_id,
        lambda *args: None,
    )
    handlers = {"dispatch_probe": handler}
    hints, cursor = collect_hints(handlers=handlers, limit=1)
    assert not hints and cursor.run_id == held.run_id
    hints, cursor = collect_hints(handlers=handlers, cursor=cursor, limit=1)
    assert hints[0].run_id == wanted.run_id
    hints, cursor = collect_hints(handlers=handlers, cursor=cursor, limit=1)
    assert not hints and cursor is None


def test_unknown_task_types_are_not_dynamically_imported_or_routed():
    """Unimplemented durable work remains untouched until its owner is installed."""
    queued()
    assert collect_hints(handlers={}) == ((), None)
    assert TaskRun.objects.get().state == "queued"


def test_raised_gate_denial_also_advances_past_held_work():
    """Concrete admission services raise safely without poisoning the whole page."""
    held, wanted = queued(), queued()

    def admit(action, status):
        """Reject one durable scope while a later independent operation is ready."""
        if status.run_id == held.run_id:
            raise PermissionError("Synthetic held work")
        return True

    registry = {"dispatch_probe": Handler(WorkQueue.GENERAL, admit, lambda *args: None)}
    hints, cursor = collect_hints(handlers=registry)
    assert [hint.run_id for hint in hints] == [wanted.run_id] and cursor is None
    assert TaskRun.objects.filter(state="queued").count() == 2
