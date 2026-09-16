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
