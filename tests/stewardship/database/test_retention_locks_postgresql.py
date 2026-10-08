"""Retention never waits long for a lock and always releases its lease (#387).

Each test uses a real competing PostgreSQL session or the real execution
control:

- fact cleanup waits at most a second for a busy Campaign row, without
  keeping this task's row locked, and never for an insert's key-share lock
  (M3);
- compaction marking does not wait for the source pointer's row lock (L4);
- a graceful stop still releases the compaction lease (L5);
- report fact cleanup gets only its share of the budget, and a reclaim chunk
  stopped by its statement timeout ends the drain and is logged (L6).
"""

from time import monotonic

import psycopg
import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.lifetime import ExecutionInterrupted
from parishkit.stewardship.reports import retention
from parishkit.stewardship.reports.retention import compact_facts
from parishkit.stewardship.source import compaction
from parishkit.stewardship.source.compaction import compact_source
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_fact_retention_postgresql import superseded
from .test_source_compaction_postgresql import _Execution, history  # noqa: F401
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def separate_session():
    """A new autocommit session as the test database owner."""
    settings = connection.settings_dict
    return psycopg.connect(
        host=settings["HOST"] or "127.0.0.1",
        port=settings["PORT"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        dbname=settings["NAME"],
        autocommit=True,
    )


def task_row_locked(task_id):
    """Whether any session holds this task's row locked, seen from another."""
    with separate_session() as session:
        row = session.execute(
            "SELECT 1 FROM stewardship_task_run WHERE id=%s FOR UPDATE SKIP LOCKED",
            [task_id],
        ).fetchone()
    return row is None


def test_fact_cleanup_stops_at_its_lock_limit_on_a_busy_campaign(tmp_path):
    """A Campaign row a transition holds stops cleanup after LOCK_SECONDS.

    Cleanup then holds nothing, the generation stays, the timeout log
    records the busy lock at INFO, and the next run, with the Campaign
    free, removes it.
    """
    inputs, owner, old, _ = superseded(tmp_path)
    with separate_session() as session, session.transaction():
        session.execute(
            "SELECT 1 FROM stewardship_campaign WHERE id=%s FOR UPDATE",
            [inputs.campaign_id],
        )
        started = monotonic()
        assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
        assert retention.LOCK_SECONDS * 0.9 <= monotonic() - started < 3
        assert not task_row_locked(owner.run_id)
    entry = OperationalLog.objects.get(event="work_budget_reached")
    assert entry.level == "INFO" and entry.context["what"] == "lock_timeout"
    assert entry.context["limit_seconds"] == retention.LOCK_SECONDS
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_fact_cleanup_waits_out_a_short_key_share_hold(tmp_path):
    """Inserts that reference the Campaign do not stop cleanup.

    Such an insert holds the Campaign FOR KEY SHARE until it commits. Cleanup
    locks the Campaign FOR NO KEY UPDATE, which that lock does not conflict
    with, so the generation is removed while the hold lasts, and no lock
    stop is logged.
    """
    inputs, owner, old, _ = superseded(tmp_path)
    with separate_session() as session, session.transaction():
        session.execute(
            "SELECT 1 FROM stewardship_campaign WHERE id=%s FOR KEY SHARE",
            [inputs.campaign_id],
        )
        assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]
    assert not OperationalLog.objects.filter(event="work_budget_reached").exists()


def test_fact_cleanup_waits_at_most_its_lock_limit_for_its_task(tmp_path):
    """Another session holding this task's row stops cleanup after LOCK_SECONDS."""
    inputs, owner, old, _ = superseded(tmp_path)
    with separate_session() as session, session.transaction():
        session.execute(
            "SELECT 1 FROM stewardship_task_run WHERE id=%s FOR UPDATE", [owner.run_id]
        )
        started = monotonic()
        assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
        assert retention.LOCK_SECONDS * 0.9 <= monotonic() - started < 3
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_compaction_marking_does_not_wait_for_the_source_pointer(history):  # noqa: F811
    """A reader share-locking the pointer (a Family form open) never blocks it."""
    task = running_source_task()
    with separate_session() as session, session.transaction():
        session.execute("SELECT 1 FROM stewardship_source_current FOR SHARE")
        with task_login(ServiceRole.WORKER, exact=True):
            claim = acquire_source(**task, phase="compaction")
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '2s'")
                result = compact_source(claim, admit=permit)
            finally:
                release_source(claim)
    assert result.snapshot_count == 1
    assert SourceSnapshot.objects.filter(compacted_at__isnull=False).count() == 1


def test_a_graceful_stop_still_releases_the_compaction_lease(history):  # noqa: F811
    """Releasing is admitted while draining; lost ownership still refuses."""
    task = running_source_task()
    execution = _Execution(task)
    claim = acquire_source(**task, phase="compaction")
    execution.control.stop.set()
    compaction._release_compaction(execution, claim)
    assert SourceMutationLease.objects.get().phase == "idle"
    claim = acquire_source(**task, phase="compaction")
    execution.control.failed.set()
    with pytest.raises(ExecutionInterrupted):
        compaction._release_compaction(execution, claim)
    assert SourceMutationLease.objects.get().phase == "compaction"
    release_source(claim)


def test_a_stop_requested_during_retention_leaves_the_lease_idle(history):  # noqa: F811
    """A whole retention run that a graceful stop interrupts still releases."""
    task = running_source_task()

    class Stopping(_Execution):
        """Asks the worker to stop as soon as the lease is held."""

        def maintain_source(self, claim):
            """Request a graceful stop on entry, then renew nothing."""
            self.control.stop.set()
            return super().maintain_source(claim)

    compaction.compact_before_refresh(Stopping(task))
    assert SourceMutationLease.objects.get().phase == "idle"


def test_fact_cleanup_gets_only_its_share_of_the_budget(history, monkeypatch):  # noqa: F811
    """Fact cleanup is given half the budget; snapshot retention keeps the rest."""
    seen = []

    def facts(execution, *, deadline, budget_seconds):
        """Record the fact phase's deadline instead of cleaning facts."""
        seen.append((deadline - monotonic(), budget_seconds))
        return []

    monkeypatch.setattr(compaction, "_compact_superseded_facts", facts)
    compaction.compact_before_refresh(_Execution(running_source_task()))
    share = compaction.RETENTION_BUDGET_SECONDS * compaction.FACT_BUDGET_SHARE
    ((remaining, budget),) = seen
    assert budget == share and share - 1 < remaining <= share
    # Snapshot retention still ran after the fact phase.
    assert SourceSnapshot.objects.filter(compacted_at__isnull=False).count() == 1


def test_fact_cleanup_stopped_by_its_budget_is_logged(tmp_path):
    """A fact cleanup that runs out of budget with work left logs the stop."""
    inputs, owner, old, _ = superseded(tmp_path)

    class Execution:
        """Only the claim and the drain check are used."""

        claim = owner

        def check(self):
            """Never draining."""

    assert (
        compaction._compact_superseded_facts(
            Execution(), deadline=monotonic() - 1, budget_seconds=7
        )
        == []
    )
    entry = OperationalLog.objects.get(event="work_budget_reached")
    assert entry.level == "INFO" and entry.context["what"] == "retention_budget"
    assert entry.context["limit_seconds"] == 7


def test_reclaim_chunks_run_under_a_statement_timeout(history):  # noqa: F811
    """Both reclaimers bound their own statements."""
    task = running_source_task()
    claim = acquire_source(**task, phase="compaction")
    try:
        for reclaim in (compaction._reclaim_memberships, compaction._reclaim_payloads):
            with CaptureQueriesContext(connection) as queries:
                reclaim()
            statement = (
                "SET LOCAL statement_timeout = "
                f"'{compaction.RECLAIM_STATEMENT_SECONDS}s'"
            )
            assert queries.captured_queries[1]["sql"] == statement
    finally:
        release_source(claim)


def test_a_reclaim_chunk_stopped_by_its_timeout_ends_the_drain(db):
    """The timed-out chunk rolls back, the drain stops, and the stop is logged
    as a WARNING task timeout naming the refresh task."""
    task = running_source_task()
    calls, tally = [], {"rows": 0}

    def slow():
        """One chunk whose statement outlives a 100 ms timeout."""
        calls.append(1)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '100ms'")
            cursor.execute("SELECT pg_sleep(1)")
        return 1

    compaction._drain(
        slow, monotonic() + 30, lambda: None, tally, "rows", task_id=task["task_id"]
    )
    assert calls == [1] and tally == {"rows": 0}
    entry = OperationalLog.objects.get(event="task_timed_out")
    assert entry.level == "WARNING" and entry.context["what"] == "statement_timeout"
    assert entry.context["limit_seconds"] == compaction.RECLAIM_STATEMENT_SECONDS
    assert entry.context["task_id"] == str(task["task_id"])
