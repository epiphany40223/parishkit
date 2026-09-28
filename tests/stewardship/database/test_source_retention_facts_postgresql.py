"""Retention under the live pattern: unchanged refreshes, each with report facts.

On the validation deployment every 15-minute refresh promoted an identical
snapshot and rebuilt report facts, and each fact generation pinned its source
snapshot with no expiry. Nothing ever released those pins, so retention
reclaimed nothing and the database kept growing.
"""

from dataclasses import replace

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.reports.facts import publish_fact_set
from parishkit.stewardship.reports.models import CampaignDailyFactSet
from parishkit.stewardship.source import compaction
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import (
    SourceSnapshot,
    SourceSnapshotPin,
)

from .fact_builders import fact_fixture, staged_facts
from .source_builders import running_source_task
from .test_source_snapshots_postgresql import permit, prepared, publish

pytestmark = pytest.mark.django_db(transaction=True)


class _Execution:
    """The slice of a live refresh execution that retention uses."""

    def __init__(self):
        task = running_source_task()
        from parishkit.stewardship.jobs.ownership import TaskClaim

        self.claim = TaskClaim(task["task_id"], task["task_fence"], task["worker_id"])

    def effect(self):
        """A plain transaction stands in for the handler's fenced effect scope."""
        return transaction.atomic()

    def maintain_source(self, claim):
        """Renewal is irrelevant to a sub-second synthetic batch."""
        from contextlib import nullcontext

        return nullcontext()

    def check(self):
        """The synthetic lifetime is always active."""


def refresh_with_facts(inputs, owner, watermark):
    """Promote an identical corpus, then build and publish facts from it."""
    snapshot, claim = prepared()
    snapshot = publish(snapshot, claim)
    release_source(claim)
    facts, _ = staged_facts(
        replace(inputs, source_id=snapshot.pk, submission_watermark=watermark),
        owner,
        snapshot,
    )
    publish_fact_set(facts.pk, owner, admit=permit, interactive=True)
    return snapshot, facts


def test_unchanged_refreshes_with_facts_are_reclaimed(tmp_path, monkeypatch):
    """Superseded fact generations stop pinning unchanged refreshed snapshots."""
    inputs, owner, first = fact_fixture(tmp_path)
    # Only the fact pins are under test here; the campaign fixture's own
    # credential rows would otherwise protect its first snapshot.
    monkeypatch.setattr(
        compaction,
        "LIVE_REFERENCES",
        "SELECT id FROM (SELECT NULL::uuid AS id) AS live WHERE id IS NOT NULL",
    )
    history = [refresh_with_facts(inputs, owner, watermark) for watermark in (2, 3, 4)]
    snapshots = [first] + [snapshot for snapshot, _ in history]
    digests = {row.content_digest for row in snapshots}
    assert len(digests) == 1
    assert SourceSnapshotPin.objects.filter(parent_kind="facts").count() == 3

    compaction.compact_before_refresh(_Execution())

    current, latest = history[-1]
    assert list(CampaignDailyFactSet.objects.values_list("pk", flat=True)) == [
        latest.pk
    ]
    assert list(
        SourceSnapshotPin.objects.filter(parent_kind="facts").values_list(
            "snapshot_id", flat=True
        )
    ) == [current.pk]
    reclaimed = set(
        SourceSnapshot.objects.filter(compacted_at__isnull=False).values_list(
            "pk", flat=True
        )
    )
    assert reclaimed == {row.pk for row in snapshots[:-1]}
    assert SourceMutationLease.objects.get().phase == "idle"


def test_rejected_staging_memberships_are_reclaimed(tmp_path):
    """A failed load's staged rows go; its rejected manifest stays as evidence."""
    from parishkit.stewardship.source.rejection import reject_snapshot
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.version_models import ENTITY_MODELS

    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    snapshot, claim = prepared()
    publish(snapshot, claim)
    staged, _ = prepared(claim=claim)
    reject_snapshot(staged.pk, claim, admit=permit)
    release_source(claim)
    assert any(
        membership.objects.filter(snapshot_id=staged.pk).exists()
        for _, membership in ENTITY_MODELS.values()
    )
    compaction.compact_before_refresh(_Execution())
    assert not any(
        membership.objects.filter(snapshot_id=staged.pk).exists()
        for _, membership in ENTITY_MODELS.values()
    )
    assert SourceSnapshot.objects.get(pk=staged.pk).state == "rejected"


def test_worker_login_compacts_superseded_facts(tmp_path, monkeypatch):
    """The real worker role may delete disposable fact generations."""
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    inputs, owner, _ = fact_fixture(tmp_path)
    monkeypatch.setattr(
        compaction,
        "LIVE_REFERENCES",
        "SELECT id FROM (SELECT NULL::uuid AS id) AS live WHERE id IS NOT NULL",
    )
    history = [refresh_with_facts(inputs, owner, watermark) for watermark in (2, 3)]
    execution = _Execution()
    with task_login(ServiceRole.WORKER, exact=True):
        claim = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="compaction",
        )
        try:
            removed = compaction._compact_superseded_facts(execution)
        finally:
            release_source(claim)
    assert removed == [history[0][1].pk]
    assert list(CampaignDailyFactSet.objects.values_list("pk", flat=True)) == [
        history[1][1].pk
    ]


def test_fact_compaction_never_takes_the_global_work_lock(tmp_path, monkeypatch):
    """Retention must not enter the refresh handler's work-lock effect scope."""
    from parishkit.stewardship.reports import retention

    inputs, owner, _ = fact_fixture(tmp_path)
    monkeypatch.setattr(
        compaction,
        "LIVE_REFERENCES",
        "SELECT id FROM (SELECT NULL::uuid AS id) AS live WHERE id IS NOT NULL",
    )
    history = [refresh_with_facts(inputs, owner, watermark) for watermark in (2, 3)]
    original = retention.compact_facts
    probed = []

    def probe(*args, **kwargs):
        """Check this backend's advisory locks inside the deleting transaction."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory' AND classid=736220 AND objid=1 AND granted)"
            )
            probed.append(cursor.fetchone()[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(retention, "compact_facts", probe)

    class NoEffect(_Execution):
        """A refresh's effect scope takes the work lock; retention must avoid it."""

        def effect(self):
            """Fail loudly if retention enters the handler's effect scope."""
            raise AssertionError("retention entered the work-lock effect scope")

    execution = NoEffect()
    claim = acquire_source(
        task_id=execution.claim.run_id,
        task_fence=execution.claim.fence,
        worker_id=execution.claim.worker_id,
        phase="compaction",
    )
    try:
        removed = compaction._compact_superseded_facts(execution)
    finally:
        release_source(claim)
    assert removed == [history[0][1].pk]
    assert probed and not any(probed)


def test_fact_cleanup_failure_does_not_stop_snapshot_retention(tmp_path, monkeypatch):
    """An outdated pin guard failed fact cleanup and silently skipped everything.

    On the validation deployment an empty in-place SQL left the old pin guard
    installed, so every fact pin release raised, and because fact cleanup ran
    first inside the same handler, no snapshot batch ever ran. Fact cleanup
    failures are now logged on their own and snapshot retention continues.
    """
    from parishkit.stewardship.source.snapshot_models import SourceCompactionBatch

    inputs, owner, first = fact_fixture(tmp_path)
    monkeypatch.setattr(
        compaction,
        "LIVE_REFERENCES",
        "SELECT id FROM (SELECT NULL::uuid AS id) AS live WHERE id IS NOT NULL",
    )
    history = [refresh_with_facts(inputs, owner, watermark) for watermark in (2, 3)]
    skipped = []

    def refuse(execution, **kwargs):
        """Simulate the old pin guard refusing the worker's fact pin release."""
        raise IntegrityError(
            "Worker may release only unused response comparison inputs"
        )

    from parishkit.stewardship import observability

    monkeypatch.setattr(compaction, "_compact_superseded_facts", refuse)
    monkeypatch.setattr(
        observability,
        "emit_failure",
        lambda error, *, event: skipped.append((type(error), event)),
    )
    compaction.compact_before_refresh(_Execution())
    assert skipped == [(IntegrityError, observability.Event.SOURCE_RETENTION_SKIPPED)]
    # The fixture's first snapshot has no fact pin, so it is reclaimed; the
    # fact-pinned ones stay until fact cleanup succeeds.
    assert SourceCompactionBatch.objects.exists()
    assert SourceSnapshot.objects.get(pk=first.pk).compacted_at is not None
    assert all(
        SourceSnapshot.objects.get(pk=snapshot.pk).compacted_at is None
        for snapshot, _ in history
    )
    assert SourceMutationLease.objects.get().phase == "idle"


def test_worker_reclaims_superseded_and_rejected_corpora(tmp_path, monkeypatch):
    """The live pattern under the real worker login, end to end.

    On the validation deployment, the first batch that reached real
    memberships failed: rejected staging corpora are never marked compacted,
    and the membership guard admitted the worker only for compacted
    manifests, so every batch raised and nothing was reclaimed.
    """
    from parishkit.stewardship import observability
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.source.rejection import reject_snapshot
    from parishkit.stewardship.source.snapshot_models import SourceCompactionBatch
    from parishkit.stewardship.source.version_models import ENTITY_MODELS

    from .test_background_grants_postgresql import task_login

    def members(snapshot_id):
        """Count one corpus's membership rows across every entity table."""
        return sum(
            membership.objects.filter(snapshot_id=snapshot_id).count()
            for _, membership in ENTITY_MODELS.values()
        )

    inputs, owner, _ = fact_fixture(tmp_path)
    monkeypatch.setattr(
        compaction,
        "LIVE_REFERENCES",
        "SELECT id FROM (SELECT NULL::uuid AS id) AS live WHERE id IS NOT NULL",
    )
    history = [refresh_with_facts(inputs, owner, watermark) for watermark in (2, 3, 4)]
    staged, claim = prepared()
    reject_snapshot(staged.pk, claim, admit=permit)
    release_source(claim)
    superseded = history[0][0]
    assert members(superseded.pk) and members(staged.pk)
    skipped = []
    monkeypatch.setattr(
        observability,
        "emit_failure",
        lambda error, *, event: skipped.append(repr(error)),
    )
    execution = _Execution()
    with task_login(ServiceRole.WORKER, exact=True):
        compaction.compact_before_refresh(execution)
    assert skipped == []
    assert members(staged.pk) == 0
    assert SourceSnapshot.objects.get(pk=staged.pk).state == "rejected"
    assert members(superseded.pk) == 0
    assert SourceSnapshot.objects.get(pk=superseded.pk).compacted_at is not None
    current = history[-1][0]
    assert (
        members(current.pk)
        and SourceSnapshot.objects.get(pk=current.pk).compacted_at is None
    )
    assert SourceCompactionBatch.objects.filter(snapshot_count__gt=0).exists()
    assert SourceMutationLease.objects.get().phase == "idle"
