"""Compaction removes only whole superseded, unpinned, unused fact generations."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.reports import retention
from parishkit.stewardship.reports.facts import (
    FactUnavailable,
    publish_fact_set,
    read_fact_set,
)
from parishkit.stewardship.reports.models import (
    CampaignDailyFactSet,
    FactCompactionRecord,
)
from parishkit.stewardship.reports.retention import (
    compact_facts,
    pin_facts,
    release_fact_pin,
)
from parishkit.stewardship.source.pins import release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

from .fact_builders import fact_fixture, staged_facts
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def superseded(tmp_path):
    """Two complete generations, with the current interactive pointer on the second."""
    inputs, owner, source = fact_fixture(tmp_path)
    old, _ = staged_facts(inputs, owner, source)
    publish_fact_set(old.pk, owner, admit=permit, interactive=True)
    new, _ = staged_facts(replace(inputs, submission_watermark=2), owner, source)
    publish_fact_set(new.pk, owner, admit=permit, interactive=True)
    return inputs, owner, old, new


def test_whole_compaction_is_idempotent_and_keeps_source_manifest_and_evidence(
    tmp_path,
):
    """Deleting derived rows releases only their pin and retains operational keys."""
    inputs, owner, old, new = superseded(tmp_path)
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
    assert CampaignDailyFactSet.objects.get().pk == new.pk
    assert not SourceSnapshotPin.objects.filter(parent_id=old.pk).exists()
    assert SourceSnapshotPin.objects.filter(parent_id=new.pk).exists()
    evidence = FactCompactionRecord.objects.get()
    assert evidence.fact_set_id == old.pk and evidence.row_count == 2
    assert AuditEvent.objects.filter(
        event_type="facts_compacted", subject_id=evidence.pk
    ).exists()
    with pytest.raises(FactUnavailable):
        pin_facts(old.pk, parent_kind="export", parent_id=uuid4(), admit=permit)


@pytest.mark.parametrize(
    "kind", ["export", "digest", "render", "verification", "work", "operator"]
)
def test_retained_parent_keeps_generation_and_source_until_explicit_release(
    tmp_path, kind
):
    """Expiry of a generated file is not authority to release retained metadata."""
    inputs, owner, old, _ = superseded(tmp_path)
    pin = pin_facts(old.pk, parent_kind=kind, parent_id=uuid4(), admit=permit)
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
    assert release_fact_pin(pin.pk, admit=permit)
    assert not release_fact_pin(pin.pk, admit=permit)
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_source_input_pin_cannot_be_released_while_its_facts_remain(tmp_path):
    """The independent source compactor cannot reclaim a retained report's input."""
    _, _, old, _ = superseded(tmp_path)
    pin = SourceSnapshotPin.objects.get(parent_kind="facts", parent_id=old.pk)
    with pytest.raises(IntegrityError, match="still require"):
        release_snapshot_pin(pin.pk, admit=permit)


def test_a_partial_raw_delete_cannot_commit(tmp_path):
    """Even an otherwise disposable graph must remain complete until fully removed."""
    _, _, old, _ = superseded(tmp_path)
    with (
        pytest.raises(IntegrityError, match="whole disposable"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DELETE FROM stewardship_daily_fact WHERE id=%s", (old.days.first().pk,)
        )
    assert old.days.count() == 2


def test_failure_after_deletion_rolls_back_rows_pins_and_evidence(
    tmp_path, monkeypatch
):
    """Fault injection after actual deletion leaves an exact retryable generation."""
    inputs, owner, old, _ = superseded(tmp_path)

    def fail(*args, **kwargs):
        """Interrupt after SQL deletion and input release but before commit."""
        raise RuntimeError("synthetic interruption")

    with monkeypatch.context() as context:
        context.setattr(retention, "record_action", fail)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            compact_facts(inputs.campaign_id, owner, admit=permit)
    assert old.days.count() == 2
    assert SourceSnapshotPin.objects.filter(parent_id=old.pk).exists()
    assert not FactCompactionRecord.objects.exists()
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_active_lazy_reader_blocks_cleanup_until_serialization_finishes(tmp_path):
    """Cross-connection FOR SHARE protection lasts through the actual last query."""
    inputs, owner, old, _ = superseded(tmp_path)
    entered, finish = Event(), Event()

    def reading():
        """Own an independent connection and deliberately defer materialization."""
        try:
            with read_fact_set(old.pk, admit=permit) as record:
                entered.set()
                assert finish.wait(10)
                return record.days.count()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reading)
        try:
            assert entered.wait(10)
            assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
        finally:
            finish.set()
        assert future.result() == 2
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]
