"""Execution identities separate reactivation from delivery recovery and coverage."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedules import occurrence_key


def test_production_cycles_and_deliverability_generations_have_distinct_keys():
    """An unchanged revision can run again without aliasing an earlier recovery."""
    identity = uuid4(), "production", "family:1", "once"
    keys = {
        occurrence_key(*identity),
        occurrence_key(*identity, production_cycle=1),
        occurrence_key(*identity, recovery_generation=1),
        occurrence_key(*identity, production_cycle=1, recovery_generation=1),
    }
    assert len(keys) == 4
    assert occurrence_key(*identity) == occurrence_key(*identity, production_cycle=0)


@pytest.mark.parametrize("cycle", [-1, True, "1", 2**63])
def test_cycle_rejects_invalid_values(cycle):
    """Do not silently coerce malformed durable identities."""
    with pytest.raises(ValueError, match="Production execution cycle"):
        occurrence_key(uuid4(), "production", "admins", "date", production_cycle=cycle)


def test_testing_cannot_claim_a_production_cycle():
    """Testing is still separated by its own rehearsal epoch and cleanup owner."""
    with pytest.raises(ValueError, match="Production execution cycle"):
        occurrence_key(uuid4(), "testing", "admins", "date", production_cycle=1)
