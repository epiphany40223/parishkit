"""Source retention, explicit parent lifetime and reader/reference cleanup races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import connections, transaction

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.source import snapshots
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.compaction import compact_source
from parishkit.stewardship.source.leases import _now, acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.pins import pin_snapshot, release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import (
    SourceCompactionBatch,
    SourceCurrent,
    SourceSnapshot,
)
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    read_snapshot,
    reconstruct_snapshot,
    stage_entities,
)
from parishkit.stewardship.source.version_models import ENTITY_MODELS, SourceFamily

from .source_builders import running_source_task, source_corpus

pytestmark = pytest.mark.django_db(transaction=True)


def permit(*args):
    """Only synthetic internal admission is used by this disposable-data suite."""
    return True


@pytest.fixture
def history(monkeypatch):
    """Generate valid old promotion metadata while lease clocks remain real."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    with transaction.atomic():
        now = _now()
    claim = acquire_source(**running_source_task(), phase="full")
    records = []
    for index, instant in enumerate(
        (
            (now - timedelta(days=100)).replace(hour=10, minute=0, second=0),
            (now - timedelta(days=100)).replace(hour=11, minute=0, second=0),
            now,
        )
    ):
        with monkeypatch.context() as context:
            context.setattr(snapshots, "_now", lambda instant=instant: instant)
            corpus = source_corpus(name=f"Synthetic {index}")
            snapshot = begin_snapshot(claim, organization_id=100, admit=permit)
            for kind, entities in corpus.items():
                stage_entities(
                    snapshot.pk, claim, kind=kind, entities=entities, admit=permit
                )
            finish_snapshot(
                snapshot.pk,
                claim,
                expected_counts={kind: len(rows) for kind, rows in corpus.items()},
                cursor={},
                admit=permit,
            )
            records.append(
                promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=permit)
            )
    release_source(claim)
    return records


def cleanup(**kwargs):
    """A new real source claim guards each synthetic cleanup batch."""
    claim = acquire_source(**running_source_task(), phase="compaction")
    try:
        return compact_source(claim, admit=permit, **kwargs)
    finally:
        release_source(claim)


def test_compaction_preserves_manifests_anchors_current_and_shared_payloads(history):
    """Only a redundant old corpus and its newly unreferenced content disappear."""
    result = cleanup()
    assert (result.snapshot_count, result.membership_count, result.payload_count) == (
        1,
        9,
        1,
    )
    assert SourceSnapshot.objects.count() == 3
    assert SourceFamily.objects.count() == 2
    for kind, (payload, _) in ENTITY_MODELS.items():
        assert payload.objects.count() == (2 if kind == "family" else 1)
    assert reconstruct_snapshot(history[1].pk)["family"]["1"]["name"] == "Synthetic 1"
    assert reconstruct_snapshot()["family"]["1"]["name"] == "Synthetic 2"
    with pytest.raises(InvalidSourcePayload, match="unavailable"):
        reconstruct_snapshot(history[0].pk)
    again = cleanup()
    assert (again.snapshot_count, again.membership_count, again.payload_count) == (
        0,
        0,
        0,
    )
    assert SourceCompactionBatch.objects.count() == 2
    context = AuditContext.objects.get(event__subject_id=result.pk).context
    assert context == {"count": 9, "version": 3, "outcome": "succeeded"}
    assert result.recent_cutoff == result.cutoff_at - timedelta(days=90)


@pytest.mark.parametrize(
    "kind",
    [
        "submission",
        "report",
        "digest",
        "publication",
        "audit",
        "boundary",
        "restore",
        "delivery_hold",
        "operator",
        "facts",
    ],
)
def test_retained_parent_pins_override_compaction_until_explicit_release(history, kind):
    """Artifact expiry alone never removes a retained parent's replay inputs."""
    pin = pin_snapshot(history[0].pk, parent_kind=kind, parent_id=uuid4(), admit=permit)
    assert cleanup().snapshot_count == 0
    assert reconstruct_snapshot(history[0].pk)
    assert release_snapshot_pin(pin.pk, admit=permit)
    assert not release_snapshot_pin(pin.pk, admit=permit)
    assert cleanup().snapshot_count == 1


def test_expiring_form_baseline_pin_must_have_bounded_lifetime(history):
    """A form input reference expires explicitly; other parent pins may not expire."""
    with pytest.raises(ValueError, match="expiry"):
        pin_snapshot(
            history[0].pk, parent_kind="form_baseline", parent_id=uuid4(), admit=permit
        )
    with transaction.atomic():
        expiry = _now() + timedelta(minutes=5)
    parent = uuid4()
    pin = pin_snapshot(
        history[0].pk,
        parent_kind="form_baseline",
        parent_id=parent,
        admit=permit,
        expires_at=expiry,
    )
    assert (
        pin_snapshot(
            history[0].pk,
            parent_kind="form_baseline",
            parent_id=parent,
            admit=permit,
            expires_at=expiry,
        ).pk
        == pin.pk
    )
    assert cleanup().snapshot_count == 0
    with pytest.raises(ValueError, match="explicit parent release"):
        pin_snapshot(
            history[0].pk,
            parent_kind="report",
            parent_id=uuid4(),
            admit=permit,
            expires_at=expiry,
        )


def test_pin_cannot_resurrect_compacted_input_or_bypass_admission(history):
    """Losing selection requires an exact rebuild or rebaseline, not another cutoff."""
    with pytest.raises(PermissionError):
        pin_snapshot(
            history[0].pk,
            parent_kind="report",
            parent_id=uuid4(),
            admit=lambda *args: False,
        )
    cleanup()
    with pytest.raises(InvalidSourcePayload, match="reconstructable"):
        pin_snapshot(
            history[0].pk, parent_kind="report", parent_id=uuid4(), admit=permit
        )


def test_active_reader_wins_over_compaction_and_later_cleanup_resumes(history):
    """No expired heartbeat is used to infer that a lazy source reader is done."""
    entered, finish = Event(), Event()

    def reader():
        """Hold a real shared row lock while the other connection attempts cleanup."""
        try:
            with read_snapshot(history[0].pk):
                entered.set()
                assert finish.wait(10)
                return reconstruct_snapshot(history[0].pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reader)
        try:
            assert entered.wait(10)
            assert cleanup().snapshot_count == 0
        finally:
            finish.set()
        assert future.result()["family"]["1"]["name"] == "Synthetic 0"
    assert cleanup().snapshot_count == 1
