"""Producer traversal boundaries do not need private data or a live scheduler."""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns import schedule_production as module
from parishkit.stewardship.campaigns.family_schedule_planning import (
    FamilyPlanningResult,
)
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.storage import StorageInvariantError


@pytest.mark.parametrize("limit", [0, 101, True, "20"])
def test_invalid_page_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="bounded process identity"):
        module.FamilyScheduleProducer(uuid4(), limit=limit)


def test_invalid_worker_and_fake_guard_are_rejected():
    """Reject forged caller identities before touching persistence."""
    with pytest.raises(ValueError, match="bounded process identity"):
        module.FamilyScheduleProducer("worker")
    with pytest.raises(TypeError, match="scheduler ownership"):
        module.FamilyScheduleProducer(uuid4())(object())


def test_nested_transaction_is_rejected(monkeypatch):
    """Traversal may not inherit a caller's long-lived transaction."""
    monkeypatch.setattr(module, "connection", Mock(in_atomic_block=True))
    with pytest.raises(StorageInvariantError, match="own its transactions"):
        module.FamilyScheduleProducer(uuid4())(Mock(spec=SchedulerGuard))


def test_missing_campaign_resets_traversal_without_reading_families(monkeypatch):
    """Removing the current campaign also discards the old traversal cursor."""
    monkeypatch.setattr(module, "connection", Mock(in_atomic_block=False))
    runtime, families = Mock(), Mock()
    runtime.objects.values_list.return_value.first.return_value = None
    monkeypatch.setattr(module, "SystemConfiguration", runtime)
    monkeypatch.setattr(module, "FamilyCampaign", families)
    producer = module.FamilyScheduleProducer(uuid4())
    producer.campaign_id, producer.cursor = uuid4(), uuid4()
    assert producer(Mock(spec=SchedulerGuard)) == ()
    assert producer.campaign_id is producer.cursor is None
    families.objects.filter.assert_not_called()


@pytest.mark.parametrize(
    "error", [PermissionError("scope"), StorageInvariantError("group")]
)
def test_one_failed_group_does_not_starve_the_next(monkeypatch, error):
    """A scoped failure advances only that Family, with safe invariant reporting."""
    monkeypatch.setattr(module, "connection", Mock(in_atomic_block=False))
    runtime, families, query, failure = Mock(), Mock(), Mock(), Mock()
    identifiers = [uuid4(), uuid4()]
    families.objects.filter.return_value = query
    query.order_by.return_value.values_list.return_value = identifiers
    monkeypatch.setattr(module, "SystemConfiguration", runtime)
    monkeypatch.setattr(module, "FamilyCampaign", families)
    monkeypatch.setattr(module, "emit_failure", failure)
    monkeypatch.setattr(module, "ensure_preparation_epoch", Mock())
    completed = FamilyPlanningResult(identifiers[-1])
    planner = Mock(side_effect=[error, completed])
    monkeypatch.setattr(module, "plan_family", planner)
    producer = module.FamilyScheduleProducer(uuid4(), limit=2)
    assert producer(Mock(spec=SchedulerGuard)) == (completed,)
    assert producer.cursor == identifiers[-1]
    assert planner.call_count == 2
    assert failure.call_count == int(isinstance(error, StorageInvariantError))


def sweep(monkeypatch, identifiers, planner):
    """Patch one campaign's Family page; return the query mock and the planner."""
    monkeypatch.setattr(module, "connection", Mock(in_atomic_block=False))
    families, query = Mock(), Mock()
    families.objects.filter.return_value = query
    query.filter.return_value = query
    query.order_by.return_value.values_list.return_value = identifiers
    monkeypatch.setattr(module, "SystemConfiguration", Mock())
    monkeypatch.setattr(module, "FamilyCampaign", families)
    monkeypatch.setattr(module, "ensure_preparation_epoch", Mock())
    monkeypatch.setattr(module, "plan_family", planner)
    monkeypatch.setattr(module, "_enqueue", Mock(return_value=True))
    return query


def test_sweep_plans_a_full_page_while_it_finds_mail(monkeypatch):
    """A launch send plans 100 Families per loop, then 20 once idle (#394)."""
    identifiers = [uuid4() for _ in range(250)]

    def plan(guard, *, family_id, worker_id):
        """Only the first page has new mail to prepare."""
        selected = uuid4() if planner.call_count <= 100 else None
        return FamilyPlanningResult(family_id, selected=selected)

    planner = Mock(side_effect=plan)
    sweep(monkeypatch, identifiers, planner)
    producer = module.FamilyScheduleProducer(uuid4())
    assert producer.limit == module.FAMILY_SWEEP_LIMIT == 100
    guard = Mock(spec=SchedulerGuard)
    assert len(producer(guard)) == 100
    assert producer.busy and producer.cursor == identifiers[99]
    # The mock returns the same list whatever the cursor; only the count matters.
    assert len(producer(guard)) == 100
    assert not producer.busy
    assert len(producer(guard)) == module.IDLE_FAMILY_SWEEP_LIMIT == 20


def test_sweep_stops_at_its_time_budget_and_resumes(monkeypatch):
    """A slow loop keeps its cursor rather than wrapping past unplanned Families."""
    identifiers = [uuid4() for _ in range(5)]
    clock = iter([0, 10, 31, 99])
    monkeypatch.setattr(module, "monotonic", lambda: next(clock))
    planner = Mock(
        side_effect=lambda guard, **kw: FamilyPlanningResult(kw["family_id"])
    )
    sweep(monkeypatch, identifiers, planner)
    emitted = Mock()
    monkeypatch.setattr(module, "emit", emitted)
    producer = module.FamilyScheduleProducer(uuid4(), seconds=30)
    assert module.FamilyScheduleProducer(uuid4()).seconds == 40
    assert len(producer(Mock(spec=SchedulerGuard))) == 2
    # Fewer than a page remained, but the sweep was cut short: no wrap.
    assert producer.cursor == identifiers[1]
    # The budget ending the page is logged with its limit and elapsed time.
    emitted.assert_called_once_with(
        module.Event.WORK_BUDGET_REACHED,
        timeout="family_sweep_budget",
        limit_seconds=30,
        elapsed_seconds=99,
    )


@pytest.mark.parametrize("seconds", [0, 61, True, "30"])
def test_invalid_time_budget_is_rejected(seconds):
    with pytest.raises(ValueError, match="bounded process identity"):
        module.FamilyScheduleProducer(uuid4(), seconds=seconds)


def test_reselected_held_work_does_not_keep_the_full_page(monkeypatch):
    """Only newly allocated preparation keeps the 100-Family page.

    A held occurrence (a paused send) is selected again every loop and
    reuses its ticket; that is not new work.
    """
    identifiers = [uuid4() for _ in range(250)]
    planner = Mock(
        side_effect=lambda guard, **kw: FamilyPlanningResult(
            kw["family_id"], selected=uuid4()
        )
    )
    sweep(monkeypatch, identifiers, planner)
    monkeypatch.setattr(module, "_enqueue", Mock(return_value=False))
    producer = module.FamilyScheduleProducer(uuid4())
    guard = Mock(spec=SchedulerGuard)
    assert len(producer(guard)) == 100
    assert not producer.busy
    assert len(producer(guard)) == 20


def test_enqueue_reports_only_a_new_ticket(monkeypatch):
    """An existing ticket for the occurrence is not new preparation work."""
    from types import SimpleNamespace

    from parishkit.stewardship.jobs import family_mail_models, family_mail_tasks

    existing, fresh = uuid4(), uuid4()
    model = Mock()
    model.objects.filter.return_value.values_list.return_value = [existing]
    monkeypatch.setattr(family_mail_models, "FamilyMailPreparation", model)
    for ticket, expected in (
        (SimpleNamespace(pk=existing), False),
        (SimpleNamespace(pk=fresh), True),
        (None, False),
    ):
        monkeypatch.setattr(
            family_mail_tasks, "enqueue_preparation", Mock(return_value=ticket)
        )
        assert module._enqueue(Mock(), uuid4()) is expected


def test_sweep_logs_its_counts_for_debugging(monkeypatch, caplog):
    """Each sweep writes a DEBUG line with what it visited (#394)."""
    import logging

    identifiers = [uuid4() for _ in range(3)]
    planner = Mock(
        side_effect=lambda guard, **kw: FamilyPlanningResult(kw["family_id"])
    )
    sweep(monkeypatch, identifiers, planner)
    emitted = Mock()
    monkeypatch.setattr(module, "emit", emitted)
    with caplog.at_level(logging.DEBUG, logger="parishkit.stewardship.debug"):
        module.FamilyScheduleProducer(uuid4())(Mock(spec=SchedulerGuard))
    emitted.assert_not_called()
    assert "visited 3 of a 100-Family page" in caplog.text
    assert "budget not reached" in caplog.text


def test_a_held_gate_resumes_at_the_full_page(monkeypatch):
    """After preparation was held, the first open page is a full one."""
    sweep(monkeypatch, [], Mock())
    monkeypatch.setattr(
        module, "ensure_preparation_epoch", Mock(side_effect=PermissionError)
    )
    producer = module.FamilyScheduleProducer(uuid4())
    producer.busy = False
    assert producer(Mock(spec=SchedulerGuard)) == ()
    assert producer.busy
