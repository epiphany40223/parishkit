"""Both orderings of a late durable report pin competing with fact compaction."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction

from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
from parishkit.stewardship.reports import retention
from parishkit.stewardship.reports.facts import FactBusy, FactUnavailable, read_fact_set
from parishkit.stewardship.reports.retention import (
    compact_facts,
    pin_facts,
    release_fact_pin,
)

from .lock_observer import backend_pid, wait_for_lock
from .test_fact_retention_postgresql import superseded
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def test_readonly_consumer_blocks_both_service_and_direct_sql_deletion(tmp_path):
    """A generation lock protects lazy queries without requiring UPDATE grants."""
    inputs, owner, old, _ = superseded(tmp_path)
    entered, finish = Event(), Event()

    def reading():
        """Keep the actual READ ONLY transaction alive through the final query."""
        try:
            with (
                CampaignReadGuard(
                    [inputs.campaign_id],
                    authorize=lambda guard: None,
                    abort=lambda: None,
                ),
                read_fact_set(old.pk, admit=permit) as record,
            ):
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
            for table, field in (
                ("stewardship_daily_fact", "fact_set_id"),
                ("stewardship_daily_fact_set", "id"),
            ):
                with (
                    pytest.raises(IntegrityError, match="protected"),
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(f"DELETE FROM {table} WHERE {field}=%s", (old.pk,))
        finally:
            finish.set()
        assert future.result(timeout=10) == 2
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_deletion_winning_lock_rejects_later_readonly_consumer(tmp_path, monkeypatch):
    """A busy reader fails immediately, even while compaction remains uncommitted."""
    inputs, owner, old, _ = superseded(tmp_path)
    deleted, finish = Event(), Event()
    original = retention.record_action

    def pause(*args, **kwargs):
        """Hold cleanup uncommitted after deletion, with both locks still owned."""
        result = original(*args, **kwargs)
        deleted.set()
        assert finish.wait(10)
        return result

    def cleaning():
        """Finish the real fenced cleanup on an independent SQL session."""
        try:
            return compact_facts(inputs.campaign_id, owner, admit=permit)
        finally:
            connections.close_all()

    def reading():
        """A real read-only guard must report contention, not a SQL timeout/500."""
        try:
            with (
                CampaignReadGuard(
                    [inputs.campaign_id],
                    authorize=lambda guard: None,
                    abort=lambda: None,
                ),
                read_fact_set(old.pk, admit=permit),
            ):
                pytest.fail("A removed generation must not become readable")
        finally:
            connections.close_all()

    monkeypatch.setattr(retention, "record_action", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleaning_future = pool.submit(cleaning)
        try:
            assert deleted.wait(10)
            reader = pool.submit(reading)
            with pytest.raises(FactBusy):
                reader.result(timeout=2)
        finally:
            finish.set()
        assert cleaning_future.result(timeout=10) == [old.pk]
    with (
        pytest.raises(FactUnavailable, match="not ready"),
        read_fact_set(old.pk, admit=permit),
    ):
        pytest.fail("Completed cleanup must remain unavailable")


def test_skipping_active_reader_releases_candidate_rows_before_batch_end(tmp_path):
    """A skipped candidate cannot block later pin/source work until batch commit."""
    from parishkit.stewardship.reports.models import CampaignDailyFactSet
    from parishkit.stewardship.source.snapshot_models import SourceSnapshot

    inputs, owner, old, _ = superseded(tmp_path)
    skipped, finish = Event(), Event()

    def cleaning():
        """Leave the outer batch open after it has skipped this protected reader."""
        try:
            with transaction.atomic():
                assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
                skipped.set()
                assert finish.wait(10)
        finally:
            connections.close_all()

    with read_fact_set(old.pk, admit=permit), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(cleaning)
        try:
            assert skipped.wait(10)
            # These would raise immediately if the skipped candidate retained
            # either FOR UPDATE lock in the still-open batch transaction.
            assert (
                SourceSnapshot.objects.select_for_update(nowait=True)
                .filter(pk=old.source_id)
                .exists()
            )
            assert (
                CampaignDailyFactSet.objects.select_for_update(nowait=True)
                .filter(pk=old.pk)
                .exists()
            )
        finally:
            finish.set()
        pending.result(timeout=10)


def test_uncommitted_pin_wins_over_cleanup_selection(tmp_path):
    """Cleanup skips a generation even before its newly locked pin is committed."""
    inputs, owner, old, _ = superseded(tmp_path)
    entered, finish = Event(), Event()
    parent_id = uuid4()

    def pinning():
        """Hold pin installation open on an independent connection."""
        try:
            with transaction.atomic():
                pin = pin_facts(
                    old.pk, parent_kind="export", parent_id=parent_id, admit=permit
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
            assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
        finally:
            finish.set()
        pin_id = future.result()
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
    release_fact_pin(pin_id, parent_kind="export", parent_id=parent_id, admit=permit)
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_deletion_winning_lock_makes_later_exact_pin_fail_closed(tmp_path, monkeypatch):
    """A late selector cannot replace deleted exact inputs with the current graph."""
    inputs, owner, old, _ = superseded(tmp_path)
    deleted, finish, pin_started = Event(), Event(), Event()
    pin_backend = []
    original = retention.record_action

    def pause(*args, **kwargs):
        """Expose the interval after deletion but before the batch commits."""
        result = original(*args, **kwargs)
        deleted.set()
        assert finish.wait(10)
        return result

    def cleaning():
        """Own a separate connection for the entire cleanup transaction."""
        try:
            return compact_facts(inputs.campaign_id, owner, admit=permit)
        finally:
            connections.close_all()

    def pinning():
        """Request the exact old generation while its compactor holds the row."""
        try:
            pin_backend.append(backend_pid())
            pin_started.set()
            return pin_facts(
                old.pk, parent_kind="digest", parent_id=uuid4(), admit=permit
            )
        finally:
            connections.close_all()

    monkeypatch.setattr(retention, "record_action", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cleaning_future = pool.submit(cleaning)
        try:
            assert deleted.wait(10)
            pin_future = pool.submit(pinning)
            assert pin_started.wait(10)
            wait_for_lock(pin_backend[0])
        finally:
            finish.set()
        assert cleaning_future.result() == [old.pk]
        with pytest.raises(FactUnavailable):
            pin_future.result()
