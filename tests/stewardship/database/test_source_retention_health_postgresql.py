"""Source retention that keeps being skipped raises an operational incident."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.operational_content import IncidentKind
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.source import compaction, retention_health
from parishkit.stewardship.source.snapshot_models import SourceCompactionBatch

from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_source_compaction_postgresql import _Execution, history  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def run_retention(*, login=None):
    """One refresh's retention, under its own run's correlation ID.

    With ``login``, retention runs as that service's database login; the
    refresh task itself is created first, as the scheduler would.
    """
    from contextlib import nullcontext

    execution = _Execution(running_source_task())
    with (
        task_login(login, exact=True, reconnect=True) if login else nullcontext(),
        correlation(uuid4()),
    ):
        compaction.compact_before_refresh(execution)


def skips():
    """The durable skip entries so far."""
    return OperationalLog.objects.filter(event="source_retention_skipped")


def episode():
    """The open retention episode, if any."""
    return OperationalIncident.objects.filter(
        kind=IncidentKind.SOURCE_RETENTION_FAILING, resolved_at__isnull=True
    ).first()


def observe():
    """Observe as the worker that runs the operational collection."""
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        retention_health.observe_retention_health()
        return OperationalIncident.objects.filter(
            kind=IncidentKind.SOURCE_RETENTION_FAILING, resolved_at__isnull=True
        ).first()


def broken(*args, **kwargs):
    """Simulate a retention fault that persists across refreshes."""
    raise IntegrityError("synthetic retention fault")


def test_three_skipped_runs_open_the_incident_and_a_clean_run_resolves_it(
    history,  # noqa: F811
    monkeypatch,
):
    """Each skipped run keeps one durable entry; three in a row is an incident."""
    monkeypatch.setattr(compaction, "compact_source", broken)
    for count in (1, 2):
        run_retention()
        assert skips().count() == count
        assert observe() is None
    # The third run itself opens the incident, as the worker login.
    run_retention(login=ServiceRole.WORKER)
    entry = skips().first()
    assert (entry.level, entry.context) == ("ERROR", {})
    opened = episode()
    assert opened is not None and opened.signal_level == "WARNING"
    monkeypatch.undo()
    run_retention(login=ServiceRole.WORKER)
    assert SourceCompactionBatch.objects.exists()
    assert episode() is None


def test_a_run_that_failed_fact_cleanup_still_counts_as_skipped(
    history,  # noqa: F811
    monkeypatch,
):
    """Snapshot batches that ran after a fact cleanup failure do not hide it.

    That was the shape of the validation deployment's silent retention stop:
    fact pins were never released, so every batch reclaimed nothing.
    """
    monkeypatch.setattr(compaction, "_compact_superseded_facts", broken)
    for _ in range(3):
        run_retention()
    assert SourceCompactionBatch.objects.exists()
    assert observe() is not None


def test_one_run_with_two_failures_keeps_one_entry(history, monkeypatch):  # noqa: F811
    """Fact cleanup and the batches both failing is still one skipped run."""
    monkeypatch.setattr(compaction, "_compact_superseded_facts", broken)
    monkeypatch.setattr(compaction, "compact_source", broken)
    run_retention()
    assert skips().count() == 1


def test_a_retry_that_gets_through_breaks_the_streak(history, monkeypatch):  # noqa: F811
    """A run whose later attempt succeeds is a success (#359 review L2).

    A retried task, or a fallback full refresh after a delta, keeps the run's
    correlation ID; its newer compaction evidence outweighs the earlier skip.
    """
    monkeypatch.setattr(compaction, "compact_source", broken)
    run_retention()
    run_retention()
    retried = uuid4()
    with correlation(retried):
        compaction.compact_before_refresh(_Execution(running_source_task()))
    monkeypatch.undo()
    with correlation(retried):
        compaction.compact_before_refresh(_Execution(running_source_task()))
    with transaction.atomic():
        assert not retention_health.retention_failing()
    assert observe() is None


def test_skips_older_than_the_lookback_are_ignored(history, monkeypatch):  # noqa: F811
    """Only the last 30 days are read (#359 review L3)."""
    from datetime import timedelta

    monkeypatch.setattr(compaction, "compact_source", broken)
    for _ in range(3):
        run_retention()
    with transaction.atomic():
        assert retention_health.retention_failing()
    monkeypatch.setattr(retention_health, "LOOKBACK", timedelta(0))
    with transaction.atomic():
        assert not retention_health.retention_failing()


def test_a_retried_run_counts_once(history, monkeypatch):  # noqa: F811
    """Attempts of one refresh task share its correlation ID: one run, not three."""
    monkeypatch.setattr(compaction, "compact_source", broken)
    run = uuid4()
    for _ in range(3):
        with correlation(run):
            compaction.compact_before_refresh(_Execution(running_source_task()))
    assert skips().count() == 3
    with transaction.atomic():
        assert not retention_health.retention_failing()
