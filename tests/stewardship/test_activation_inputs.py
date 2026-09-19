"""Activation input comparisons must reject stale or malformed prepared manifests."""

from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.activation_inputs import TokenPreparationInputs


def inputs():
    """Synthetic identities describe a valid empty population without real keys."""
    return TokenPreparationInputs(uuid4(), uuid4(), 1, uuid4(), "a" * 64, "b" * 64, 0)


def generation(value):
    """Expose the same metadata projection the owning database reader provides."""
    fields = asdict(value)
    fields["coverage_digest"] = fields.pop("eligibility_digest")
    fields["coverage_count"] = fields.pop("eligible_count")
    return SimpleNamespace(
        **fields, state="ready", completed_at=datetime(2026, 9, 19, tzinfo=UTC)
    )


def test_current_empty_population_has_a_complete_manifest():
    """Zero eligible Families is valid, but still needs an explicit ready manifest."""
    value = inputs()
    assert value.covers(generation(value))
    assert TokenPreparationInputs.retained(SimpleNamespace(**asdict(value))) == value


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("configuration_id", uuid4()),
        ("source_snapshot_id", uuid4()),
        ("source_generation", 2),
        ("credential_epoch", uuid4()),
        ("key_inventory_digest", "c" * 64),
        ("coverage_digest", "c" * 64),
        ("coverage_count", 1),
        ("state", "building"),
        ("state", "cancelled"),
        ("state", "active"),
        ("completed_at", None),
    ],
)
def test_manifest_must_match_every_pinned_input(field, replacement):
    """A matching count alone cannot bless stale source, epoch, keys or config."""
    value = inputs()
    prepared = generation(value)
    setattr(prepared, field, replacement)
    assert not value.covers(prepared)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("configuration_id", "not-an-id"),
        ("source_snapshot_id", None),
        ("credential_epoch", "not-an-id"),
        ("source_generation", True),
        ("source_generation", 0),
        ("source_generation", 2**63),
        ("eligible_count", False),
        ("eligible_count", -1),
        ("eligible_count", 2**63),
        ("key_inventory_digest", "x" * 64),
        ("key_inventory_digest", None),
        ("eligibility_digest", "b" * 63),
        ("eligibility_digest", "B" * 64),
    ],
)
def test_invalid_pinned_input_is_rejected(field, replacement):
    """Boundary validation rejects bad metadata, not behavior of the crypto library."""
    fields = asdict(inputs()) | {field: replacement}
    with pytest.raises(ValueError):
        TokenPreparationInputs(**fields)
