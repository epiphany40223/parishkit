"""Both orderings of a late durable report pin competing with fact compaction."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from django.db import connections, transaction

from parishkit.stewardship.reports import retention
from parishkit.stewardship.reports.facts import FactUnavailable
from parishkit.stewardship.reports.retention import (
    compact_facts,
    pin_facts,
    release_fact_pin,
)

from .test_fact_retention_postgresql import superseded
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def test_uncommitted_pin_wins_over_cleanup_selection(tmp_path):
    """Cleanup skips a generation even before its newly locked pin is committed."""
    inputs, owner, old, _ = superseded(tmp_path)
    entered, finish = Event(), Event()

    def pinning():
        """Hold pin installation open on an independent connection."""
        try:
            with transaction.atomic():
                pin = pin_facts(
                    old.pk, parent_kind="export", parent_id=uuid4(), admit=permit
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
    release_fact_pin(pin_id, admit=permit)
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_deletion_winning_lock_makes_later_exact_pin_fail_closed(tmp_path, monkeypatch):
    """A late selector cannot replace deleted exact inputs with the current graph."""
    inputs, owner, old, _ = superseded(tmp_path)
    deleted, finish, pin_started = Event(), Event(), Event()
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
        finally:
            finish.set()
        assert cleaning_future.result() == [old.pk]
        with pytest.raises(FactUnavailable):
            pin_future.result()
