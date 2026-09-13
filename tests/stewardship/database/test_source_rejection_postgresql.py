"""Expired staging can be rejected under a newer fence, never rebound/promoted."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.leases import (
    SourceFenceLost,
    SourceLeaseUnavailable,
    acquire_source,
    release_source,
    reserve_source_request,
)
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.rejection import reject_snapshot
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    promote_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .source_builders import running_source_task
from .test_source_snapshots_postgresql import permit, prepared

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Only idle migration seeds are recreated after disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


@pytest.mark.parametrize("ready", [False, True])
def test_live_owner_rejects_without_erasing_any_attempt_evidence(ready):
    """Failure preserves the task/fence, membership rows and any validated cursor."""
    if ready:
        snapshot, claim = prepared()
    else:
        claim = acquire_source(**running_source_task(), phase="full")
        snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
        stage_entities(
            snapshot.pk,
            claim,
            kind="family",
            entities={"1": {"name": "Synthetic"}},
            admit=permit,
        )
    before = SourceSnapshot.objects.values().get(pk=snapshot.pk)
    counts = {
        kind: membership.objects.count()
        for kind, (_, membership) in ENTITY_MODELS.items()
    }
    reject_snapshot(snapshot.pk, claim, admit=permit)
    reject_snapshot(snapshot.pk, claim, admit=permit)
    after = SourceSnapshot.objects.values().get(pk=snapshot.pk)
    mutable = {"state", "version", "updated_at", "actor_id", "correlation_id"}
    assert {key: value for key, value in before.items() if key not in mutable} == {
        key: value for key, value in after.items() if key not in mutable
    }
    assert after["state"] == "rejected" and after["version"] == before["version"] + 1
    assert counts == {
        kind: membership.objects.count()
        for kind, (_, membership) in ENTITY_MODELS.items()
    }
    assert SourceCurrent.objects.get().snapshot_id is None
    assert AuditContext.objects.filter(event__event_type="source_rejected").count() == 1
    with pytest.raises(InvalidSourcePayload):
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)


def test_newer_owner_rejects_old_staging_without_rebinding_it():
    """A new owner can retire an abandoned attempt even if its old Task still exists."""
    snapshot, old = prepared()
    release_source(old)
    claim = acquire_source(**running_source_task(), phase="full")
    result = reject_snapshot(snapshot.pk, claim, admit=permit)
    assert result.task_id == old.task_id and result.source_fence == old.fence
    assert result.actor_id == claim.worker_id
    with pytest.raises(SourceFenceLost):
        reject_snapshot(snapshot.pk, old, admit=permit)


def test_takeover_cannot_reject_during_an_external_request_safety_window():
    """The existing lease guard enforces drainage before a recovery fence exists."""
    snapshot, old = prepared()
    reserve_source_request(old, timeout_seconds=1, safety_seconds=1)
    release_source(old)
    next_task = running_source_task()
    with pytest.raises(SourceLeaseUnavailable):
        acquire_source(**next_task, phase="full")
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "ready"
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(2.05)")
    claim = acquire_source(**next_task, phase="full")
    assert reject_snapshot(snapshot.pk, claim, admit=permit).state == "rejected"


@pytest.mark.parametrize("phase", ["publication", "compaction"])
def test_nonrefresh_owner_cannot_reject_source_staging(phase):
    """Source-wide exclusion is not authority for unrelated domain mutations."""
    snapshot, old = prepared()
    release_source(old)
    claim = acquire_source(**running_source_task(), phase=phase)
    with pytest.raises(InvalidSourcePayload):
        reject_snapshot(snapshot.pk, claim, admit=permit)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceSnapshot.objects.filter(pk=snapshot.pk).update(
            version=F("version") + 1, state="rejected", actor_id=claim.worker_id
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"counts": {"family": 999}},
        {"cursor": {"advanced": True}},
        {"content_digest": "f" * 64},
        {"actor_id": uuid4()},
    ],
)
def test_sql_rejection_cannot_rewrite_forensic_evidence(changes):
    """Raw SQL receives the same fixed-evidence rule as the owning service."""
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
    with pytest.raises(IntegrityError), transaction.atomic():
        SourceSnapshot.objects.filter(pk=snapshot.pk).update(
            version=F("version") + 1, state="rejected", **changes
        )
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "staging"


def test_rejection_requires_admission_even_on_replay():
    """Knowledge of an already rejected snapshot UUID is not mutation authority."""
    snapshot, claim = prepared()
    for rejected in (False, True):
        if rejected:
            reject_snapshot(snapshot.pk, claim, admit=permit)
        with pytest.raises(PermissionError):
            reject_snapshot(snapshot.pk, claim, admit=lambda *args: False)


def test_audit_failure_rolls_back_rejection(monkeypatch):
    """No operational rejection is committed without its durable audit event."""
    snapshot, claim = prepared()

    def fail(*args, **kwargs):
        """Simulate unavailable audit persistence, not a provider failure."""
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr("parishkit.stewardship.source.rejection.record_action", fail)
    with pytest.raises(RuntimeError):
        reject_snapshot(snapshot.pk, claim, admit=permit)
    assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "ready"


def test_rejection_and_promotion_have_one_atomic_winner():
    """A competing callback cannot publish data after its rejection commits."""
    snapshot, claim = prepared()
    barrier = Barrier(2)

    def attempt(promote):
        """Each contender owns and closes its independent database connection."""
        try:
            barrier.wait(timeout=10)
            if promote:
                promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)
            else:
                reject_snapshot(snapshot.pk, claim, admit=permit)
            return True
        except InvalidSourcePayload:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, (False, True))) == 1
    snapshot.refresh_from_db()
    current = SourceCurrent.objects.get()
    assert (current.snapshot_id == snapshot.pk) == (snapshot.state == "promoted")


def test_rejection_guard_migration_round_trip_preserves_populated_evidence():
    """Old readers can retain rejected manifests without re-enabling stale writes."""
    snapshot, old = prepared()
    release_source(old)
    claim = acquire_source(**running_source_task(), phase="full")
    reject_snapshot(snapshot.pk, claim, admit=permit)
    targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        MigrationExecutor(connection).migrate(
            [("stewardship_source", "0009_refresh_request_guards")]
        )
        assert SourceSnapshot.objects.get(pk=snapshot.pk).state == "rejected"
    finally:
        MigrationExecutor(connection).migrate(targets)
    assert SourceSnapshot.objects.get(pk=snapshot.pk).source_fence == old.fence
