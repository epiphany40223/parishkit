"""Delivery journal contracts on real PostgreSQL, without a provider or worker API."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import F
from django.db.models.functions import Now

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_states import DeliveryAction as Action
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import (
    OutboxEvent,
    OutboxMessage,
    OutboxRender,
)
from parishkit.stewardship.jobs.outbox_storage import change_message, create_message
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryEvidence,
    DeliveryIdentity,
)
from parishkit.stewardship.jobs.storage import change_run, retry_failed
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from ..configuration_factory import configuration_version
from ..test_outbox_validation import rendering

pytestmark = pytest.mark.django_db(transaction=True)


def permit(*args):
    """Synthetic owner proof for internal storage only; no runtime port is granted."""
    return True


def provider_evidence():
    """Explicit synthetic provider proof; never used by a real dispatcher."""
    return DeliveryEvidence(
        provider_message_digest="a" * 64,
        evidence_digest="b" * 64,
        evidence_note="Synthetic provider outcome verified by this test",
    )


@pytest.fixture
def inputs(tmp_path):
    """Prepare synthetic parish metadata without live configuration or credentials."""
    version = configuration_version()
    prepare_initial_configuration(
        AuthorityStore(tmp_path, validate_sections),
        version,
        testing_recipient="test@example.org",
        actor_id=uuid4(),
        correlation_id=uuid4(),
    )
    return dict(
        identity=DeliveryIdentity(
            scope_id=Parish.objects.get().pk,
            semantic_key=uuid4(),
            mode="testing",
            routing="operational",
            purpose="operational",
        ),
        render=rendering(configuration_id=version.version_id),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )


def change(status, action, **options):
    """Use the latest optimistic version while keeping each command independent."""
    return change_message(
        **(
            dict(
                message_id=status.message_id,
                action=action,
                command_id=uuid4(),
                expected_version=status.version,
                actor_id=status.worker_id or uuid4(),
                correlation_id=uuid4(),
                admit=permit,
            )
            | options
        )
    )


def claim(status, *, run_id=None, lease_seconds=300):
    """Claim only a synthetic TaskRun; no external worker is launched."""
    row = TaskRun.objects.get(pk=run_id or status.task_id)
    worker = uuid4()
    return change_run(
        run_id=row.pk,
        action="claim",
        expected_version=row.version,
        actor_id=worker,
        correlation_id=uuid4(),
        admit=permit,
        lease_seconds=lease_seconds,
    )


def submit(status, *, task=None, **options):
    """Pin a numbered provider boundary to the currently claimed test task."""
    task = task or claim(status)
    return change(
        status,
        Action.SUBMIT,
        **(
            dict(
                run_id=task.run_id,
                task_fence=task.fence,
                actor_id=task.worker_id,
                provider_seconds=30,
            )
            | options
        ),
    )


def test_create_submit_accept_preserves_render_and_history(inputs):
    """Provider acceptance is terminal; unrelated audit history contains no body."""
    first = create_message(**inputs)
    assert create_message(**inputs) == first
    submitted = submit(first)
    accepted = change(
        submitted,
        Action.ACCEPT,
        evidence=provider_evidence(),
    )
    connections.close_all()
    message = OutboxMessage.objects.get(pk=first.message_id)
    assert (accepted.state, message.attempt) == ("delivered", 1)
    assert message.finished_at is not None and message.sealed_substitutions is None
    history = list(message.events.order_by("version"))
    assert [event.action for event in history] == ["created", "submit", "accept"]
    assert {event.render_id for event in history} == {first.render_id}
    assert history[-1].provider_message_digest == "a" * 64
    assert list(
        AuditEvent.objects.filter(subject_id=message.pk)
        .order_by("created_at")
        .values_list("event_type", flat=True)
    ) == ["outbox_created", "outbox_submit", "outbox_accept"]
    with pytest.raises(ValueError):
        change(accepted, Action.AUTHORIZE_RESEND)


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_creation_denial_has_no_task_render_or_history(inputs, answer):
    """Exact-True admission is required before any durable allocation."""
    before = TaskRun.objects.count()
    with pytest.raises(PermissionError):
        create_message(**(inputs | {"admit": lambda *args: answer}))
    assert not OutboxMessage.objects.exists()
    assert not OutboxRender.objects.exists() and not OutboxEvent.objects.exists()
    assert TaskRun.objects.count() == before


def test_late_creation_denial_rolls_back_initial_admission(inputs):
    """A denial at the task owner also removes the entire surrounding operation."""

    def deny_task(action, identity, status):
        """Permit domain preparation but reject the nested task allocation."""
        return action != "create_task"

    with pytest.raises(PermissionError):
        create_message(**(inputs | {"admit": deny_task}))
    assert not OutboxMessage.objects.exists() and not OutboxEvent.objects.exists()
    assert not TaskRun.objects.filter(task_type="outbox_delivery").exists()


def test_concurrent_producers_share_one_semantic_delivery(inputs):
    """Independent PostgreSQL connections race the same logical mail."""
    barrier = Barrier(2)

    def produce():
        """Each thread owns and closes its independent connection."""
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            return create_message(**inputs)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: produce(), range(2)))
    assert results[0] == results[1]
    assert (
        OutboxMessage.objects.count()
        == OutboxRender.objects.count()
        == OutboxEvent.objects.count()
        == 1
    )


def test_replays_recheck_admission_and_cannot_change_recipients(inputs):
    """An existing delivery key is not an authority or a render-edit capability."""
    first = create_message(**inputs)
    with pytest.raises(PermissionError):
        create_message(**(inputs | {"admit": lambda *args: False}))
    with pytest.raises(ValueError, match="already bound"):
        create_message(
            **(inputs | {"render": replace(inputs["render"], text="Changed")})
        )
    actor, command = uuid4(), uuid4()
    cancelled = change(first, Action.CANCEL_UNSENT, actor_id=actor, command_id=command)
    assert (
        change(first, Action.CANCEL_UNSENT, actor_id=actor, command_id=command)
        == cancelled
    )
    with pytest.raises(PermissionError):
        change(
            first,
            Action.CANCEL_UNSENT,
            actor_id=actor,
            command_id=command,
            admit=lambda *args: False,
        )
    with pytest.raises(ValueError, match="already bound"):
        change(first, Action.ACCEPT, actor_id=actor, command_id=command)
    assert OutboxEvent.objects.filter(message_id=first.message_id).count() == 2


def test_stale_version_and_worker_cannot_begin_attempt(inputs):
    """No stale optimistic token or wrong fence changes message or history."""
    first = create_message(**inputs)
    task = claim(first)
    with pytest.raises(StaleRecordError):
        change(first, Action.CANCEL_UNSENT, expected_version=2)
    with pytest.raises(IntegrityError, match="current claim"):
        submit(first, task=task, task_fence=task.fence + 1)
    with pytest.raises(IntegrityError, match="current claim"):
        submit(first, task=task, actor_id=uuid4())
    assert OutboxEvent.objects.filter(message_id=first.message_id).count() == 1
    assert submit(first, task=task).attempt == 1


def test_attempt_replay_cannot_change_recorded_deadline_or_fence(inputs):
    """The command fingerprint binds options even after the attempt has advanced."""
    first = create_message(**inputs)
    task, command = claim(first), uuid4()
    submitted = submit(first, task=task, command_id=command)
    assert submit(first, task=task, command_id=command) == submitted
    with pytest.raises(ValueError, match="already bound"):
        submit(first, task=task, command_id=command, provider_seconds=60)
    with pytest.raises(ValueError, match="already bound"):
        submit(first, task=task, command_id=command, task_fence=task.fence + 1)
    recorded = OutboxEvent.objects.get(message_id=first.message_id, command_id=command)
    assert (recorded.provider_deadline - recorded.submitted_at).total_seconds() == 30


def test_unknown_cannot_blindly_retry_and_resolution_retains_old_attempt(inputs):
    """Explicit duplicate-risk authorization preserves the first unknown outcome."""
    status = submit(create_message(**inputs))
    unknown = change(status, Action.MARK_UNKNOWN)
    with pytest.raises(ValueError), transaction.atomic():
        change(
            unknown,
            Action.SUBMIT,
            run_id=status.run_id,
            task_fence=status.task_fence,
            provider_seconds=30,
        )
    with pytest.raises(ValueError):
        change(unknown, Action.CANCEL_UNSENT)
    with pytest.raises(ValueError), work_transaction():
        change(unknown, Action.RETRY_FAILED, render=inputs["render"])
    with pytest.raises(IntegrityError, match="attributed evidence"):
        change(unknown, Action.AUTHORIZE_RESEND)
    evidence = DeliveryEvidence(
        evidence_digest="b" * 64,
        evidence_note="Synthetic Admin acknowledged duplicate delivery risk",
        reason="duplicate_risk_acknowledged",
    )
    pending = change(unknown, Action.AUTHORIZE_RESEND, evidence=evidence)
    task = TaskRun.objects.get(pk=pending.task_id)
    second = change(
        pending,
        Action.SUBMIT,
        run_id=task.pk,
        task_fence=task.fence,
        actor_id=task.worker_id,
        provider_seconds=30,
    )
    assert second.attempt == 2
    first_unknown = OutboxEvent.objects.get(
        message_id=unknown.message_id, action="mark_unknown"
    )
    assert first_unknown.attempt == 1 and first_unknown.state == "delivery_unknown"
    assert (
        OutboxEvent.objects.get(
            message_id=pending.message_id, action="authorize_resend"
        ).evidence_note
        == evidence.evidence_note
    )


def test_failed_delivery_requires_linked_task_retry_and_preserves_terminal_history(
    inputs,
):
    """Explicit failure retry creates a new render without replacing the old one."""
    status = submit(create_message(**inputs))
    failed = change(status, Action.FAIL_UNACCEPTED, evidence=provider_evidence())
    run = TaskRun.objects.get(pk=status.task_id)
    change_run(
        run_id=run.pk,
        expected_version=run.version,
        action="permanent_failure",
        actor_id=run.worker_id,
        fence=run.fence,
        correlation_id=uuid4(),
        admit=permit,
    )
    with (
        pytest.raises(IntegrityError, match="explicit task retry"),
        work_transaction(),
    ):
        change(failed, Action.RETRY_FAILED, render=inputs["render"])
    with pytest.raises(StorageInvariantError, match="lock order"), transaction.atomic():
        change(failed, Action.RETRY_FAILED, render=inputs["render"])
    with work_transaction():
        retry = retry_failed(
            run_id=run.pk,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=permit,
        )
        pending = change(
            failed,
            Action.RETRY_FAILED,
            render=replace(inputs["render"], text="Retry content"),
        )
    assert (
        pending.message_id == status.message_id
        and pending.render_id != status.render_id
    )
    assert OutboxRender.objects.get(pk=status.render_id).text == "Hello"
    assert (
        OutboxEvent.objects.get(
            message_id=status.message_id, action="fail_unaccepted"
        ).state
        == "permanent_failure"
    )
    assert submit(pending, task=claim(pending, run_id=retry.run_id)).attempt == 2


@pytest.mark.parametrize("mutation", ["identity", "unrelated", "history", "delete"])
def test_raw_sql_cannot_rewrite_binding_or_history(inputs, mutation):
    """SQL guards are independent of optimistic ORM helpers and input validators."""
    first = create_message(**inputs)
    message = "unrelated fields" if mutation == "unrelated" else None
    with pytest.raises(IntegrityError, match=message), transaction.atomic():
        if mutation == "identity":
            OutboxMessage.objects.filter(pk=first.message_id).update(
                scope_id=uuid4(), version=F("version") + 1
            )
        elif mutation == "unrelated":
            OutboxMessage.objects.filter(pk=first.message_id).update(
                state="pending",
                action="prepared",
                command_id=uuid4(),
                version=F("version") + 1,
                not_before=Now() + timedelta(seconds=10),
            )
        else:
            with connection.cursor() as cursor:
                if mutation == "history":
                    cursor.execute(
                        "UPDATE stewardship_outbox_event SET reason='forged'"
                    )
                else:
                    cursor.execute("DELETE FROM stewardship_outbox_message")


@pytest.mark.parametrize("action", [Action.RETRY_UNACCEPTED, Action.RETRY_IDEMPOTENT])
def test_retry_schedule_and_attempt_history_use_database_clock(inputs, action):
    """Retry waits are enforced; an uncertain idempotent attempt stays immutable."""
    status = submit(create_message(**inputs))
    waiting = change(status, action, retry_seconds=1, evidence=provider_evidence())
    task = TaskRun.objects.get(pk=status.run_id)
    with pytest.raises(IntegrityError, match="not yet due"):
        change(
            waiting,
            Action.SUBMIT,
            run_id=task.pk,
            task_fence=task.fence,
            actor_id=task.worker_id,
            provider_seconds=30,
        )
    if action is Action.RETRY_IDEMPOTENT:
        from parishkit.stewardship.jobs.outbox_storage import prepare_message

        with pytest.raises(IntegrityError, match="uncertain idempotent"):
            change(waiting, Action.CANCEL_UNSENT)
        with pytest.raises(IntegrityError, match="uncertain idempotent"):
            prepare_message(
                message_id=waiting.message_id,
                expected_version=waiting.version,
                command_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                admit=permit,
                render=replace(inputs["render"], text="Changed"),
            )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    second = change(
        waiting,
        Action.SUBMIT,
        run_id=task.pk,
        task_fence=task.fence,
        actor_id=task.worker_id,
        provider_seconds=30,
    )
    assert second.attempt == 2 and second.render_id == status.render_id
    assert list(
        OutboxEvent.objects.filter(message_id=status.message_id)
        .order_by("version")
        .values_list("attempt", flat=True)
    ) == [0, 1, 1, 2]


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        Action.ACCEPT,
        Action.FAIL_UNACCEPTED,
        Action.RETRY_UNACCEPTED,
        Action.RETRY_IDEMPOTENT,
    ],
)
def test_provider_outcomes_require_explicit_evidence(inputs, unknown, action):
    """Both direct outcomes and reconciliation reject unproven acceptance claims."""
    status = submit(create_message(**inputs))
    if unknown:
        status = change(status, Action.MARK_UNKNOWN)
    options = (
        {"retry_seconds": 1}
        if action in (Action.RETRY_UNACCEPTED, Action.RETRY_IDEMPOTENT)
        else {}
    )
    with pytest.raises(IntegrityError, match="attributed evidence"):
        change(status, action, **options)


@pytest.mark.parametrize(
    "action",
    [
        Action.ACCEPT,
        Action.FAIL_UNACCEPTED,
        Action.RETRY_UNACCEPTED,
        Action.RETRY_IDEMPOTENT,
    ],
)
def test_expired_worker_must_record_uncertainty_before_reconciliation(inputs, action):
    """Losing a task lease cannot authorize acceptance or a fresh provider attempt."""
    first = create_message(**inputs)
    status = submit(first, task=claim(first, lease_seconds=1))
    options = (
        {"retry_seconds": 1}
        if action in (Action.RETRY_UNACCEPTED, Action.RETRY_IDEMPOTENT)
        else {}
    )
    with pytest.raises(IntegrityError, match="current claim"):
        change(
            status, action, actor_id=uuid4(), evidence=provider_evidence(), **options
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    with pytest.raises(IntegrityError, match="current claim"):
        change(status, action, evidence=provider_evidence(), **options)
    unknown = change(status, Action.MARK_UNKNOWN, actor_id=uuid4())
    resolved = change(
        unknown, action, actor_id=uuid4(), evidence=provider_evidence(), **options
    )
    assert resolved.version == unknown.version + 1
