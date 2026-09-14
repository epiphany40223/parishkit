"""Bounded non-sensitive cleanup summary inputs are independently unit tested."""

from dataclasses import FrozenInstanceError

import pytest

from parishkit.stewardship.campaigns.production_storage import (
    CleanupInventory,
    TestingSummary,
)


def test_inventory_detaches_counts_and_preserves_total():
    """Neither caller mutation nor direct assignment can revise acknowledged totals."""
    counts = {"sessions": 2, "submissions": 3}
    inventory = CleanupInventory("a" * 64, counts)
    counts["sessions"] = 10
    assert inventory.total == 5 and inventory.counts["sessions"] == 2
    with pytest.raises(TypeError):
        inventory.counts["sessions"] = 99
    with pytest.raises(FrozenInstanceError):
        inventory.digest = "b" * 64
    assert CleanupInventory("a" * 64, {}).total == 0


@pytest.mark.parametrize(
    "counts",
    [
        None,
        [],
        "private",
        {"private name": 1},
        {"sessions": True},
        {"sessions": -1},
        {"sessions": 1.0},
        {"sessions": "1"},
        {"sessions": 9223372036854775808},
        {f"category_{number}": 0 for number in range(101)},
    ],
)
def test_invalid_cleanup_counts_have_generic_errors(counts):
    """Private source payloads cannot be retained as cleanup totals or echoed."""
    with pytest.raises(ValueError) as error:
        CleanupInventory("a" * 64, counts)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("digest", [None, 1, "", "a" * 63, "A" * 64, "private"])
def test_manifest_requires_an_exact_fingerprint(digest):
    """A filename, note or raw ID is not evidence that a manifest was inventoried."""
    with pytest.raises(ValueError, match="Invalid Production evidence fingerprint"):
        CleanupInventory(digest, {})


@pytest.mark.parametrize(
    "values",
    [
        (0, 1, 0, 0, 0, 0),
        (0, 0, 1, 0, 0, 0),
        (0, 0, 1, 1, 1, 0),
        (0, 0, 0, -1, 1, 0),
        (False, 0, 0, 0, 0, 0),
    ],
)
def test_aggregate_requires_exact_terminal_counts(values):
    """Unresolved/nonterminal deliveries cannot hide in a claimed terminal summary."""
    with pytest.raises(ValueError):
        TestingSummary("b" * 64, *values)
    assert TestingSummary("b" * 64, 3, 2, 3, 1, 1, 1).messages == 3
