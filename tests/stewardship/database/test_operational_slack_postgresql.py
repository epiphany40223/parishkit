"""Actual independent Slack ownership and certainty, with a fake private provider."""

from threading import Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.key_files import file_fingerprint, write_private
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint, recover_hint
from parishkit.stewardship.jobs.family_mail_delivery_tasks import preparation_attempts
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_slack_models import (
    OperationalSlackAttempt,
    OperationalSlackResult,
)
from parishkit.stewardship.jobs.operational_slack_storage import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.operational_slack_tasks import (
    TASK_TYPE,
    produce_slack,
    slack_handler,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.operational_delivery import OperationalSlack
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_operational_routing_postgresql import configured_routing

pytestmark = pytest.mark.django_db(transaction=True)
KEY = b"synthetic-slack-key"


@pytest.fixture
def slack_setup(tmp_path):
    """One applied channel and an owner-only fake key, never a real integration."""
    store, actor, notice, _ = configured_routing(
        tmp_path, slack_fingerprint=file_fingerprint(KEY)
    )
    path = tmp_path / "slack-key"
    write_private(path, KEY)
    return store, actor, notice, path


def schedule():
    """Opaque notice intake uses only actual metadata scheduler privileges."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_slack(guard)


def consume(identifier, setup, *, stop=None):
    """Exercise the maintained general worker and its installed channel owner."""
    store, _, _, path = setup
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: slack_handler(store, credential_path=path)},
            stop=stop,
        )


def remove_channel(setup, kind):
    """Apply real current configuration, not an independently changed projection."""
    store, actor, _, _ = setup
    row = next(
        row
        for row in store.active().document()["sections"]["integrations"]
        if row["values"]["kind"] == kind
    )
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "remove", "section": "integrations", "id": row["id"]}],
        ).state
        == "applied"
    )


@pytest.mark.parametrize("outcome", list(DeliveryOutcome))
def test_independent_current_slack_preserves_all_provider_outcomes(
    slack_setup, monkeypatch, outcome
):
    """Email absence cannot suppress Slack, and neither Testing routing nor PII leak."""
    remove_channel(slack_setup, "email")
    (identifier,) = schedule()
    assert schedule() == ()
    calls = []
    stop = Event()

    def submit(value, notification, *, seconds, check):
        """Only the external exchange is fake; re-open SQL to prove prior commit."""
        assert not connection.in_atomic_block and value == KEY and 0 < seconds <= 30
        assert isinstance(notification, OperationalSlack)
        stop.set()
        check()
        attempt = OperationalSlackAttempt.objects.get()
        assert attempt.pk == notification.delivery_id
        assert attempt.notice_id == slack_setup[2] and attempt.mode == "testing"
        assert notification.channel_id == "C123"
        assert "[TESTING]" in notification.message()["text"]
        assert "@example.org" not in notification.message()["text"]
        calls.append(notification.delivery_id)
        return outcome

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_slack_tasks.submit_operational_slack",
        submit,
    )
    assert consume(identifier, slack_setup, stop=stop)
    assert OperationalSlackResult.objects.get().outcome == outcome
    assert (
        TaskRun.objects.get(pk=identifier).state
        == {
            DeliveryOutcome.ACCEPTED: "succeeded",
            DeliveryOutcome.NOT_SENT: "retry_wait",
            DeliveryOutcome.UNKNOWN: "failed",
        }[outcome]
    )
    assert not OutboxMessage.objects.exists()
    assert not OperationalLog.objects.filter(level="CRITICAL").exists()
    assert OperationalLog.objects.filter(level__in=("WARNING", "ERROR")).count() == (
        outcome != DeliveryOutcome.ACCEPTED
    )
    assert not consume(identifier, slack_setup)
    assert len(calls) == 1


def claimed(setup):
    """The caller owns the actual Worker role for claim and later SQL effects."""
    store, _, _, path = setup
    return claim_hint(
        TaskRun.objects.get(task_type=TASK_TYPE).pk,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: slack_handler(store, credential_path=path)},
    )


def begin(execution, setup):
    """Start the real guarded provider window without invoking the provider."""
    return begin_submission(
        execution.claim,
        store=setup[0],
        configuration_id=setup[0].active().version_id,
        fingerprint=file_fingerprint(KEY),
    )


def test_sql_rejects_forged_slack_submission_and_results(slack_setup):
    """Direct INSERTs cannot bypass target, Task, actor, mode or outcome ownership."""
    schedule()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claimed(slack_setup)
        values = dict(
            notice_id=slack_setup[2],
            run_id=execution.claim.run_id,
            fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            actor_id=execution.claim.worker_id,
            correlation_id=execution.claim.run_id,
            configuration_id=slack_setup[0].active().version_id,
            channel_id="C123",
            fingerprint=file_fingerprint(KEY),
            mode="testing",
        )
        for field, wrong in (
            ("notice_id", uuid4()),
            ("run_id", uuid4()),
            ("fence", 99),
            ("worker_id", uuid4()),
            ("actor_id", None),
            ("correlation_id", uuid4()),
            ("configuration_id", uuid4()),
            ("channel_id", "C456"),
            ("fingerprint", "a" * 64),
            ("mode", "production"),
        ):
            with pytest.raises(DatabaseError), work_transaction():
                OperationalSlackAttempt.objects.create(**(values | {field: wrong}))
        identifier, deadline, _ = begin(execution, slack_setup)
        with work_transaction():
            assert 33 < (deadline - database_now()).total_seconds() <= 35
        result = dict(
            attempt_id=identifier,
            outcome="accepted",
            reason="provider",
            actor_id=execution.claim.worker_id,
            correlation_id=execution.claim.run_id,
        )
        for invalid in (
            {"actor_id": None},
            {"actor_id": uuid4()},
            {"correlation_id": uuid4()},
            {"attempt_id": uuid4()},
            {"reason": "recovery", "outcome": "delivery_unknown"},
        ):
            with pytest.raises(DatabaseError), work_transaction():
                OperationalSlackResult.objects.create(**(result | invalid))
        finish_submission(identifier, execution.claim, DeliveryOutcome.ACCEPTED)
        with pytest.raises(PermissionError):
            begin(execution, slack_setup)
    # Even the fixture owner cannot rewrite accepted historical evidence.
    for table in ("stewardship_ops_slack_attempt", "stewardship_ops_slack_result"):
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(f"UPDATE {table} SET actor_id=NULL")
    assert OperationalSlackAttempt.objects.count() == 1
    assert OperationalSlackResult.objects.get().outcome == "accepted"


def test_forced_shutdown_waits_for_real_deadline_then_never_resends(
    slack_setup, monkeypatch
):
    """Lost process drainage cannot invent acceptance or overlap its provider."""
    from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure

    from .test_taskrun_postgresql import act, expire

    (identifier,) = schedule()

    def die(*args, **kwargs):
        """Represent the helper's fatal undrained-process signal, not a timeout fact."""
        raise ProviderCheckDrainFailure("Synthetic forced shutdown")

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_slack_tasks.submit_operational_slack",
        die,
    )
    with pytest.raises(ProviderCheckDrainFailure):
        consume(identifier, slack_setup)
    attempt = OperationalSlackAttempt.objects.get()
    running = act(
        _status(TaskRun.objects.get(pk=identifier)), "heartbeat", lease_seconds=1
    )
    expire(running)
    store, _, _, _ = slack_setup

    def recover():
        """Recovery works after the optional key/channel has disappeared."""
        with task_login(ServiceRole.WORKER, exact=True):
            return recover_hint(
                identifier,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: slack_handler(store)},
            )

    assert recover() is False
    assert not OperationalSlackResult.objects.exists()
    remove_channel(slack_setup, "slack")
    with connection.cursor() as cursor:
        # One genuine 35-second provider/drain test; no fake SQL time or bypassed
        # trigger. All other cases share the same session bootstrap without waits.
        cursor.execute(
            "SELECT pg_sleep(GREATEST(0, "
            "EXTRACT(EPOCH FROM (%s-clock_timestamp())))+0.02)",
            [attempt.deadline_at],
        )
    assert recover()
    assert not recover()
    result = OperationalSlackResult.objects.get()
    assert (result.reason, result.outcome) == ("recovery", "delivery_unknown")
    assert TaskRun.objects.get(pk=identifier).state == "failed"
    assert not consume(identifier, slack_setup)


def test_slack_preparation_exhaustion_logs_without_recursive_alerts(
    slack_setup, monkeypatch
):
    """Real failed preparation consumes the bounded budget, without provider IO."""
    from parishkit.stewardship.jobs.operational_models import OperationalNotice

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_slack_tasks.retry_delay", lambda _: 1
    )
    write_private(slack_setup[3], b"different-synthetic-key")
    (identifier,) = schedule()
    for number in range(1, 6):
        assert consume(identifier, slack_setup)
        row = TaskRun.objects.get(pk=identifier)
        assert preparation_attempts(_status(row)) == number
        assert row.state == ("failed" if number == 5 else "retry_wait")
        if number < 5:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1.02)")
    assert not OperationalSlackAttempt.objects.exists()
    assert not OperationalSlackResult.objects.exists()
    assert OperationalNotice.objects.count() == 1
    assert (
        OperationalLog.objects.filter(event="task_failed", level="ERROR").count() == 1
    )
    assert not OperationalLog.objects.filter(level="CRITICAL").exists()


def test_configuration_mismatch_holds_claim_without_spending_attempt(
    slack_setup, monkeypatch
):
    """Temporary authority loss consumes no attempt and cannot abort scheduler scans."""
    store, _, _, path = slack_setup
    (identifier,) = schedule()
    monkeypatch.setattr(store, "manifest_reference", lambda: (uuid4(), "f" * 64))
    for _ in range(7):
        with pytest.raises(PermissionError):
            consume(identifier, slack_setup)
    assert TaskRun.objects.get(pk=identifier).attempt == 0
    assert not OperationalSlackAttempt.objects.exists()
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        owner = slack_handler(store, scheduler=True)
        assert owner.admit("hint", _status(TaskRun.objects.get(pk=identifier))) is False


def test_channel_removed_after_claim_is_a_nonfailure_hold(slack_setup):
    """Removing an optional channel after claim cannot exhaust delivery work."""
    store, _, _, path = slack_setup
    (identifier,) = schedule()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: slack_handler(store, credential_path=path)},
        )
    remove_channel(slack_setup, "slack")
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        maintain_execution(execution),
    ):
        execution.handler.execute(execution)
    row = TaskRun.objects.get(pk=identifier)
    assert (row.state, row.phase) == ("retry_wait", "reconciling")
    assert preparation_attempts(_status(row)) == 0
    assert not OperationalSlackAttempt.objects.exists()


def test_slack_submission_rejects_wrong_fingerprint_and_scheduler_reads(slack_setup):
    """SQL independently binds current target; metadata readers cannot see routing."""
    store, _, _, path = slack_setup
    (identifier,) = schedule()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: slack_handler(store, credential_path=path)},
        )
        with pytest.raises(PermissionError):
            begin_submission(
                execution.claim,
                store=store,
                configuration_id=store.active().version_id,
                fingerprint="a" * 64,
            )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        for column in ("channel_id", "fingerprint"):
            with (
                pytest.raises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(f"SELECT {column} FROM stewardship_ops_slack_attempt")
    assert not OperationalSlackAttempt.objects.exists()
