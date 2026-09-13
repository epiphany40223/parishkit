"""Abandoned exact work resumes without replacing input cutoffs or newer events."""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.reports.demand import claim_rebuild, complete_rebuild
from parishkit.stewardship.reports.facts import FactUnavailable, publish_fact_set
from parishkit.stewardship.reports.recovery import (
    fail_fact_set,
    recover_fact_set,
    recover_rebuild,
)

from .fact_builders import fact_fixture, staged_facts
from .source_builders import running_source_task
from .test_fact_demand_postgresql import due_demand
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def retire(owner):
    """End the old test execution through the genuine fenced TaskRun transition."""
    run = TaskRun.objects.get(pk=owner.run_id)
    change_run(
        run_id=run.pk,
        expected_version=run.version,
        action="permanent_failure",
        actor_id=owner.worker_id,
        correlation_id=uuid4(),
        fence=owner.fence,
        admit=permit,
    )
    task = running_source_task()
    return TaskClaim(task["task_id"], task["task_fence"], task["worker_id"])


def test_failed_exact_work_resumes_with_immutable_partial_chunks(tmp_path):
    """A failed calculation retains its input pin and can replay identical chunks."""
    inputs, owner, source = fact_fixture(tmp_path)
    record, _ = staged_facts(inputs, owner, source)
    fail_fact_set(record.pk, owner, code="calculation_interrupted", admit=permit)
    rival = running_source_task()
    rival = TaskClaim(rival["task_id"], rival["task_fence"], rival["worker_id"])
    with pytest.raises(FactUnavailable, match="live fact builder"):
        recover_fact_set(record.pk, rival, admit=permit)
    replacement = retire(owner)
    resumed = recover_fact_set(record.pk, replacement, admit=permit)
    assert resumed.state == "building" and resumed.failure_code == ""
    assert staged_facts(inputs, replacement, source)[0].pk == record.pk
    with pytest.raises(TaskOwnershipLost):
        publish_fact_set(record.pk, owner, admit=permit)
    assert publish_fact_set(record.pk, replacement, admit=permit).state == "ready"


def test_recover_claim_preserves_newer_revision_and_transfers_both_fences(tmp_path):
    """Recovery owns the old exact tuple, while revision 2 still waits independently."""
    inputs, owner, source = fact_fixture(tmp_path)
    due_demand(inputs)
    demand = claim_rebuild(
        inputs.campaign_id, inputs.population_scope, owner, admit=permit
    )
    next_demand = due_demand(replace(inputs, submission_watermark=2))
    record, _ = staged_facts(inputs, owner, source)
    replacement = retire(owner)
    with pytest.raises(FactUnavailable, match="Interactive recovery"):
        recover_fact_set(record.pk, replacement, admit=permit)
    resumed = recover_rebuild(demand.pk, replacement, revision=1, admit=permit)
    assert resumed.claimed_generation_id == record.pk
    assert resumed.pending_due_at == next_demand.pending_due_at
    assert resumed.pending_revision == 2 and resumed.claimed_revision == 1
    publish_fact_set(record.pk, replacement, admit=permit)
    complete = complete_rebuild(demand.pk, replacement, revision=1, admit=permit)
    assert complete.pending_revision == 2 and complete.pending_due_at is not None


def test_same_owner_can_retry_failed_claim_without_changing_its_revision(tmp_path):
    """A local recoverable calculation failure need not allocate a competing task."""
    inputs, owner, source = fact_fixture(tmp_path)
    due_demand(inputs)
    demand = claim_rebuild(
        inputs.campaign_id, inputs.population_scope, owner, admit=permit
    )
    record, _ = staged_facts(inputs, owner, source)
    fail_fact_set(record.pk, owner, code="retryable_calculation", admit=permit)
    resumed = recover_rebuild(demand.pk, owner, revision=1, admit=permit)
    assert resumed.version == demand.version
    assert publish_fact_set(record.pk, owner, admit=permit).state == "ready"


def test_ready_generation_does_not_release_a_live_interactive_claim(tmp_path):
    """Publication and demand completion are distinct ownership checkpoints."""
    inputs, owner, source = fact_fixture(tmp_path)
    due_demand(inputs)
    demand = claim_rebuild(
        inputs.campaign_id, inputs.population_scope, owner, admit=permit
    )
    record, _ = staged_facts(inputs, owner, source)
    publish_fact_set(record.pk, owner, admit=permit)
    rival = running_source_task()
    rival = TaskClaim(rival["task_id"], rival["task_fence"], rival["worker_id"])
    with pytest.raises(FactUnavailable, match="live fact builder"):
        recover_rebuild(demand.pk, rival, revision=1, admit=permit)
    demand.refresh_from_db()
    assert demand.claimed_task_id == owner.run_id
    assert recover_rebuild(demand.pk, owner, revision=1, admit=permit).pk == demand.pk
    replacement = retire(owner)
    recovered = recover_rebuild(demand.pk, replacement, revision=1, admit=permit)
    assert recovered.claimed_task_id == replacement.run_id
    assert complete_rebuild(demand.pk, replacement, revision=1, admit=permit)
