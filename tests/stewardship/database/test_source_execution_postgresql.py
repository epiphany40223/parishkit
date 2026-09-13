"""Exercise the compiled handler through real hints, SQL and private HTTP fences."""

from uuid import UUID, uuid4

import pytest
from django.db import connection, connections

from parishkit import parishsoft_transport
from parishkit.parishsoft_transport import SourceTransportDrainFailure
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.source import execution as worker
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.execution import refresh_handler
from parishkit.stewardship.source.fallback import request_full_fallback
from parishkit.stewardship.source.families import reconcile_source_families
from parishkit.stewardship.source.leases import (
    acquire_source,
    release_source,
    reserve_source_request,
)
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.refresh_models import SourceRefreshFallback
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import add_draft
from .credential_builders import keys
from .source_builders import running_source_task
from .test_source_attempts_postgresql import configured, setup, stage
from .test_source_leases_postgresql import delay
from .test_source_refreshing_postgresql import fake_provider, pages, seed_full
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle singleton seeds after each disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def handler(tmp_path, credential, *, reconcile):
    """Bind a real private synthetic file and explicit test reconciliation owner."""
    path = tmp_path / "source-key"
    path.write_bytes(credential.value)
    path.chmod(0o600)
    return refresh_handler(credential_path=path, reconcile=reconcile)


def run(receipt, compiled):
    """The real hint dispatcher owns the Task and independent renewal lifetime."""
    return execute_hint(
        receipt.task_root_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: compiled},
    )


def test_full_execution_promotes_family_effects_and_acknowledges(tmp_path, monkeypatch):
    """Connect real provider observation, campaign codes and Task success."""
    credential, store, version, actor = configured(tmp_path)
    _, campaign, _ = add_draft(store, version, actor)
    ring = keys()

    def effects(snapshot, execution, claim):
        """Use real Family reconciliation, not a callback-only success assertion."""
        assert connection.in_atomic_block
        reconcile_source_families(
            snapshot.pk,
            claim,
            campaign_id=UUID(campaign["id"]),
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            suppressed_addresses=frozenset(),
            admit=permit,
        )
        return True

    compiled = handler(tmp_path, credential, reconcile=effects)
    receipt = command()
    remaining, calls = fake_provider(monkeypatch, pages())
    assert run(receipt, compiled)
    assert not remaining and calls
    snapshot = SourceSnapshot.objects.get()
    assert snapshot.state == "promoted"
    assert SourceCurrent.objects.get().snapshot_id == snapshot.pk
    assert FamilyCampaign.objects.get().source_generation == snapshot.generation
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "succeeded"
    lease = SourceMutationLease.objects.get()
    assert lease.owner_id is None and lease.external_deadline is not None
    assert not run(receipt, compiled)


def test_invalid_corpus_fails_without_promotion(tmp_path, monkeypatch):
    """A closed invalid-data error uses the atomic rejection/diagnostic owner."""
    credential, *_ = configured(tmp_path)
    compiled = handler(tmp_path, credential, reconcile=permit)
    receipt = command()
    fake_provider(monkeypatch, pages(member_change={"birthdate": "PRIVATE-BAD"}))
    assert run(receipt, compiled)
    assert SourceSnapshot.objects.get().state == "rejected"
    assert SourceCurrent.objects.get().snapshot_id is None
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "failed"
    assert (
        OperationalLog.objects.get(event="source_refresh_invalid").level == "CRITICAL"
    )


def test_no_base_delta_waits_without_reading_credential_or_provider(
    tmp_path, monkeypatch
):
    """An unseeded delta depends on full work, never a fabricated empty baseline."""
    configured(tmp_path)
    compiled = refresh_handler(credential_path=tmp_path / "absent", reconcile=permit)
    receipt = command(cause="delta", actor_id=None)
    _, calls = fake_provider(monkeypatch, [])
    assert run(receipt, compiled)
    assert not calls and not SourceSnapshot.objects.exists()
    assert SourceRefreshFallback.objects.get().reason == "no_base"
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "retry_wait"
    assert SourceMutationLease.objects.get().fence == 0


def test_incompatible_delta_rejects_and_parks_full_dependency(tmp_path, monkeypatch):
    """Full-fallback creation and parent waiting commit with rejected staging."""
    credential, execution, claim, store, version, actor = setup(tmp_path)
    base = seed_full(credential, execution, claim)
    add_draft(store, version, actor)
    receipt = command(cause="delta", actor_id=None)
    compiled = handler(tmp_path, credential, reconcile=permit)
    _, calls = fake_provider(monkeypatch, [])
    assert run(receipt, compiled)
    assert not calls and SourceCurrent.objects.get().snapshot_id == base.pk
    assert SourceSnapshot.objects.exclude(pk=base.pk).get().state == "rejected"
    dependency = SourceRefreshFallback.objects.get()
    assert dependency.reason == "incomplete_delta" and dependency.attempt_id
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "retry_wait"
    assert SourceMutationLease.objects.get().owner_id is None


def test_delta_execution_can_promote_complete_unchanged_base(tmp_path, monkeypatch):
    """No change indications retain the whole corpus and full-observation provenance."""
    credential, execution, claim, *_ = setup(tmp_path)
    base = seed_full(credential, execution, claim)
    receipt = command(cause="delta", actor_id=None)
    compiled = handler(tmp_path, credential, reconcile=permit)
    fake_provider(monkeypatch, [[{"organizationID": 12345}], []])
    assert run(receipt, compiled)
    result = SourceSnapshot.objects.exclude(pk=base.pk).get()
    assert result.state == "promoted" and result.counts == base.counts
    assert result.cursor["full_snapshot_id"] == str(base.pk)
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "succeeded"


def test_scope_change_during_http_rejects_without_second_read(tmp_path, monkeypatch):
    """Late campaign changes do not become credentials for more observation."""
    credential, store, version, actor = configured(tmp_path)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=permit)
    calls = []

    def exchange(payload, **options):
        """Change scope while the real finite transport holds no SQL connection."""
        assert connection.connection is None
        calls.append(1)
        add_draft(store, version, actor)
        connections.close_all()
        return b'200\n[{"organizationID":12345}]'

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    assert run(receipt, compiled)
    assert len(calls) == 1 and SourceCurrent.objects.get().snapshot_id is None
    assert SourceSnapshot.objects.get().state == "rejected"
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "retry_wait"


def test_late_effect_failure_rolls_back_pointer_without_false_success(
    tmp_path, monkeypatch
):
    """Unknown reconciliation failure leaves ready staging for fenced recovery."""
    credential, *_ = configured(tmp_path)
    receipt = command()

    def fail(*args):
        """Emulate a later required owner failing in the promotion transaction."""
        assert SourceCurrent.objects.get().snapshot_id is not None
        raise RuntimeError("Synthetic reconciliation failure")

    compiled = handler(tmp_path, credential, reconcile=fail)
    fake_provider(monkeypatch, pages())
    with pytest.raises(RuntimeError, match="reconciliation failure"):
        run(receipt, compiled)
    assert SourceSnapshot.objects.get().state == "ready"
    assert SourceCurrent.objects.get().snapshot_id is None
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "running"


def test_unknown_helper_drain_propagates_and_retains_ownership(tmp_path, monkeypatch):
    """The real consumer must stop, not manufacture safe release/retry."""
    credential, *_ = configured(tmp_path)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=permit)

    def fail(*args, **kwargs):
        """Inject the transport's fatal uncertain-process-drain signal."""
        raise SourceTransportDrainFailure("Synthetic drain failure")

    monkeypatch.setattr(parishsoft_transport, "_exchange", fail)
    with pytest.raises(SourceTransportDrainFailure):
        run(receipt, compiled)
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "running"
    assert SourceMutationLease.objects.get().owner_id == receipt.task_root_id


def test_completion_failure_keeps_promoted_proof_for_recovery(tmp_path, monkeypatch):
    """A crash between promotion and acknowledgement never erases completed truth."""
    credential, *_ = configured(tmp_path)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=permit)

    def fail(*args, **kwargs):
        """Crash after tentative release, before Task acknowledgement commits."""
        assert SourceMutationLease.objects.get().owner_id is None
        raise RuntimeError("Synthetic completion outage")

    monkeypatch.setattr(worker, "_transition", fail)
    fake_provider(monkeypatch, pages())
    with pytest.raises(RuntimeError, match="completion outage"):
        run(receipt, compiled)
    assert SourceSnapshot.objects.get().state == "promoted"
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "running"
    assert SourceMutationLease.objects.get().owner_id == receipt.task_root_id


def test_reconciliation_must_return_exact_success(tmp_path, monkeypatch):
    """Simply returning from incomplete owning effects does not publish source truth."""
    credential, *_ = configured(tmp_path)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=lambda *args: None)
    fake_provider(monkeypatch, pages())
    with pytest.raises(StorageInvariantError, match="reconciliation did not complete"):
        run(receipt, compiled)
    assert SourceCurrent.objects.get().snapshot_id is None
    assert SourceSnapshot.objects.get().state == "ready"


def test_missing_credential_fails_before_source_acquisition(tmp_path, monkeypatch):
    """A private-file intake failure has no fabricated provider attempt."""
    configured(tmp_path)
    receipt = command()
    compiled = refresh_handler(credential_path=tmp_path / "absent", reconcile=permit)
    _, calls = fake_provider(monkeypatch, [])
    assert run(receipt, compiled)
    assert not calls and not SourceSnapshot.objects.exists()
    assert SourceMutationLease.objects.get().fence == 0
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "failed"


def test_competing_owner_causes_safe_wait_without_another_observation(
    tmp_path, monkeypatch
):
    """A claim/acquire race neither steals source ownership nor calls the provider."""
    credential, *_ = configured(tmp_path)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=permit)
    execution = claim_hint(
        receipt.task_root_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: compiled},
    )
    # The real race is after Task claim but before acquisition, not a claim
    # already known to be blocked by an existing source reservation.
    lease = acquire_source(**running_source_task(), phase="publication")
    _, calls = fake_provider(monkeypatch, [])
    with maintain_execution(execution):
        compiled.execute(execution)
    assert not calls and not SourceSnapshot.objects.exists()
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "retry_wait"
    assert SourceMutationLease.objects.get().owner_id == lease.task_id


@pytest.mark.parametrize("succeed", [False, True])
def test_full_dependency_result_is_acknowledged_without_parent_observation(
    tmp_path, monkeypatch, succeed
):
    """A live parent's linked result can settle even after configuration changes."""
    credential, store, version, actor = configured(tmp_path)
    parent = command(cause="delta", actor_id=None)
    compiled = refresh_handler(credential_path=tmp_path / "absent", reconcile=permit)
    execution = claim_hint(
        parent.task_root_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: compiled},
    )
    dependency = request_full_fallback(execution)
    target = dependency.command.request
    full = handler(tmp_path, credential, reconcile=permit) if succeed else compiled
    remaining, calls = fake_provider(monkeypatch, pages() if succeed else [])
    assert execute_hint(
        target.task_root_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: full},
    )
    assert not remaining
    before = len(calls)
    add_draft(store, version, actor)
    with maintain_execution(execution):
        compiled.execute(execution)
        assert execution.control.finished.is_set()
    assert len(calls) == before
    assert TaskRun.objects.get(pk=parent.task_root_id).state == (
        "succeeded" if succeed else "failed"
    )


@pytest.mark.parametrize("external", [False, True])
def test_known_source_contention_holds_hints_without_consuming_claims(
    tmp_path, external
):
    """Known lease/drain contention is held before counting another worker attempt."""
    credential, *_ = configured(tmp_path)
    lease = acquire_source(**running_source_task(), phase="publication")
    if external:
        reserve_source_request(lease, timeout_seconds=1, safety_seconds=1)
        release_source(lease)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=permit)
    registry = {TASK_TYPE: compiled}
    assert collect_hints(handlers=registry)[0] == ()
    with pytest.raises(PermissionError, match="not admitted"):
        run(receipt, compiled)
    task = TaskRun.objects.get(pk=receipt.task_root_id)
    assert task.state == "queued" and task.attempt == 0
    if external:
        delay(2.05)
    else:
        release_source(lease)
    assert collect_hints(handlers=registry)[0][0].run_id == receipt.task_root_id


@pytest.mark.parametrize("ready", [False, True])
def test_successful_new_owner_retires_but_never_reuses_old_staging(
    tmp_path, monkeypatch, ready
):
    """A later source fence rejects old observations without rewriting their history."""
    credential, execution, lease, *_ = setup(tmp_path)
    old = begin_refresh_attempt(execution, lease, credential)
    if ready:
        stage(old, execution, lease)
    # This synthetic older read never invoked HTTP. Releasing its live source
    # ownership therefore leaves no external deadline to wait out.
    release_source(lease)
    receipt = command()
    compiled = handler(tmp_path, credential, reconcile=permit)
    fake_provider(monkeypatch, pages())
    assert run(receipt, compiled)
    prior = SourceSnapshot.objects.get(pk=old.snapshot_id)
    assert prior.state == "rejected" and prior.task_id == execution.claim.run_id
    assert prior.source_fence == lease.fence and prior.generation is None
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"
    current = SourceCurrent.objects.get().snapshot
    assert current.pk != prior.pk and current.source_fence > prior.source_fence
    assert TaskRun.objects.get(pk=receipt.task_root_id).state == "succeeded"
