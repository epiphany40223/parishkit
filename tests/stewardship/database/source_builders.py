"""Synthetic source task/corpus builders shared by the Phase 2 database suites."""

from uuid import uuid4

from parishkit.stewardship.jobs.storage import change_run, enqueue
from parishkit.stewardship.source.version_models import ENTITY_MODELS


def running_source_task():
    """Create an admitted synthetic execution without a broker or provider."""
    status = enqueue(
        task_type="source_probe",
        domain_request_id=uuid4(),
        actor_id=None,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    worker = uuid4()
    status = change_run(
        run_id=status.run_id,
        expected_version=status.version,
        action="claim",
        actor_id=worker,
        correlation_id=uuid4(),
        lease_seconds=60,
        admit=lambda *args: True,
    )
    return dict(task_id=status.run_id, task_fence=status.fence, worker_id=worker)


def source_corpus(name="Synthetic"):
    """Every collection is explicit, including valid empty optional collections."""
    return {
        **{kind: {} for kind in ENTITY_MODELS},
        "family": {"1": {"name": name}},
        "member": {"2": {"name": "Member", "family_key": "1"}},
        "contact": {
            "member:2:email": {
                "owner_kind": "member",
                "owner_key": "2",
                "email": "example@example.invalid",
            }
        },
        "address": {
            "family:1:home": {
                "owner_kind": "family",
                "owner_key": "1",
                "street": "1 Example St",
            }
        },
        "ministry": {"3": {"name": "Synthetic Ministry"}},
        "roster": {
            "2:3": {"member_key": "2", "ministry_key": "3", "role": "Chairperson"}
        },
        "fund": {"4": {"name": "Synthetic Fund"}},
        "pledge": {"5": {"family_key": "1", "fund_key": "4", "amount": "100.00"}},
        "contribution": {"6": {"family_key": "1", "fund_key": "4", "amount": "25.00"}},
    }
