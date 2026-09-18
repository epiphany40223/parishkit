"""Current source observations, not elapsed cooldowns, own alert recovery."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event as ThreadEvent
from time import monotonic, sleep

import pytest
from django.db import connection, connections

from parishkit.parishsoft_source import SourceOrganizationMismatch
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import WORK_ORDER_LOCK, work_transaction
from parishkit.stewardship.jobs import operational_collection
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.operational_content import IncidentKind, IncidentLevel
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalLogReceipt,
    OperationalNotice,
)
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_storage import record_observation
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source import health
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.errors import SourceScopeChanged
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.loading import DestructiveSourceChange
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.snapshots import promote_snapshot

from .campaign_builders import add_draft, change, restored_runtime
from .test_bootstrap_postgresql import bootstrapped as bootstrap_fixture
from .test_operational_collection_postgresql import consume, schedule
from .test_source_attempts_postgresql import configured, stage
from .test_source_requests_postgresql import claim, command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)
bootstrapped = bootstrap_fixture


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore only idle fresh-install seeds after the disposable test flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def publish(credential, *, kind="full"):
    """Promote a real scoped source attempt without network or arbitrary SQL edits."""
    base = SourceCurrent.objects.select_related("snapshot").get().snapshot
    execution = claim(
        command(**({"cause": "delta", "actor_id": None} if kind == "delta" else {}))
    )
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase=kind,
        )
    attempt = begin_refresh_attempt(execution, lease, credential)
    cursor = refresh_cursor(
        snapshot_id=attempt.snapshot_id,
        kind=kind,
        started_at=attempt.snapshot.started_at,
        window_digest=attempt.request.window_digest,
        evidence={},
        base_cursor=base.cursor if base is not None else None,
    )
    stage(attempt, execution, lease, cursor_changes=cursor)
    with execution.effect():
        result = promote_snapshot(
            attempt.snapshot_id, lease, admit=permit, reconcile=permit
        )
    release_source(lease)
    execution.transition("complete")
    return result


def observe():
    """Sample with the same global order used by the fenced collector."""
    with work_transaction():
        health.observe_source_health()


def future_observation(monkeypatch, seconds):
    """Move only the observation input, never provider, lease or incident SQL clocks."""
    original = health.require_source_refresh

    def shifted(**kwargs):
        """Keep actual admission while choosing a deterministic freshness boundary."""
        scope = original(**kwargs)
        return replace(scope, instant=scope.instant + timedelta(seconds=seconds))

    monkeypatch.setattr(health, "require_source_refresh", shifted)


def test_configured_quiet_source_gets_fenced_periodic_sample_and_no_false_alert(
    tmp_path,
):
    """No critical log or request traffic is needed to schedule the health sample."""
    configured(tmp_path)
    identifiers = schedule()
    assert len(identifiers) == 1
    assert consume(identifiers[0])
    assert not OperationalIncident.objects.exists()
    assert not consume(identifiers[0])


def test_missing_source_escalates_after_configured_grace_and_recovers_once(
    tmp_path, monkeypatch, settings
):
    """A real subsequent promotion resolves a stale episode, not timer passage."""
    credential, *_ = configured(tmp_path)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    with monkeypatch.context() as patch:
        future_observation(patch, 121)
        observe()
        observe()
    stale = OperationalIncident.objects.get(kind="source_stale")
    assert stale.occurrences == 2 and OperationalNotice.objects.count() == 1
    observe()
    stale.refresh_from_db()
    assert stale.resolved_at is None
    publish(credential)
    observe()
    observe()
    stale.refresh_from_db()
    assert stale.resolved_at is not None
    assert list(
        OperationalNotice.objects.order_by("created_at").values_list("phase", flat=True)
    ) == ["opened", "resolved"]


def test_old_window_success_cannot_resolve_current_source_incident(tmp_path):
    """A recent snapshot for the previous campaign window is not healthy proof."""
    credential, store, version, actor = configured(tmp_path)
    publish(credential)
    with work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_STALE, IncidentLevel.CRITICAL, policy=IncidentPolicy()
        )
    add_draft(store, version, actor)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    publish(credential)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None


def test_snapshot_staleness_uses_exact_pre_read_boundary(tmp_path, monkeypatch):
    """A recent promotion cannot hide an observation that began too long ago."""
    credential, *_ = configured(tmp_path)
    snapshot = publish(credential)
    original = health.require_source_refresh
    for elapsed, expected in ((1799, False), (1800, True)):

        def scoped(elapsed=elapsed, **kwargs):
            """Keep real admission; select only this observation's instant."""
            scope = original(**kwargs)
            return replace(
                scope, instant=snapshot.started_at + timedelta(seconds=elapsed)
            )

        monkeypatch.setattr(health, "require_source_refresh", scoped)
        observe()
        assert (
            OperationalIncident.objects.filter(kind="source_stale").exists() is expected
        )


@pytest.mark.parametrize(
    "error,kind",
    [
        (SourceOrganizationMismatch("PRIVATE"), "source_tenant_mismatch"),
        (DestructiveSourceChange("PRIVATE"), "source_destructive_change"),
    ],
)
def test_specific_failure_collection_and_new_success_resolve_once(
    tmp_path, monkeypatch, error, kind
):
    """Actual failure settlement, SQL receipt and current promotion form one chain."""
    credential, *_ = configured(tmp_path)
    execution = claim(command())
    settle_failed_read(execution, error)
    (identifier,) = schedule()
    assert consume(identifier)
    incident = OperationalIncident.objects.get(kind=kind)
    assert incident.resolved_at is None
    assert "PRIVATE" not in str(list(OperationalLog.objects.values()))
    publish(credential)
    # Only advance the scheduler's idempotency bucket to obtain the next sample.
    with work_transaction():
        instant = database_now() + timedelta(minutes=1)
    monkeypatch.setattr(operational_collection, "database_now", lambda: instant)
    (next_identifier,) = schedule()
    assert consume(next_identifier)
    incident.refresh_from_db()
    assert incident.resolved_at is not None
    assert (
        OperationalNotice.objects.filter(incident=incident, phase="resolved").count()
        == 1
    )


def test_delayed_failure_intake_does_not_reopen_recovered_source(tmp_path):
    """The pending log blocks recovery until its exact collector receipt commits."""
    credential, *_ = configured(tmp_path)
    settle_failed_read(claim(command()), SourceOrganizationMismatch("PRIVATE"))
    publish(credential)
    observe()
    assert not OperationalIncident.objects.exists()
    (identifier,) = schedule()
    assert consume(identifier)
    incident = OperationalIncident.objects.get(kind="source_tenant_mismatch")
    assert incident.resolved_at is not None
    assert list(
        OperationalNotice.objects.filter(incident=incident)
        .order_by("incident_version")
        .values_list("phase", flat=True)
    ) == ["opened", "resolved"]


@pytest.mark.parametrize(
    "event,level",
    [(Event.SOURCE_PROVIDER_FAILED, "WARNING"), (Event.SOURCE_HELD, "CRITICAL")],
)
def test_new_failure_blocks_recovery_even_after_its_log_is_receipted(
    tmp_path, monkeypatch, event, level
):
    """New transient or terminal failure invalidates pre-failure source evidence."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    with work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_REFRESH_FAILED,
            IncidentLevel.CRITICAL,
            policy=IncidentPolicy(),
        )
        operational(event, level=level)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    (identifier,) = schedule()
    consume(identifier)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    publish(credential)
    with work_transaction():
        instant = database_now() + timedelta(minutes=1)
    monkeypatch.setattr(operational_collection, "database_now", lambda: instant)
    (next_identifier,) = schedule()
    consume(next_identifier)
    incident.refresh_from_db()
    assert incident.resolved_at is not None


def test_intentional_admission_hold_is_neither_failure_nor_recovery(
    tmp_path, monkeypatch
):
    """Use the shared admission boundary; do not reinterpret a hold as an outage."""
    configured(tmp_path)
    with work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_STALE, IncidentLevel.CRITICAL, policy=IncidentPolicy()
        )

    def held(**kwargs):
        """Represent the actual typed restore/purge denial at the shared boundary."""
        raise SourceScopeChanged("Intentional hold")

    monkeypatch.setattr(health, "require_source_refresh", held)
    observe()
    assert schedule() == ()
    incident.refresh_from_db()
    assert incident.occurrences == 1 and incident.resolved_at is None


@pytest.mark.parametrize("sql_failure", [False, True])
def test_failed_sample_rolls_back_its_effects_without_silencing_intake(
    tmp_path, monkeypatch, sql_failure
):
    """Unrelated critical receipts survive, but no partial healthy sample commits."""
    configured(tmp_path)
    future_observation(monkeypatch, 1801)
    unrelated = operational(Event.TASK_FAILED, level="CRITICAL")
    (identifier,) = schedule()
    original = operational_collection.observe_source_health

    def interrupted():
        """Inject after a real incident/notice has been allocated."""
        original()
        if sql_failure:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")
        raise RuntimeError("Synthetic interruption")

    monkeypatch.setattr(operational_collection, "observe_source_health", interrupted)
    assert consume(identifier)
    assert OperationalLogReceipt.objects.filter(log=unrelated).exists()
    assert not OperationalIncident.objects.filter(kind="source_stale").exists()
    assert OperationalNotice.objects.count() == 1
    assert (
        OperationalLog.objects.filter(
            event=Event.SOURCE_INVALID, level="CRITICAL"
        ).count()
        == 1
    )
    assert "Synthetic" not in str(list(OperationalLog.objects.values()))
    with work_transaction():
        instant = database_now() + timedelta(minutes=1)
    monkeypatch.setattr(operational_collection, "database_now", lambda: instant)
    (next_identifier,) = schedule()
    assert consume(next_identifier)
    assert OperationalIncident.objects.get(kind="source_refresh_failed")
    assert not OperationalIncident.objects.filter(kind="source_stale").exists()


def test_completion_failure_rolls_back_successful_sample(tmp_path, monkeypatch):
    """A successful sample still commits only with its owning completed Task."""
    configured(tmp_path)
    future_observation(monkeypatch, 1801)
    (identifier,) = schedule()

    def fail_progress(*args, **kwargs):
        """Interrupt after the real observation, outside its isolated savepoint."""
        raise RuntimeError("Synthetic completion interruption")

    monkeypatch.setattr(Execution, "progress", fail_progress)
    with pytest.raises(RuntimeError, match="Synthetic"):
        consume(identifier)
    assert not OperationalIncident.objects.exists()
    assert not OperationalNotice.objects.exists()


def test_delta_cannot_resolve_destructive_loss_without_a_new_full_refresh(tmp_path):
    """Retained corpus plus change indications do not prove full counts recovered."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    settle_failed_read(claim(command()), DestructiveSourceChange("PRIVATE"))
    (identifier,) = schedule()
    consume(identifier)
    incident = OperationalIncident.objects.get(kind="source_destructive_change")
    assert publish(credential, kind="delta").kind == "delta"
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    publish(credential)
    # Collection may lag behind a subsequent delta; its full anchor is still proof.
    publish(credential, kind="delta")
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None


def test_info_source_hold_does_not_fence_new_success(tmp_path):
    """Ordinary contention is not a failed provider read or a terminal failure."""
    credential, *_ = configured(tmp_path)
    with work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_REFRESH_FAILED,
            IncidentLevel.CRITICAL,
            policy=IncidentPolicy(),
        )
    publish(credential)
    operational(Event.SOURCE_HELD, level="INFO")
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None


def test_actual_restore_gate_blocks_even_a_fresh_success_from_resolving(tmp_path):
    """Retained success cannot certify health during intentional restore review."""
    credential, *_ = configured(tmp_path)
    snapshot = publish(credential)
    with work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_STALE, IncidentLevel.CRITICAL, policy=IncidentPolicy()
        )
    with restored_runtime(snapshot.started_at):
        observe()
        assert schedule() == ()
        incident.refresh_from_db()
        assert incident.resolved_at is None and incident.occurrences == 1
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    publish(credential)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None


@pytest.mark.parametrize(
    "event,kind",
    [
        (Event.SOURCE_TENANT_MISMATCH, "source_tenant_mismatch"),
        (Event.SOURCE_HELD, "source_refresh_failed"),
    ],
)
def test_pending_source_page_prevents_premature_recovery(
    tmp_path, monkeypatch, event, kind
):
    """The first page cannot resolve while another failure lacks its receipt."""
    credential, *_ = configured(tmp_path)
    OperationalLog.objects.bulk_create(
        [
            OperationalLog(
                event=event,
                level="CRITICAL",
                schema="exception",
                context={},
            )
            for _ in range(operational_collection.BATCH_SIZE + 1)
        ]
    )
    publish(credential)
    (identifier,) = schedule()
    assert consume(identifier)
    incident = OperationalIncident.objects.get(kind=kind)
    assert incident.resolved_at is None
    with work_transaction():
        instant = database_now() + timedelta(minutes=1)
    monkeypatch.setattr(operational_collection, "database_now", lambda: instant)
    (next_identifier,) = schedule()
    assert consume(next_identifier)
    incident.refresh_from_db()
    assert incident.resolved_at is not None
    assert incident.occurrences == operational_collection.BATCH_SIZE + 1


def test_sample_waits_for_concurrent_failure_and_cannot_clear_it(tmp_path):
    """The real work-order lock excludes an uncommitted newer failure from recovery."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    started = ThreadEvent()
    backend = {}
    with work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_REFRESH_FAILED,
            IncidentLevel.CRITICAL,
            policy=IncidentPolicy(),
        )

    def sample_on_another_connection():
        """Each worker thread owns and closes its actual database connection."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend["pid"] = cursor.fetchone()[0]
            started.set()
            observe()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            operational(Event.SOURCE_PROVIDER_FAILED, level="WARNING")
            future = pool.submit(sample_on_another_connection)
            assert started.wait(5)
            deadline = monotonic() + 5
            while True:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                        "AND locktype='advisory' AND classid=%s AND objid=%s "
                        "AND objsubid=2 AND NOT granted)",
                        [backend["pid"], *WORK_ORDER_LOCK],
                    )
                    if cursor.fetchone()[0]:
                        break
                assert not future.done() and monotonic() < deadline
                sleep(0.01)
        future.result(timeout=10)
    incident.refresh_from_db()
    assert incident.resolved_at is None


def test_initial_grace_uses_activation_not_old_runtime_birth(tmp_path, monkeypatch):
    """A long-lived bootstrap runtime does not make newly applied source overdue."""
    configured(tmp_path)
    original = health.require_source_refresh

    def old_runtime(**kwargs):
        """Model old bootstrap time without changing SQL clocks or installed data."""
        scope = original(**kwargs)
        scope.runtime.created_at -= timedelta(days=1)
        return scope

    monkeypatch.setattr(health, "require_source_refresh", old_runtime)
    observe()
    assert not OperationalIncident.objects.exists()


@pytest.mark.parametrize(
    "missing,kind", [(False, "source_tenant_mismatch"), (True, "source_refresh_failed")]
)
def test_configuration_defect_is_collected_not_silently_held(
    tmp_path, monkeypatch, missing, kind
):
    """Actual applied config errors still enqueue work and reach safe notice intent."""
    credential, store, version, actor = configured(tmp_path)
    publish(credential)
    integration = version.document()["sections"]["integrations"][0]
    operation = {"section": "integrations", "id": integration["id"]}
    if missing:
        operation.update(operation="remove")
    else:
        operation.update(
            operation="update",
            values={"settings": {"organization_id": "999"}},
        )
    change(store, version, actor, [operation])
    (identifier,) = schedule()
    assert consume(identifier)
    assert OperationalLog.objects.filter(level="CRITICAL").count() == 1
    assert OperationalLog.objects.get(level="CRITICAL").schema == "action"
    with work_transaction():
        instant = database_now() + timedelta(minutes=1)
    monkeypatch.setattr(operational_collection, "database_now", lambda: instant)
    (next_identifier,) = schedule()
    assert consume(next_identifier)
    incident = OperationalIncident.objects.get(kind=kind)
    assert incident.resolved_at is None
    if not missing:
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "section": "integrations",
                    "id": integration["id"],
                    "operation": "update",
                    "values": {"settings": {"organization_id": "12345"}},
                }
            ],
        )
        observe()
        incident.refresh_from_db()
        assert incident.resolved_at is None
        publish(credential)
        monkeypatch.setattr(
            operational_collection,
            "database_now",
            lambda: instant + timedelta(minutes=1),
        )
        (last_identifier,) = schedule()
        assert consume(last_identifier)
        incident.refresh_from_db()
        assert incident.resolved_at is not None


def test_bootstrap_without_parish_configuration_is_not_an_outage(bootstrapped):
    """Technical login bootstrap has not admitted ordinary source refresh yet."""
    observe()
    assert schedule() == ()
    assert not OperationalLog.objects.exists()
    assert not OperationalIncident.objects.exists()


def test_unrelated_configuration_edit_cannot_restart_initial_grace(
    tmp_path, monkeypatch
):
    """The first admitted tenant's immutable activation remains the grace anchor."""
    _, store, version, actor = configured(tmp_path)
    initial = health.initial_source_at(12345)
    parish = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        actor,
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Updated Example"},
            }
        ],
    )
    assert health.initial_source_at(12345) == initial
    original = health.require_source_refresh

    def due(**kwargs):
        """Set exactly the original grace deadline, not a wall-clock sleep."""
        return replace(original(**kwargs), instant=initial + timedelta(seconds=1800))

    monkeypatch.setattr(health, "require_source_refresh", due)
    observe()
    assert OperationalIncident.objects.get(kind="source_stale").resolved_at is None


def test_looser_threshold_cannot_resolve_without_a_new_success(
    tmp_path, monkeypatch, settings
):
    """Settings and elapsed cooldowns do not manufacture a recovered observation."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=60)
    with monkeypatch.context() as patch:
        future_observation(patch, 61)
        observe()
    incident = OperationalIncident.objects.get(kind="source_stale")
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=3600)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    publish(credential)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None
