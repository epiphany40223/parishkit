"""A real verification consumer protects superseded facts until comparison ends."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest
from django.db import connections

from parishkit.stewardship.reports import materialization
from parishkit.stewardship.reports.facts import begin_fact_set, fact_inputs
from parishkit.stewardship.reports.materialization import (
    materialize_fact_set,
    verify_fact_set,
)
from parishkit.stewardship.reports.retention import compact_facts

from .test_fact_materialization_postgresql import allocation
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def test_compaction_skips_a_generation_being_recalculated(
    response_service, monkeypatch
):
    """Hold actual FOR SHARE locks, not a mocked pin or an expiring heartbeat."""
    first, claim = allocation(response_service, population="current")
    materialize_fact_set(first.pk, claim, admit=permit, interactive=True)
    inputs = fact_inputs(first)
    second = begin_fact_set(
        replace(inputs, through_date=inputs.through_date + timedelta(days=1)),
        claim,
        admit=permit,
    )
    materialize_fact_set(second.pk, claim, admit=permit, interactive=True)
    entered, release = Event(), Event()
    original = materialization.calculate_participation

    def pause(context):
        """Pause the actual verifier after detached input loading under its guard."""
        entered.set()
        assert release.wait(10), "verification test did not release its consumer"
        return original(context)

    def verify():
        """Close the consumer's thread-local SQL session even on failure."""
        try:
            return verify_fact_set(first.pk, admit=permit)
        finally:
            connections.close_all()

    monkeypatch.setattr(materialization, "calculate_participation", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(verify)
        try:
            assert entered.wait(10)
            assert compact_facts(inputs.campaign_id, claim, admit=permit) == []
        finally:
            release.set()
        assert pending.result(timeout=10) == ()
    assert compact_facts(inputs.campaign_id, claim, admit=permit) == [first.pk]
