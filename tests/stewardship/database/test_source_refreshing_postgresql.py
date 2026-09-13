"""Real attempt/HTTP/staging pipeline, with only the provider exchange replaced."""

import json

import pytest
from django.db import connection, connections

from parishkit import parishsoft_transport
from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.credentials import SourceCredential
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.refresh_models import SourceRefreshAttempt
from parishkit.stewardship.source.refreshing import load_and_stage_attempt
from parishkit.stewardship.source.snapshots import (
    finish_snapshot,
    promote_snapshot,
    stage_entities,
)
from parishkit.stewardship.storage import StorageInvariantError

from ..test_source_loading import provider_pages
from .campaign_builders import add_draft
from .test_source_attempts_postgresql import setup
from .test_source_requests_postgresql import claim as claim_request
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only the disposable database's idle migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def pages(**options):
    """Scope the complete shared loader fixture to the configured synthetic tenant."""
    values = provider_pages(**options)
    values[0][0]["organizationID"] = 12345
    values[1][0]["registeredOrganizationID"] = 12345
    return values


def fake_provider(monkeypatch, values):
    """Every actual transport preflight still runs, including closed-SQL checks."""
    remaining, calls = list(values), []

    def exchange(payload, **kwargs):
        """Only this private pipe exchange is faked; no real API credential is used."""
        assert connection.connection is None and not connection.in_atomic_block
        calls.append(json.loads(payload)["url"])
        return b"200\n" + json.dumps(remaining.pop(0)).encode()

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    return remaining, calls


def run(credential, execution, lease):
    """Use real independent renewal for the whole observation and staging attempt."""
    with maintain_execution(execution), execution.maintain_source(lease):
        return load_and_stage_attempt(execution, lease, credential)


def test_full_pipeline_stages_one_bound_manifest_without_claiming_success(
    tmp_path, monkeypatch
):
    """A validated load is ready, not current and not a succeeded TaskRun."""
    credential, execution, lease, *_ = setup(tmp_path)
    remaining, calls = fake_provider(monkeypatch, pages())
    result = run(credential, execution, lease)
    attempt = SourceRefreshAttempt.objects.get()
    assert not remaining and len(calls) == len(pages())
    assert result.pk == attempt.snapshot_id and result.state == "ready"
    assert result.cursor["watermark"] == result.started_at.isoformat()
    assert result.cursor["full_snapshot_id"] == str(result.pk)
    assert result.counts["family"] == result.counts["member"] == 1
    assert result.counts["fund"] == 1
    assert SourceCurrent.objects.get().snapshot_id is None
    task = TaskRun.objects.get(pk=execution.claim.run_id)
    assert task.state == "running" and task.phase == "validating"
    assert task.progress_current == task.progress_total == sum(result.counts.values())


def test_bad_provider_data_does_not_stage_any_entities(tmp_path, monkeypatch):
    """Normalization must finish before any source collection is staged."""
    credential, execution, lease, *_ = setup(tmp_path)
    fake_provider(monkeypatch, pages(member_change={"birthdate": "INVALID-PRIVATE"}))
    with pytest.raises(InvalidSourcePayload):
        run(credential, execution, lease)
    snapshot = SourceSnapshot.objects.get()
    assert snapshot.state == "staging" and not snapshot.counts
    assert SourceCurrent.objects.get().snapshot_id is None
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"


def test_wrong_loaded_key_cannot_start_observation(tmp_path, monkeypatch):
    """A key mismatch stops before manifest creation or any helper subprocess."""
    _, execution, lease, *_ = setup(tmp_path)
    _, calls = fake_provider(monkeypatch, [])
    with pytest.raises(PermissionError):
        run(SourceCredential(b"WRONG-SYNTHETIC"), execution, lease)
    assert not calls and not SourceSnapshot.objects.exists()


def test_observation_requires_maintained_task_and_source(tmp_path):
    """Durable lease possession alone does not authorize a lengthy provider scan."""
    credential, execution, lease, *_ = setup(tmp_path)
    with pytest.raises(StorageInvariantError, match="maintained"):
        load_and_stage_attempt(execution, lease, credential)
    assert not SourceSnapshot.objects.exists()


def test_campaign_changed_between_reads_cannot_stage_old_request(tmp_path, monkeypatch):
    """A later page repeats current-scope admission rather than trusting startup."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    calls = []

    def exchange(payload, **kwargs):
        """Simulate an independent configuration commit during a provider wait."""
        assert connection.connection is None
        calls.append(1)
        add_draft(store, version, actor)
        connections.close_all()
        return b'200\n[{"organizationID":12345}]'

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    with pytest.raises(PermissionError):
        run(credential, execution, lease)
    assert len(calls) == 1
    snapshot = SourceSnapshot.objects.get()
    assert snapshot.state == "staging" and not snapshot.counts
    assert SourceCurrent.objects.get().snapshot_id is None


def seed_full(credential, execution, lease):
    """Seed complete real converter output without consuming an HTTP safety window."""
    from dataclasses import replace

    from parishkit.stewardship.source.corpus import normalize_core

    from ..test_source_corpus import source

    attempt = begin_refresh_attempt(execution, lease, credential)
    snapshot = attempt.snapshot
    data = replace(source(), organization_id=12345)
    for family in data.families.values():
        family["registeredOrganizationID"] = 12345
    corpus = normalize_core(data, as_of=snapshot.started_at.date())
    with execution.effect():
        for kind, rows in corpus.items():
            stage_entities(snapshot.pk, lease, kind=kind, entities=rows, admit=permit)
        cursor = refresh_cursor(
            snapshot_id=snapshot.pk,
            kind="full",
            started_at=snapshot.started_at,
            window_digest=attempt.request.window_digest,
            evidence={
                "giving_as_of_date": snapshot.started_at.date().isoformat(),
                "anonymous_pledges": 0,
                "anonymous_contributions": 0,
            },
        )
        snapshot = finish_snapshot(
            snapshot.pk,
            lease,
            expected_counts={k: len(v) for k, v in corpus.items()},
            cursor=cursor,
            admit=permit,
        )
        promote_snapshot(snapshot.pk, lease, admit=permit, reconcile=permit)
        release_source(lease)
    execution.transition("complete")
    return snapshot


def next_delta():
    """Acquire a new concrete delta claim after the seed full observation ended."""
    execution = claim_request(command(cause="delta", actor_id=None))
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="delta",
        )
    return execution, lease


def test_delta_uses_only_promoted_base_and_preserves_full_provenance(
    tmp_path, monkeypatch
):
    """No indications still yields a complete candidate, not partial truth."""
    credential, execution, lease, *_ = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    execution, lease = next_delta()
    remaining, calls = fake_provider(monkeypatch, [[{"organizationID": 12345}], []])
    result = run(credential, execution, lease)
    assert not remaining and len(calls) == 2
    assert result.state == "ready" and result.base_id == first.pk
    assert result.counts == first.counts
    assert result.cursor["full_snapshot_id"] == str(first.pk)
    assert SourceCurrent.objects.get().snapshot_id == first.pk


def test_delta_changed_window_requires_full_without_any_provider_read(
    tmp_path, monkeypatch
):
    """A new request cannot apply the previous campaign's delta coverage."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    first = seed_full(credential, execution, lease)
    add_draft(store, version, actor)
    execution, lease = next_delta()
    _, calls = fake_provider(monkeypatch, [])
    with pytest.raises(ChangeFeedIncomplete):
        run(credential, execution, lease)
    assert not calls and SourceCurrent.objects.get().snapshot_id == first.pk
    assert SourceSnapshot.objects.exclude(pk=first.pk).get().state == "staging"
