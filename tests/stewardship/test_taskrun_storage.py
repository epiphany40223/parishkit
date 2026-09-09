"""Pure validation rejects malformed or private-shaped metadata before DB access."""

from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.storage import change_run, enqueue, retry_failed


@pytest.mark.parametrize(
    "changes",
    [
        {"task_type": "private value"},
        {"task_type": None},
        {"task_type": ""},
        {"actor_id": "private value"},
        {"domain_request_id": "private value"},
        {"idempotency_key": "private value"},
        {"correlation_id": None},
    ],
)
def test_invalid_enqueue_never_reaches_database(changes):
    """Identifiers are opaque and errors never echo submitted private text."""
    arguments = dict(
        task_type="storage_probe",
        domain_request_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: None,
    )
    with pytest.raises((ValueError, TypeError)) as error:
        enqueue(**(arguments | changes))
    assert "private value" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "private value"},
        {"action": "private value"},
        {"action": []},
        {"expected_version": True},
        {"expected_version": 0},
        {"actor_id": None},
        {"action": "lease_expired", "lease_seconds": None},
        {"actor_id": "private value"},
        {"lease_seconds": 0},
        {"lease_seconds": 301},
        {"lease_seconds": True},
        {"action": "complete", "lease_seconds": 60},
        {"retry_seconds": 1},
        {"progress": (1, 2)},
        {"action": "progress", "lease_seconds": None, "progress": [1, 2]},
        {"action": "progress", "lease_seconds": None, "progress": (2, 1)},
        {"action": "progress", "lease_seconds": None, "progress": (False, 1)},
        {"action": "progress", "lease_seconds": None, "progress": (0, 2**63)},
        {"action": "retryable_failure", "lease_seconds": None, "retry_seconds": 86401},
    ],
)
def test_invalid_change_never_reaches_database(changes):
    """Unknown actions, booleans, out-of-range values and stray inputs fail closed."""
    arguments = dict(
        run_id=uuid4(),
        action="claim",
        expected_version=1,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        lease_seconds=60,
        admit=lambda *args: None,
    )
    with pytest.raises((ValueError, TypeError)) as error:
        change_run(**(arguments | changes))
    assert "private value" not in str(error.value)


def test_retry_requires_real_actor_and_command_identifiers():
    """A terminal retry cannot fabricate a user or reuse a plaintext command key."""
    with pytest.raises(TypeError):
        retry_failed(
            run_id=uuid4(),
            command_id=uuid4(),
            actor_id=None,
            correlation_id=uuid4(),
            admit=lambda *args: None,
        )
