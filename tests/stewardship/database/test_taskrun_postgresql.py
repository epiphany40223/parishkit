"""Real task constraints, retry races, lease expiry and atomic history.

The admission stub represents an internal caller with synthetic proof only.
These tests do not run a broker, worker, provider, campaign or production action.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import F
from django.db.models.deletion import ProtectedError

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.models import TaskRun, TaskRunEvent
from parishkit.stewardship.jobs.storage import change_run, enqueue, retry_failed
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..configuration_factory import configuration_version

pytestmark = pytest.mark.django_db(transaction=True)


def permit(action, status):
    """Synthetic admission only; no operational callback is installed by this PR."""
    assert action and status.root_id


def new(**kwargs):
    """Give each synthetic logical operation an independent domain request."""
    return enqueue(
        **(
            dict(
                task_type="storage_probe",
                domain_request_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                admit=permit,
            )
            | kwargs
        )
    )


def act(status, action, **kwargs):
    """Use a fresh status version and the current worker identity/fence by default."""
    run = TaskRun.objects.get(pk=status.run_id)
    values = dict(
        run_id=status.run_id,
        expected_version=status.version,
        action=action,
        actor_id=run.worker_id,
        correlation_id=uuid4(),
        admit=permit,
    )
    if run.state == "running" and action != "lease_expired":
        values["fence"] = status.fence
    if action == "claim":
        values.update(actor_id=uuid4(), lease_seconds=60)
    if action in ("retryable_failure", "recovery_retry"):
        values["retry_seconds"] = 1
    if action == "lease_expired":
        values["actor_id"] = None
    return change_run(**(values | kwargs))


def expire(status):
    """Wait for a real one-second PostgreSQL lease; do not disable any guards."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    return act(status, "lease_expired")


def retry(status, **kwargs):
    """Allocate a synthetic Admin retry under the owning chain lock."""
    return retry_failed(
        **(
            dict(
                run_id=status.run_id,
                command_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                admit=permit,
            )
            | kwargs
        )
    )


def test_enqueue_claim_progress_heartbeat_completion_and_history():
    """All state and history survive reconnect; task success is not mail fulfillment."""
    status = new()
    assert status.state == "queued" and status.root_id == status.run_id
    status = act(status, "claim")
    assert (status.state, status.attempt, status.fence) == ("running", 1, 1)
    status = act(status, "progress", progress=(2, 5))
    status = act(status, "heartbeat", lease_seconds=60)
    status = act(status, "complete")
    assert status.state == "succeeded"
    connections.close_all()
    run = TaskRun.objects.get(pk=status.run_id)
    assert run.lease_expires_at is None and run.progress_current == 2
    history = list(run.events.order_by("version"))
    assert [event.action for event in history] == [
        "created",
        "claim",
        "progress",
        "heartbeat",
        "complete",
    ]
    assert [event.version for event in history] == list(range(1, 6))
    assert [event.previous_state for event in history] == [
        "",
        "queued",
        "running",
        "running",
        "running",
    ]
    assert all(event.attempt == 1 for event in history[1:])
    assert AuditEvent.objects.filter(subject_id=run.pk).count() == 5
    assert history[-1].created_at == run.updated_at


def test_enqueue_key_is_exact_and_callback_runs_on_replay():
    """Duplicate hints preserve execution identity, never skip fresh admission."""
    key, actor, domain = uuid4(), uuid4(), uuid4()
    calls = []
    args = dict(
        idempotency_key=key,
        actor_id=actor,
        domain_request_id=domain,
        admit=lambda action, status: calls.append(action),
    )
    first = new(**args)
    assert new(**args) == first and calls == ["enqueue", "enqueue"]
    with pytest.raises(ValueError, match="already bound"):
        new(**(args | {"domain_request_id": uuid4()}))
    with pytest.raises(ValueError, match="already bound"):
        new(**(args | {"actor_id": uuid4()}))
    assert calls == ["enqueue", "enqueue"]
    assert TaskRun.objects.count() == TaskRunEvent.objects.count() == 1
    with pytest.raises(FrozenInstanceError):
        first.state = "succeeded"


@pytest.mark.parametrize(
    "action", ["complete", "retryable_failure", "permanent_failure", "safe_cancel"]
)
def test_running_outcomes_and_terminal_immutability(action):
    """Every ordinary running outcome follows the canonical transition table."""
    status = act(new(), "claim")
    result = act(status, action)
    if result.state != "retry_wait":
        with pytest.raises(IntegrityError, match="Terminal"), transaction.atomic():
            TaskRun.objects.filter(pk=result.run_id).update(
                version=F("version") + 1, action="claim", state="running"
            )
    else:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
        claimed = act(result, "claim")
        assert (
            claimed.run_id == status.run_id
            and claimed.attempt == 2
            and claimed.fence == 2
        )


@pytest.mark.parametrize(
    "action,target",
    [
        ("recovery_retry", "retry_wait"),
        ("recovery_complete", "succeeded"),
        ("recovery_fail", "failed"),
        ("recovery_cancel", "cancelled"),
    ],
)
def test_abandonment_requires_expiry_and_verified_recovery(action, target):
    """Lease loss remains nonterminal until the trusted domain verifier resolves it."""
    status = act(new(), "claim", lease_seconds=1)
    abandoned = expire(status)
    assert abandoned.state == "abandoned" and abandoned.fence == status.fence + 1
    with pytest.raises(IntegrityError, match="transition"), transaction.atomic():
        act(abandoned, "claim")
    resolved = act(abandoned, action)
    assert resolved.state == target and resolved.attempt == status.attempt


def test_expired_owner_cannot_heartbeat_or_finish():
    """A late worker cannot revive a lease even before recovery marks abandonment."""
    status = act(new(), "claim", lease_seconds=1)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    for action, kwargs in (("complete", {}), ("heartbeat", {"lease_seconds": 60})):
        with (
            pytest.raises(IntegrityError, match="lease is expired"),
            transaction.atomic(),
        ):
            act(status, action, **kwargs)
    assert TaskRun.objects.get(pk=status.run_id).state == "running"


@pytest.mark.parametrize("retrying", [False, True])
def test_pending_cancellation_does_not_claim_attempt(retrying):
    """Queued/retry-wait cancellation leaves attempt identity and history intact."""
    status = new()
    if retrying:
        status = act(act(status, "claim"), "retryable_failure")
    cancelled = act(status, "safe_cancel", actor_id=uuid4())
    assert cancelled.state == "cancelled" and cancelled.attempt == status.attempt
    with pytest.raises(StorageInvariantError):
        retry(cancelled)


@pytest.mark.parametrize(
    "action",
    [
        "heartbeat",
        "complete",
        "retryable_failure",
        "permanent_failure",
        "recovery_retry",
        "recovery_complete",
        "recovery_fail",
        "recovery_cancel",
    ],
)
def test_queued_task_cannot_skip_claim_or_recovery(action):
    """The database denies even well-shaped action fields on the wrong state."""
    status = new()
    kwargs = {"lease_seconds": 60} if action == "heartbeat" else {}
    with pytest.raises(IntegrityError, match="transition"), transaction.atomic():
        act(status, action, **kwargs)


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"state": "succeeded"},
        {"attempt": 1},
        {"worker_id": uuid4()},
        {"idempotency_key": "private value"},
        {"root_id": uuid4()},
    ],
)
def test_root_creation_cannot_forge_execution_history(changes):
    """Initial database writes must start as clean self-rooted queued work."""
    identifier = uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRun.objects.create(
            **(
                dict(id=identifier, root_id=identifier, task_type="storage_probe")
                | changes
            )
        )
    assert not TaskRunEvent.objects.exists() and not AuditEvent.objects.exists()


def test_missing_admission_and_backward_progress_are_rejected():
    """No default admission or monotonic progress bypass exists in storage."""
    with pytest.raises(TypeError, match="callback"):
        new(admit=None)
    status = act(new(), "claim")
    status = act(status, "progress", progress=(2, 5))
    with pytest.raises(IntegrityError, match="backwards"), transaction.atomic():
        act(status, "progress", progress=(1, 5))
    with pytest.raises(ValueError, match="fencing"):
        act(new(), "safe_cancel", fence=1)


def test_explicit_retry_preserves_failed_history_and_command_binding():
    """Every retry is a new execution, not a new semantic operation or reopened row."""
    initial = act(act(new(idempotency_key=uuid4()), "claim"), "permanent_failure")
    command, actor = uuid4(), uuid4()
    second = retry(initial, command_id=command, actor_id=actor)
    assert retry(initial, command_id=command, actor_id=actor) == second
    assert second.root_id == initial.run_id and second.run_id != initial.run_id
    parent, child = (
        TaskRun.objects.get(pk=initial.run_id),
        TaskRun.objects.get(pk=second.run_id),
    )
    assert parent.state == "failed" and child.parent_id == parent.pk
    assert child.domain_request_id == parent.domain_request_id
    assert child.idempotency_key == f"retry:{parent.pk}:1"
    assert child.retry_sequence == 1
    with pytest.raises(ValueError, match="already bound"):
        retry(initial, command_id=command)
    with pytest.raises(StorageInvariantError):
        retry(initial)
    failed_again = act(act(second, "claim"), "permanent_failure")
    third = retry(failed_again)
    assert TaskRun.objects.get(pk=third.run_id).retry_sequence == 2
    assert retry(initial, command_id=command, actor_id=actor).state == "failed"


@pytest.mark.parametrize("kind", ["claim", "same_retry", "different_retry", "enqueue"])
def test_concurrent_commands_are_serialized(kind):
    """Independent connections cannot allocate parallel active runs or owners."""
    status = new()
    if "retry" in kind:
        status = act(act(status, "claim"), "permanent_failure")
    barrier, command, actor, domain = Barrier(2), uuid4(), uuid4(), uuid4()

    def perform(index):
        """Use independent sockets and start both callers before either claims."""
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            try:
                if kind == "claim":
                    return act(status, "claim").run_id
                if kind == "enqueue":
                    return new(
                        idempotency_key=command,
                        actor_id=actor,
                        domain_request_id=domain,
                    ).run_id
                return retry(
                    status,
                    command_id=command if kind == "same_retry" else uuid4(),
                    actor_id=actor,
                ).run_id
            except (StaleRecordError, StorageInvariantError):
                return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(perform, range(2)))
    if kind in ("claim", "different_retry"):
        assert results.count("rejected") == 1
    else:
        assert results[0] == results[1] != "rejected"
    assert (
        TaskRun.objects.filter(
            root_id=status.root_id,
            state__in=("queued", "running", "retry_wait", "abandoned"),
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    "action", ["enqueue", "claim", "explicit_retry", "recovery_complete"]
)
def test_admission_failure_has_no_state_history_or_audit_effects(action):
    """Proof/admission failure rolls back domain writes as well as task metadata."""
    status = new()
    if action == "explicit_retry":
        status = act(act(status, "claim"), "permanent_failure")
    if action == "recovery_complete":
        status = expire(act(status, "claim", lease_seconds=1))
    before = (
        TaskRun.objects.count(),
        TaskRunEvent.objects.count(),
        AuditEvent.objects.count(),
    )

    def denied(operation, receipt):
        """A related synthetic audit is rolled back if admission fails afterward."""
        AuditEvent.objects.create(event_type="synthetic_domain_check")
        raise PermissionError("Synthetic denial")

    with pytest.raises(PermissionError):
        if action == "enqueue":
            new(admit=denied)
        elif action == "explicit_retry":
            retry(status, admit=denied)
        else:
            act(status, action, admit=denied)
    assert before == (
        TaskRun.objects.count(),
        TaskRunEvent.objects.count(),
        AuditEvent.objects.count(),
    )


def test_audit_failure_rolls_back_task_and_event():
    """The SQL history emitter cannot commit a task without its durable audit."""
    status = new()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_audit_event ADD CONSTRAINT reject_task_claim "
            "CHECK (event_type <> 'task_claim') NOT VALID"
        )
        try:
            with (
                pytest.raises(IntegrityError, match="reject_task_claim"),
                transaction.atomic(),
            ):
                act(status, "claim")
            assert TaskRun.objects.get(pk=status.run_id).version == 1
            assert TaskRunEvent.objects.count() == AuditEvent.objects.count() == 1
        finally:
            cursor.execute(
                "ALTER TABLE stewardship_audit_event DROP CONSTRAINT reject_task_claim"
            )


def test_raw_writes_cannot_forge_chain_or_history():
    """SQL guards complement normal row locks and reject counterfeit evidence."""
    status = new()
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRun.objects.create(
            root_id=status.root_id,
            task_type="storage_probe",
            retry_sequence=1,
            parent_id=status.run_id,
            retry_command_id=uuid4(),
            action="explicit_retry",
        )
    event = TaskRunEvent(**TaskRunEvent.objects.values().get())
    event.id, event.version = uuid4(), 2
    with (
        pytest.raises(IntegrityError, match="transition evidence"),
        transaction.atomic(),
    ):
        event.save(force_insert=True)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_task_event")
    with pytest.raises(ProtectedError), transaction.atomic():
        TaskRun.objects.all().delete()
    with (
        pytest.raises(IntegrityError, match="Task history cannot be deleted"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DELETE FROM stewardship_task_run WHERE id = %s", [status.run_id]
        )


def test_future_deadlines_cannot_be_skipped():
    """Negative timing checks have a wide margin, independent of expiry tests."""
    status = act(new(), "claim", lease_seconds=300)
    with pytest.raises(IntegrityError, match="timing"), transaction.atomic():
        act(status, "lease_expired")
    waiting = act(status, "retryable_failure", retry_seconds=86400)
    with pytest.raises(IntegrityError, match="timing"), transaction.atomic():
        act(waiting, "claim")


def test_heartbeat_renews_deadline():
    """Persisted deadlines prove renewal without racing a one-second live lease."""
    status = act(new(), "claim", lease_seconds=60)
    original = TaskRun.objects.get(pk=status.run_id).lease_expires_at
    renewed = act(status, "heartbeat", lease_seconds=300)
    run = TaskRun.objects.get(pk=status.run_id)
    assert (run.lease_expires_at - original).total_seconds() >= 240
    assert (
        run.events.get(version=renewed.version).lease_expires_at == run.lease_expires_at
    )
    assert act(renewed, "complete").state == "succeeded"


def test_retry_claim_resets_attempt_progress():
    """A fresh attempt can honestly report less work than its failed predecessor."""
    status = act(act(new(), "claim"), "progress", progress=(500, 1000))
    status = act(status, "retryable_failure")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    claimed = act(status, "claim")
    event = TaskRunEvent.objects.get(run_id=claimed.run_id, version=claimed.version)
    assert (event.progress_current, event.progress_total) == (0, 0)
    progressed = act(claimed, "progress", progress=(10, 100))
    with pytest.raises(IntegrityError, match="backwards"), transaction.atomic():
        act(progressed, "progress", progress=(9, 100))
    assert TaskRunEvent.objects.filter(
        run_id=claimed.run_id, attempt=1, progress_current=500
    ).exists()


def test_retry_replay_checks_binding_before_admission():
    """A replay checks current policy without posing as a fresh budget allocation."""
    parent = act(act(new(), "claim"), "permanent_failure")
    command, actor, calls = uuid4(), uuid4(), []
    kwargs = dict(
        command_id=command,
        actor_id=actor,
        admit=lambda action, status: calls.append((action, status)),
    )
    child = retry(parent, **kwargs)
    assert retry(parent, **kwargs) == child
    assert calls == [("explicit_retry", parent), ("explicit_retry_replay", child)]
    assert child.parent_id == parent.run_id and child.retry_sequence == 1
    for target, changes in ((parent, {"actor_id": uuid4()}), (child, {})):
        with pytest.raises(ValueError, match="already bound"):
            retry(target, **(kwargs | changes))
    assert len(calls) == 2
    with pytest.raises(PermissionError):
        retry(parent, **(kwargs | {"admit": deny_replay}))


def deny_replay(action, status):
    """Synthetic revoked permission must still deny correctly bound retries."""
    assert action == "explicit_retry_replay"
    raise PermissionError("Synthetic revoked permission")


def test_statement_clock_owns_full_lease_and_retry_interval():
    """Minimum intervals are measured from the write, not an earlier clock read."""
    status = act(new(), "claim", lease_seconds=1)
    run = TaskRun.objects.get(pk=status.run_id)
    assert (run.lease_expires_at - run.updated_at).total_seconds() == 1
    assert status.worker_id == run.worker_id
    # Use a separate comfortably live claim for the retry transition so this
    # assertion never depends on catching the preceding one-second lease.
    waiting = act(act(new(), "claim"), "retryable_failure", retry_seconds=1)
    run = TaskRun.objects.get(pk=waiting.run_id)
    assert (run.not_before - run.updated_at).total_seconds() == 1


def test_rejected_transition_preserves_caller_transaction():
    """The helper's own savepoint isolates a SQL failure and callback effects."""
    status = new()

    def admitted(action, receipt):
        """This admission write belongs to the rejected operation, not its caller."""
        AuditEvent.objects.create(event_type="synthetic_rejected_callback")

    with transaction.atomic():
        AuditEvent.objects.create(event_type="synthetic_outer_before")
        with pytest.raises(IntegrityError):
            act(status, "complete", admit=admitted)
        AuditEvent.objects.create(event_type="synthetic_outer_after")
        assert not connection.needs_rollback
    assert (
        AuditEvent.objects.filter(event_type__startswith="synthetic_outer").count() == 2
    )
    assert not AuditEvent.objects.filter(
        event_type="synthetic_rejected_callback"
    ).exists()
    assert TaskRun.objects.get(pk=status.run_id).version == 1


@pytest.mark.parametrize("keyed", [False, True])
def test_unrelated_enqueues_do_not_share_an_allocation_lock(keyed):
    """Both callbacks must run concurrently even inside caller-owned transactions."""
    barrier = Barrier(2)

    def admitted(action, status):
        """A global enqueue lock would prevent the other callback from arriving."""
        barrier.wait(timeout=10)

    def perform(index):
        """Each independent connection retains its locks through the outer commit."""
        connections.close_all()
        try:
            with transaction.atomic():
                return new(
                    idempotency_key=uuid4() if keyed else None, admit=admitted
                ).run_id
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(perform, range(2)))
    assert len(set(results)) == TaskRun.objects.count() == 2


@pytest.mark.parametrize("action", ["enqueue", "claim", "explicit_retry"])
def test_composed_domain_writes_share_task_correlation(action):
    """Admission-created records inherit the supplied task correlation context."""
    status = new()
    if action == "explicit_retry":
        status = act(act(status, "claim"), "permanent_failure")
    identifier = uuid4()

    def admitted(operation, receipt):
        """The owning service need not repeat correlation IDs on each record."""
        AuditEvent.objects.create(event_type="synthetic_composed_write")

    kwargs = dict(admit=admitted, correlation_id=identifier)
    if action == "enqueue":
        result = new(**kwargs)
    elif action == "explicit_retry":
        result = retry(status, **kwargs)
    else:
        result = act(status, action, **kwargs)
    assert (
        AuditEvent.objects.get(event_type="synthetic_composed_write").correlation_id
        == identifier
    )
    assert TaskRun.objects.get(pk=result.run_id).correlation_id == identifier


def test_empty_migrations_roundtrip_restores_guards():
    """The documented empty downgrade/reapply restores the actual SQL protection."""
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        MigrationExecutor(connection).migrate([("stewardship_jobs", None)])
        assert "stewardship_task_run" not in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate(leaves)
    identifier = uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRun.objects.create(
            id=identifier,
            root_id=identifier,
            task_type="storage_probe",
            state="succeeded",
        )
    status = act(new(), "claim")
    assert TaskRunEvent.objects.filter(run_id=status.run_id).count() == 2


@pytest.mark.parametrize(
    "values",
    [
        {"root_id": uuid4()},
        {"task_type": "changed"},
        {"initiated_by_id": uuid4()},
        {"retry_sequence": 9},
        {"idempotency_key": "private input"},
    ],
)
def test_raw_identity_edits_are_blocked(values):
    """Even a valid version increment cannot change operation/request bindings."""
    status = new()
    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        TaskRun.objects.filter(pk=status.run_id).update(version=2, **values)


@pytest.mark.parametrize(
    "kwargs", [{"fence": 99}, {"actor_id": uuid4()}, {"expected_version": 999}]
)
def test_stale_worker_tokens_are_rejected(kwargs):
    """Expected version, worker identity and fence all bind worker mutations."""
    status = act(new(), "claim")
    calls = []
    with pytest.raises(StaleRecordError):
        act(status, "complete", admit=lambda *args: calls.append(args), **kwargs)
    assert calls == []


def test_retry_command_identity_is_chain_local():
    """Separate logical operations do not accidentally share command allocation."""
    parents = [act(act(new(), "claim"), "permanent_failure") for _ in range(2)]
    command, actor = uuid4(), uuid4()
    children = [retry(parent, command_id=command, actor_id=actor) for parent in parents]
    assert children[0].root_id != children[1].root_id
    for parent, child in zip(parents, children, strict=True):
        assert retry(parent, command_id=command, actor_id=actor) == child


@pytest.mark.parametrize("configured", [False, True])
def test_task_audit_ownership_uses_existing_insert_guard(configured, tmp_path):
    """SQL-emitted task audits inherit actual parish ownership after activation."""
    if configured:
        prepare_initial_configuration(
            AuthorityStore(tmp_path, validate_sections),
            configuration_version(),
            testing_recipient="test@example.org",
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
    status = act(new(), "claim")
    events = AuditEvent.objects.filter(subject_id=status.run_id)
    assert events.count() == 2
    expected = (
        ("parish", Parish.objects.get().pk) if configured else ("deployment", None)
    )
    assert set(events.values_list("ownership_scope", "parish_id")) == {expected}
    assert set(events.values_list("campaign_reference", flat=True)) == {None}


def test_populated_downgrade_refuses_before_guard_removal():
    """A refused downgrade leaves the schema marker, history and guards intact."""
    status = new()
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError, match="Task history prevents"):
            MigrationExecutor(connection).migrate(
                [("stewardship_jobs", "0001_taskrun_storage")]
            )
        assert MigrationRecorder.Migration.objects.filter(
            app="stewardship_jobs", name="0002_taskrun_guards"
        ).exists()
        assert TaskRunEvent.objects.count() == AuditEvent.objects.count() == 1
        assert act(status, "claim").state == "running"
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_emitter_ignores_temporary_audit_and_context_tables():
    """Task-trigger audit writes cannot be diverted to session-local shadow tables."""
    with transaction.atomic(durable=True), connection.cursor() as cursor:
        cursor.execute("SET LOCAL search_path = pg_temp, public")
        cursor.execute(
            "CREATE TEMP TABLE stewardship_audit_event "
            "(LIKE public.stewardship_audit_event INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        status = new()
        act(status, "claim")
        cursor.execute("SELECT count(*) FROM pg_temp.stewardship_audit_event")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM public.stewardship_audit_event")
        assert cursor.fetchone()[0] == 2
