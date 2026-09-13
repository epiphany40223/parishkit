"""Pure input validation for source ownership, without database/provider access."""

from uuid import uuid4

import pytest

from parishkit.stewardship.source.leases import (
    SourceClaim,
    SourceFenceLost,
    _duration,
    _task,
    acquire_source,
    verify_source,
)
from parishkit.stewardship.storage import StorageInvariantError


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 3601, 1.2, "90"])
def test_durations_are_strict_and_bounded(value):
    """Never turn malformed timing configuration into an unbounded request."""
    with pytest.raises(ValueError, match="bounded positive"):
        _duration(value)


def test_unknown_phase_is_rejected_before_database_access():
    """Only documented source mutation classes may contend for the lease."""
    with pytest.raises(ValueError, match="Unknown"):
        acquire_source(task_id=uuid4(), task_fence=1, worker_id=uuid4(), phase="mail")


@pytest.mark.parametrize(
    "task_id,fence,worker",
    [("bad", 1, uuid4()), (uuid4(), True, uuid4()), (uuid4(), 1, None)],
)
def test_task_identity_must_be_exact(task_id, fence, worker):
    """Malformed owner identities fail before a query or provider call."""
    with pytest.raises(SourceFenceLost):
        _task(task_id, fence, worker)


def test_promotion_requires_an_outer_transaction():
    """Returning an unlocked fence cannot authorize a later promotion."""
    with pytest.raises(StorageInvariantError, match="outer transaction"):
        verify_source(SourceClaim(uuid4(), 1, uuid4(), 1, "full"))


@pytest.mark.parametrize("fence", [True, False, 0, -1, "1", 1.0, 2**63])
def test_source_fence_never_coerces_invalid_credentials(fence):
    """Task validation also requires a separately strict source mutation fence."""
    with pytest.raises(SourceFenceLost):
        SourceClaim(uuid4(), 1, uuid4(), fence, "full")
