"""Fault injection and true cross-connection late source-pin/compaction races."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from django.db import connections, transaction

from parishkit.stewardship.source import compaction
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.pins import pin_snapshot, release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import (
    SourceCompactionBatch,
    SourceSnapshot,
)
from parishkit.stewardship.source.snapshots import reconstruct_snapshot
from parishkit.stewardship.source.version_models import SourceFamily

from .test_source_compaction_postgresql import cleanup, permit
from .test_source_compaction_postgresql import history as source_history_fixture

history = source_history_fixture

pytestmark = pytest.mark.django_db(transaction=True)


def test_cleanup_failure_rolls_back_metadata_memberships_and_payloads(
    history, monkeypatch
):
    """A failed batch is retryable without a partially compacted visible corpus."""
    original = compaction._delete_corpora

    def fail(*args):
        """Inject failure after actual SQL deletion, before the transaction commits."""
        original(*args)
        raise RuntimeError("synthetic interruption")

    with monkeypatch.context() as context:
        context.setattr(compaction, "_delete_corpora", fail)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            cleanup()
    assert SourceSnapshot.objects.get(pk=history[0].pk).compacted_at is None
    assert SourceFamily.objects.count() == 3
    assert not SourceCompactionBatch.objects.exists()
    assert reconstruct_snapshot(history[0].pk)
    assert cleanup().snapshot_count == 1


def test_new_pin_winning_the_row_lock_prevents_cleanup(history):
    """An uncommitted pin wins even when cleanup's first query cannot see it."""
    entered, finish = Event(), Event()

    def pinning():
        """Hold the parent's transaction open while compaction attempts selection."""
        try:
            with transaction.atomic():
                pin = pin_snapshot(
                    history[0].pk, parent_kind="report", parent_id=uuid4(), admit=permit
                )
                entered.set()
                assert finish.wait(10)
                return pin.pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pinning)
        try:
            assert entered.wait(10)
            assert cleanup().snapshot_count == 0
        finally:
            finish.set()
        pin_id = future.result()
    assert cleanup().snapshot_count == 0
    release_snapshot_pin(pin_id, admit=permit)
    assert cleanup().snapshot_count == 1


def test_compaction_winning_the_row_lock_rejects_later_pin(history, monkeypatch):
    """A selector cannot protect deleted input after waiting for a committed batch."""
    deleted, finish, pin_started = Event(), Event(), Event()
    original = compaction._delete_corpora

    def pause(*args):
        """Expose the exact interval after deletion but before transaction commit."""
        result = original(*args)
        deleted.set()
        assert finish.wait(10)
        return result

    def cleaning():
        """Keep each background thread's database connection independent."""
        try:
            return cleanup()
        finally:
            connections.close_all()

    def pinning():
        """Try to lock the exact compacting snapshot, never another generation."""
        try:
            pin_started.set()
            return pin_snapshot(
                history[0].pk, parent_kind="report", parent_id=uuid4(), admit=permit
            )
        finally:
            connections.close_all()

    monkeypatch.setattr(compaction, "_delete_corpora", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleaning_future = pool.submit(cleaning)
        try:
            assert deleted.wait(10)
            pin_future = pool.submit(pinning)
            assert pin_started.wait(10)
        finally:
            finish.set()
        assert cleaning_future.result().snapshot_count == 1
        with pytest.raises(InvalidSourcePayload, match="reconstructable"):
            pin_future.result()
