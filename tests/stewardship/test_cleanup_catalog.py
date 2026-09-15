"""The closed inventory fingerprint contains identities, never arbitrary payloads."""

from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.campaigns.cleanup_catalog import (
    CleanupCategory,
    CleanupTarget,
    summarize_targets,
)


def test_exact_identity_and_category_change_inventory_digest():
    """Equal category counts never substitute for exact captured membership."""
    first = CleanupTarget(CleanupCategory.SUBMISSION, UUID(int=1))
    second = CleanupTarget(CleanupCategory.SUBMISSION, UUID(int=2))
    other = CleanupTarget(CleanupCategory.PROPOSAL, UUID(int=1))
    summaries = [summarize_targets([item]) for item in (first, second, other)]
    assert len({item.digest for item in summaries}) == 3
    assert summaries[0].counts == summaries[1].counts == {"submissions": 1}
    assert summarize_targets(iter([first, second])).counts == {"submissions": 2}
    assert summarize_targets([first, second]) == summarize_targets([first, second])


@pytest.mark.parametrize("category", ["submissions", "live", None, True, 1])
def test_target_rejects_noncanonical_categories(category):
    """A request string or model name cannot create a new deletion class."""
    with pytest.raises(ValueError, match="canonical category"):
        CleanupTarget(category, uuid4())


@pytest.mark.parametrize("identifier", [None, True, 1, "private-session-key", {}])
def test_target_rejects_non_uuid_identity_without_echo(identifier):
    """Never accept session keys or private values as a target primary key."""
    with pytest.raises(ValueError) as error:
        CleanupTarget(CleanupCategory.SESSION_DATA, identifier)
    assert "private-session-key" not in str(error.value)


def test_manifest_rejects_duplicate_or_unordered_targets():
    """Streaming counts cannot overstate a duplicated or reordered selection."""
    first = CleanupTarget(CleanupCategory.SUBMISSION, UUID(int=1))
    second = CleanupTarget(CleanupCategory.SUBMISSION, UUID(int=2))
    earlier_category = CleanupTarget(CleanupCategory.PROPOSAL, UUID(int=3))
    for targets in ([first, first], [second, first], [first, earlier_category]):
        with pytest.raises(ValueError, match="unique and canonically ordered"):
            summarize_targets(targets)
    assert summarize_targets([earlier_category, first, second]).total == 3


def test_empty_inventory_is_deterministic_and_not_a_nonempty_manifest():
    """Zero Testing data still has exact evidence, not an unspecified sentinel."""
    empty = summarize_targets([])
    assert empty == summarize_targets(iter(()))
    assert empty.total == 0 and dict(empty.counts) == {}
    assert (
        empty.digest
        != summarize_targets(
            [CleanupTarget(CleanupCategory.SUBMISSION, uuid4())]
        ).digest
    )


def test_inventory_rejects_untyped_values_and_target_is_private_immutable():
    """Input errors and ordinary repr must not copy row identities into logs."""
    target = CleanupTarget(CleanupCategory.SUBMISSION, uuid4())
    assert str(target.identifier) not in repr(target)
    with pytest.raises(FrozenInstanceError):
        target.identifier = uuid4()
    with pytest.raises(ValueError, match="typed targets"):
        summarize_targets([{"private": "answer"}])
