"""Delivery controls forecast configured slots, never future recipient counts."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.stewardship.accounts import delivery_forecast
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_factory import campaign, schedule


def forecast(monkeypatch, kinds, now):
    """Use real civil scheduling with only the database enumeration substituted."""
    owner = campaign()
    definitions = []
    for kind in kinds:
        values = schedule(
            owner["id"],
            kind=kind,
            date="2026-10-01" if kind in {"initial", "reminder"} else None,
            **({"weekday": 0} if kind == "weekly_digest" else {}),
        )["values"]
        definitions.append(
            SimpleNamespace(kind=kind, current_revision=SimpleNamespace(values=values))
        )
    manager = Mock()
    query = (
        manager.filter.return_value.select_related.return_value.order_by.return_value
    )
    query.__getitem__ = Mock(return_value=definitions)
    monkeypatch.setattr(delivery_forecast.ScheduleDefinition, "objects", manager)
    return delivery_forecast.next_due(
        SimpleNamespace(active_configuration=SimpleNamespace(values=owner["values"])),
        now,
    )


def test_shared_instant_counts_definitions_not_family_population(monkeypatch):
    """Only earliest ties contribute, even when later digest definitions exist."""
    result = forecast(
        monkeypatch,
        ["initial", "reminder", "reminder", "daily_digest", "weekly_digest"],
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert result == {
        "at": "2026-10-01T13:00:00+00:00",
        "count": 3,
        "types": {"initial": 1, "reminder": 2},
    }


@pytest.mark.parametrize("kinds", [[], ["initial", "daily_digest", "weekly_digest"]])
def test_no_future_slots_is_not_a_guarantee_of_an_empty_backlog(monkeypatch, kinds):
    """An exhausted configured schedule remains separate from the held inventory."""
    assert forecast(monkeypatch, kinds, datetime(2027, 1, 1, tzinfo=UTC)) == {
        "at": None,
        "count": 0,
        "types": {},
    }


def test_oversized_schedule_set_refuses_a_partial_forecast(monkeypatch):
    """The sentinel definition detects overflow instead of silently omitting it."""
    with pytest.raises(StorageInvariantError, match="configuration bounds"):
        forecast(monkeypatch, ["reminder"] * 103, datetime(2026, 10, 1, tzinfo=UTC))
