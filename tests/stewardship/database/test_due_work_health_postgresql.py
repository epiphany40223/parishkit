"""Real admitted scans, restricted roles and notification intent, without sleeps."""

from datetime import timedelta
from threading import Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import due_work_health
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, claim_hint
from parishkit.stewardship.jobs.due_work_health import DueWorkScan
from parishkit.stewardship.jobs.due_work_models import DueWorkHealth
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
)
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import (
    HintPublicationUnavailable,
    scan_once,
    scheduler_session,
)
from parishkit.stewardship.observability import Event as LogEvent

from .test_background_grants_postgresql import task_login
from .test_operational_collection_postgresql import consume, schedule

pytestmark = pytest.mark.django_db(transaction=True)


def old_task():
    """Insert a legitimate queued record with a due time already in the past."""
    identifier = uuid4()
    with transaction.atomic():
        return TaskRun.objects.create(
            id=identifier,
            root_id=identifier,
            task_type="dispatch_probe",
            not_before=database_now() - timedelta(minutes=20),
        )


def registry(admit=lambda *args: True):
    """Use the compiled dispatch interface without a provider or secret dependency."""
    return {"dispatch_probe": Handler(WorkQueue.GENERAL, admit, lambda _: None)}


def scan(*, handlers=None, **kwargs):
    """Observe with the actual metadata-only scheduler identity and session lock."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return scan_once(
            guard,
            handlers=registry() if handlers is None else handlers,
            publish=lambda _: None,
            health=DueWorkScan(),
            **kwargs,
        )


def test_due_work_is_admitted_and_held_work_cannot_open_or_resolve():
    """Old queue age alone is insufficient; held work invalidates positive proof."""
    old_task()
    scan(handlers=registry(lambda *args: False))
    sample = DueWorkHealth.objects.get()
    assert (sample.signal, sample.late_since, sample.clear_since) == (
        "unknown",
        None,
        None,
    )
    assert schedule() == ()
    scan()
    sample.refresh_from_db()
    assert sample.signal == "late" and sample.late_since is not None
    assert schedule() == ()
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        due_work_health.observe_due_work_health()
    # A first observation cannot pretend to prove sustained lateness.
    assert not OperationalIncident.objects.exists()
    scan(handlers=registry(lambda *args: False))
    sample.refresh_from_db()
    assert sample.signal == "late" and sample.late_since is not None


def test_partial_sweep_and_shutdown_never_publish_recovery():
    """A healthy prefix is not the entire queue; graceful stop discards proof."""
    old_task()
    health = DueWorkScan()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        first = scan_once(guard, handlers={}, publish=lambda _: None, health=health)
        assert first.cursor is None
        baseline = DueWorkHealth.objects.get().observed_at
        # This held first page cannot leave the previous clear sample current.
        first = scan_once(
            guard,
            handlers=registry(lambda *args: False),
            publish=lambda _: None,
            health=health,
            limit=1,
        )
        assert first.cursor is not None
        assert DueWorkHealth.objects.get().signal == "unknown"
        stop = Event()
        stop.set()
        scan_once(
            guard, handlers=registry(), publish=lambda _: None, health=health, stop=stop
        )
        assert health.started_at is None
        assert DueWorkHealth.objects.get().signal == "unknown"
        health.finish(guard, complete=True)
        assert DueWorkHealth.objects.get().signal == "unknown"
        assert DueWorkHealth.objects.get().observed_at > baseline


def test_live_long_task_is_not_an_expired_worker():
    """A due time twenty minutes ago is harmless while its claim remains live."""
    task = old_task()
    claim = claim_hint(
        task.pk, queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers=registry()
    )
    claim.heartbeat()
    scan()
    sample = DueWorkHealth.objects.get()
    assert sample.signal == "clear" and sample.late_since is None
    assert schedule() == ()
    assert TaskRun.objects.get(pk=task.pk).state == "running"


def test_unconfirmed_hint_is_not_positive_recovery_evidence():
    """Broker acceptance is not required for replay, but failure cannot mean clear."""
    from .test_dispatch_postgresql import queued

    queued()

    def unavailable(hint):
        """Inject a known bounded broker refusal without changing durable work."""
        raise HintPublicationUnavailable()

    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        result = scan_once(
            guard, handlers=registry(), publish=unavailable, health=DueWorkScan()
        )
    assert result.unconfirmed == 1
    assert DueWorkHealth.objects.get().signal == "unknown"
    assert TaskRun.objects.get().state == "queued"


def test_checkpoint_requires_session_ownership_and_narrow_writer_grants():
    """No consumer can forge scheduler evidence or caller-selected health times."""
    with transaction.atomic():
        instant = database_now()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        with pytest.raises(IntegrityError), transaction.atomic():
            DueWorkHealth.objects.create(
                signal="clear", scan_started_at=instant, escalation_seconds=900
            )
        with scheduler_session():
            DueWorkHealth.objects.create(
                signal="clear",
                scan_started_at=instant,
                clear_since=instant - timedelta(days=1),
                observed_at=instant - timedelta(days=1),
                escalation_seconds=900,
            )
            sample = DueWorkHealth.objects.get()
            assert sample.clear_since == instant and sample.observed_at >= instant
            with pytest.raises(DatabaseError) as denied, transaction.atomic():
                DueWorkHealth.objects.update(clear_since=instant)
            assert denied.value.__cause__.sqlstate == "42501"
    with task_login(ServiceRole.WORKER, exact=True):
        assert DueWorkHealth.objects.get().signal == "clear"
        for statement in (
            "UPDATE stewardship_due_work_health SET signal='clear'",
            "DELETE FROM stewardship_due_work_health",
            "INSERT INTO stewardship_due_work_health DEFAULT VALUES",
        ):
            with (
                pytest.raises(DatabaseError) as denied,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert denied.value.__cause__.sqlstate == "42501"


def test_sql_continuity_windows_break_on_gaps_holds_and_backward_samples():
    """Test our installed window calculation, not PostgreSQL's time arithmetic."""
    with transaction.atomic():
        instant = database_now()
    earlier = instant - timedelta(minutes=20)
    with connection.cursor() as cursor:
        for signal in ("late", "clear"):
            for previous_signal, gap, previous_start, expected in (
                (signal, 20, earlier, earlier),
                (signal, 90, earlier, earlier),
                (signal, 91, earlier, earlier if signal == "late" else instant),
                (signal, 300, earlier, earlier if signal == "late" else instant),
                (signal, 301, earlier, instant),
                (signal, 7200, earlier, instant),
                ("unknown", 20, earlier, instant),
                (signal, -1, earlier, instant),
                (signal, 20, instant + timedelta(seconds=1), instant),
            ):
                cursor.execute(
                    "SELECT stewardship_due_work_since_v1(%s,%s,%s,%s,%s,%s,%s)",
                    [
                        previous_signal,
                        instant - timedelta(seconds=gap),
                        previous_start,
                        earlier,
                        signal,
                        instant,
                        instant,
                    ],
                )
                assert cursor.fetchone()[0] == expected


def test_collector_escalation_suppression_and_fresh_recovery(monkeypatch):
    """Advance the sample clock, not real runtime or schema guards, for long windows.

    Real role checks, incident triggers and notices remain installed. SQL window
    derivation and scheduler persistence are independently exercised above.
    """
    old_task()
    scan()
    operational(LogEvent.DUE_WORK_LAG, level="CRITICAL")
    operational(LogEvent.DUE_WORK_LAG, level="CRITICAL")
    (identifier,) = schedule()
    consume(identifier)
    sample = DueWorkHealth.objects.get()
    monkeypatch.setattr(DueWorkHealth.objects, "first", lambda: sample)
    version = OperationalIncident.objects.get().version
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        due_work_health.observe_due_work_health()
        due_work_health.observe_due_work_health()
    incident = OperationalIncident.objects.get()
    assert incident.version == version
    assert incident.kind == "scheduler_lag" and incident.occurrences == 2
    assert OperationalNotice.objects.get().phase == "opened"
    # A stale or missing sample cannot clear an otherwise active incident.
    sample.signal = "clear"
    sample.clear_since = sample.observed_at - timedelta(minutes=10)
    sample.observed_at -= timedelta(minutes=3)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        due_work_health.observe_due_work_health()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    # The positive window must begin after the retained failure observation.
    sample.clear_since = incident.last_seen + timedelta(seconds=1)
    sample.scan_started_at = sample.clear_since + timedelta(minutes=5)
    sample.observed_at = sample.scan_started_at
    monkeypatch.setattr(due_work_health, "database_now", lambda: sample.observed_at)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        due_work_health.observe_due_work_health()
        due_work_health.observe_due_work_health()
    incident.refresh_from_db()
    assert incident.resolved_at is not None
    assert list(
        OperationalNotice.objects.order_by("incident_version").values_list(
            "phase", flat=True
        )
    ) == ["opened", "resolved"]


def test_sql_critical_policy_retains_only_sustained_bounded_failure_samples():
    """Exercise the exact application predicate invoked by the checkpoint guard."""
    with transaction.atomic():
        instant = database_now()
    with connection.cursor() as cursor:
        for signal, age, prior_age, expected in (
            ("late", 899, None, False),
            ("late", 900, None, True),
            ("late", 1000, 59, False),
            ("late", 1000, 60, True),
            ("clear", 1000, None, False),
            ("unknown", 1000, None, False),
            ("late", -1, None, False),
        ):
            cursor.execute(
                "SELECT stewardship_due_work_critical_v1(%s,%s,%s,900,%s)",
                [
                    signal,
                    instant - timedelta(seconds=age),
                    None
                    if prior_age is None
                    else instant - timedelta(seconds=prior_age),
                    instant,
                ],
            )
            assert cursor.fetchone()[0] is expected


def test_full_sweep_required_and_old_sweep_cannot_certify_recovery():
    """The last empty page completes proof; a slow traversal does not refresh it."""
    from .test_dispatch_postgresql import queued

    queued()
    health = DueWorkScan()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        result = scan_once(
            guard, handlers=registry(), publish=lambda _: None, health=health, limit=1
        )
        assert result.cursor is not None and not DueWorkHealth.objects.exists()
        result = scan_once(
            guard,
            handlers=registry(),
            publish=lambda _: None,
            health=health,
            limit=1,
            cursor=result.cursor,
        )
        assert result.cursor is None and DueWorkHealth.objects.get().signal == "clear"
        health.begin()
        health.started_at -= timedelta(minutes=2)
        health.finish(guard, complete=True)
        sample = DueWorkHealth.objects.get()
        assert sample.signal == "unknown" and sample.clear_since is None


def test_broken_scope_and_expired_lease_age_are_not_positive_evidence():
    """Scope failure invalidates recovery; lease age starts at expiry, not enqueue."""
    from types import SimpleNamespace

    from parishkit.stewardship.storage import StorageInvariantError

    old_task()

    def broken(*args):
        """Represent a missing owning domain without exposing its payload."""
        raise StorageInvariantError("synthetic private scope")

    scan(handlers=registry(broken))
    assert DueWorkHealth.objects.get().signal == "unknown"
    with transaction.atomic():
        instant = database_now()
    for state in ("running", "abandoned"):
        health = DueWorkScan()
        row = SimpleNamespace(state=state, lease_expires_at=instant, updated_at=instant)
        health.admitted(row, instant + timedelta(seconds=89))
        assert not health.late
        health.admitted(row, instant + timedelta(seconds=91))
        assert health.late


def test_pending_failure_receipt_blocks_recovery(monkeypatch):
    """An unconsumed failure cannot be overtaken by apparently clear scans."""
    from parishkit.stewardship.jobs.operational_sources import critical_log

    scan()
    sample = DueWorkHealth.objects.get()
    sample.clear_since = sample.scan_started_at - timedelta(minutes=6)
    with task_login(ServiceRole.WORKER, exact=True):
        critical_log(LogEvent.DUE_WORK_LAG)
    # A second real immutable input remains unconsumed; neither its receipt nor
    # the current incident is fabricated.
    operational(LogEvent.DUE_WORK_LAG, level="CRITICAL")
    monkeypatch.setattr(DueWorkHealth.objects, "first", lambda: sample)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        due_work_health.observe_due_work_health()
    assert OperationalIncident.objects.get().resolved_at is None


def aged_checkpoint():
    """Seed only a prior-time test fixture, then restore all guards before action.

    This disposable test database represents an already observed late episode.
    Runtime writes, grants, SQL timing, log triggers and intake remain real.
    Avoid sleeping fifteen minutes or weakening the production clock boundary.
    """
    old_task()
    scan()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_due_work_health "
            "DISABLE TRIGGER stewardship_due_work_health_guard"
        )
        cursor.execute(
            "UPDATE stewardship_due_work_health SET "
            "late_since=clock_timestamp()-interval '20 minutes', "
            "scan_started_at=clock_timestamp()-interval '41 seconds', "
            "observed_at=clock_timestamp()-interval '40 seconds'"
        )
        cursor.execute(
            "ALTER TABLE stewardship_due_work_health "
            "ENABLE TRIGGER stewardship_due_work_health_guard"
        )


def test_actual_scheduler_trigger_retains_failure_and_throttles_then_intake():
    """The real restricted checkpoint writer must actually emit the outage log."""
    from parishkit.stewardship.audit.models import OperationalLog

    aged_checkpoint()
    scan()
    sample = DueWorkHealth.objects.get()
    failure = OperationalLog.objects.get(event=LogEvent.DUE_WORK_LAG)
    assert failure.level == "CRITICAL" and failure.context == {}
    assert sample.last_failure_at is not None
    observed = sample.observed_at
    scan()
    assert OperationalLog.objects.filter(event=LogEvent.DUE_WORK_LAG).count() == 1
    assert DueWorkHealth.objects.get().observed_at == observed
    # Force the checkpoint path past the Python rate limit, not the SQL event
    # throttle, to prove the latter independently suppresses repeated intent.
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE stewardship_due_work_health SET signal='late'")
    assert OperationalLog.objects.filter(event=LogEvent.DUE_WORK_LAG).count() == 1
    assert DueWorkHealth.objects.get().last_failure_at == sample.last_failure_at
    (identifier,) = schedule()
    consume(identifier)
    assert OperationalIncident.objects.get().kind == "scheduler_lag"
    assert OperationalNotice.objects.get().phase == "opened"


def test_healthy_idle_observation_avoids_history_queries_and_redundant_writes():
    """Idle monitoring does not grow task/history storage or scan old critical logs."""
    from django.test.utils import CaptureQueriesContext

    scan()
    first = DueWorkHealth.objects.get().observed_at
    scan()
    assert DueWorkHealth.objects.get().observed_at == first
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        with CaptureQueriesContext(connection) as queries:
            due_work_health.observe_due_work_health()
        assert not any(
            table in query["sql"]
            for query in queries
            for table in ("stewardship_due_work_health", "stewardship_operational_log")
        )


def test_interrupted_sweep_invalidates_window_without_certifying_suffix():
    """An exception resets proof, not the scheduler's existing fairness cursor."""
    health = DueWorkScan()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        health.begin()
        health.finish(guard, complete=True)
        assert DueWorkHealth.objects.get().signal == "clear"
        health.interrupted(guard)
        assert health.started_at is None
        assert DueWorkHealth.objects.get().signal == "unknown"


def test_inconclusive_prefix_and_interruption_preserve_unrenewed_negative_evidence():
    """Held prefixes cannot hide sustained lag or renew a stale negative sample."""
    from parishkit.stewardship.audit.models import OperationalLog

    aged_checkpoint()
    prior = DueWorkHealth.objects.get()
    health = DueWorkScan()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        health.interrupted(guard)
        assert DueWorkHealth.objects.get().observed_at == prior.observed_at
        health.begin()
        health.unknown()
        health.finish(guard, complete=False)
        sample = DueWorkHealth.objects.get()
        assert (sample.signal, sample.late_since, sample.observed_at) == (
            "late",
            prior.late_since,
            prior.observed_at,
        )
    assert not OperationalLog.objects.filter(event=LogEvent.DUE_WORK_LAG).exists()
    # A later admitted page supplies fresh negative evidence, unlike the hold.
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        health.late = True
        health.finish(guard, complete=True)
        sample.refresh_from_db()
        assert sample.late_since == prior.late_since
        assert sample.observed_at > prior.observed_at
    assert OperationalLog.objects.filter(event=LogEvent.DUE_WORK_LAG).count() == 1


def test_scheduler_checkpoint_waits_for_actual_worker_observation_transaction():
    """Two real role connections serialize the writer with the recovery reader."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    from django.db import connections

    from parishkit.stewardship.jobs.operational_sources import critical_log

    scan()
    critical_log(LogEvent.DUE_WORK_LAG)
    started, backend = Event(), {}

    def writer():
        """Retain a separate exact scheduler login and close its thread-local socket."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_scheduler")
                cursor.execute("SELECT pg_backend_pid()")
                backend["pid"] = cursor.fetchone()[0]
            with scheduler_session() as guard:
                started.set()
                DueWorkScan().interrupted(guard)
        finally:
            connections.close_all()

    with task_login(ServiceRole.SCHEDULER, exact=True):
        # Keep this fixture scheduler role installed for the other connection;
        # independently create the actual worker role for the main connection.
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        with (
            task_login(ServiceRole.WORKER, exact=True),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            with work_transaction():
                due_work_health.observe_due_work_health()
                future = pool.submit(writer)
                assert started.wait(5)
                deadline = monotonic() + 1
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                            "AND locktype='advisory' AND classid=736246 AND objid=1 "
                            "AND objsubid=2 AND NOT granted)",
                            [backend["pid"]],
                        )
                        if cursor.fetchone()[0]:
                            break
                    assert not future.done() and monotonic() < deadline
                    sleep(0.01)
            future.result(timeout=5)
    assert DueWorkHealth.objects.get().signal == "unknown"
