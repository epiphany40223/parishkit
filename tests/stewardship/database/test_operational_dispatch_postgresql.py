"""Exact operational MAIL ownership, provider certainty and current authorization."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_dispatch import (
    begin_submission,
    bound_operational,
    cohort_state,
    finish_submission,
)
from parishkit.stewardship.jobs.operational_models import (
    OperationalCohort,
    OperationalRecipient,
)
from parishkit.stewardship.jobs.outbox_dispatch import delivery_handler
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.operational_delivery import OperationalMail

from ..policy_factory import address
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail as family_mail_fixture
from .test_family_mail_worker_postgresql import (
    dispatch_worker as dispatch_worker_fixture,
)
from .test_operational_fanout_postgresql import consume, schedule
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
family_mail = family_mail_fixture
dispatch_worker = dispatch_worker_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def allocated(routing):
    """Use the compiled scheduler and preparation worker before attempting MAIL."""
    store, _, _, _ = routing
    (identifier,) = schedule()
    assert consume(identifier, store)
    return list(
        OperationalRecipient.objects.select_related("outbox").order_by("address")
    )


def test_failed_partial_fanout_cancels_children_without_provider_work(
    routing, monkeypatch, tmp_path
):
    """A failed parent cannot strand already-committed operational outbox children."""
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.jobs import operational_fanout
    from parishkit.stewardship.jobs.storage import change_run

    store, _, _, _ = routing
    monkeypatch.setattr(operational_fanout, "BATCH_SIZE", 1)
    (identifier,) = schedule()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                operational_fanout.TASK_TYPE: operational_fanout.fanout_handler(store)
            },
        )
        with work_transaction():
            assert operational_fanout.prepare_page(execution.claim, store) == (1, 2)
            row = TaskRun.objects.get(pk=identifier)
            # Inject a terminal parent via the real fenced journal. The child
            # verifier must independently prove this state, not trust a callback.
            change_run(
                run_id=row.pk,
                expected_version=row.version,
                action="permanent_failure",
                actor_id=row.worker_id,
                correlation_id=row.correlation_id,
                fence=row.fence,
                admit=lambda *_: True,
            )
    message = OutboxMessage.objects.get()
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        assert execute_hint(
            message.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={
                "outbox_delivery": delivery_handler(
                    store, credential_path=tmp_path / "nonexistent-credential"
                )
            },
        )
    message.refresh_from_db()
    assert message.state == "cancelled" and message.reason == "preparation_failed"
    assert message.attempt == 0 and OperationalRecipient.objects.count() == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "cancelled"
    assert (
        OperationalLog.objects.filter(level="ERROR", event="task_failed").count() == 2
    )


def test_removed_email_channel_holds_claims_without_exhaustion(routing):
    """Absent routing may later return; it must not create a failed pending outbox."""
    store, actor, _, _ = routing
    first, second = allocated(routing)
    message = second.outbox
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        running = claim(first.outbox, store)
    email = next(
        row
        for row in store.active().document()["sections"]["integrations"]
        if row["values"]["kind"] == "email"
    )
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "remove", "section": "integrations", "id": email["id"]}],
        ).state
        == "applied"
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        for _ in range(7):
            with pytest.raises(PermissionError):
                claim(message, store)
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        preparation_attempts,
    )
    from parishkit.stewardship.jobs.lifetime import maintain_execution

    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True),
        maintain_execution(running),
    ):
        running.handler.execute(running)
    held = TaskRun.objects.get(pk=first.outbox.task_id)
    assert held.state == "retry_wait" and held.phase == "reconciling"
    assert preparation_attempts(_status(held)) == 0
    first.outbox.refresh_from_db()
    assert first.outbox.attempt == 0 and first.outbox.state == "pending"
    row = TaskRun.objects.get(pk=message.task_id)
    assert row.state == "queued" and row.attempt == 0
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "integrations", **email}],
        ).state
        == "applied"
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert claim(message, store) is not None


@pytest.mark.parametrize(
    "failure", [FamilyDeliveryStatus.SYSTEMIC, FamilyDeliveryStatus.UNAVAILABLE]
)
def test_provider_cooldown_preserves_unclaimed_intents_until_recovery(
    routing, monkeypatch, tmp_path, failure
):
    """The maintained solo consumer cannot spend held recipients' attempt budgets."""
    from parishkit.stewardship.jobs import family_mail_delivery_tasks as circuit_owner

    store, actor, _, _ = routing
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address(f"extra{i}@example.org"),
                }
                for i in range(2)
            ],
        ).state
        == "applied"
    )
    messages = [item.outbox for item in allocated(routing)]
    path = tmp_path / "workspace"
    write_private(path, b"synthetic-workspace")
    clock, outcomes, sent = [100.0], [], []
    monkeypatch.setattr(circuit_owner, "monotonic", lambda: clock[0])

    def submit(candidate, settings, mail, *, seconds, check):
        """Replace only external SMTP; preserve committed attempts and live checks."""
        assert not connection.in_atomic_block
        check()
        sent.append(mail.semantic_key)
        return FamilyDeliveryResult(outcomes.pop(0), 1)

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_mail_tasks.submit_operational_mail",
        submit,
    )
    owner = delivery_handler(store, credential_path=path)

    def deliver(message):
        """Repeated hints share the real process circuit and fresh SQL admission."""
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            return execute_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={"outbox_delivery": owner},
            )

    attempts = 1 if failure is FamilyDeliveryStatus.SYSTEMIC else 3
    for index in range(attempts):
        outcomes.append(failure)
        assert deliver(messages[index])
        if index + 1 < attempts:
            clock[0] += 60
    waiting = messages[-1]
    for _ in range(7):
        with pytest.raises(PermissionError):
            deliver(waiting)
        clock[0] += 5
    waiting.refresh_from_db()
    assert waiting.state == "pending" and waiting.attempt == 0
    assert TaskRun.objects.get(pk=waiting.task_id).attempt == 0
    assert len(sent) == attempts
    # An hour later there is still a pending intent, not a time-based expiration.
    # Real Task/provider clocks are unchanged; only the process cooldown advances.
    clock[0] += 3600
    outcomes.append(FamilyDeliveryStatus.ACCEPTED)
    assert deliver(waiting)
    waiting.refresh_from_db()
    assert waiting.state == "delivered" and waiting.attempt == 1
    assert len(sent) == attempts + 1 and not outcomes


def claim(message, store):
    """Claim through the composite handler, without opening a private provider pipe."""
    from pathlib import Path

    return claim_hint(
        message.task_id,
        queue=WorkQueue.MAIL,
        worker_id=uuid4(),
        handlers={
            "outbox_delivery": delivery_handler(
                store, credential_path=Path("/synthetic/not-read")
            )
        },
    )


def begin(message, execution, store):
    """Current configuration is pinned at the last local pre-provider boundary."""
    return begin_submission(
        message.pk,
        execution.claim,
        store=store,
        configuration_id=SystemConfiguration.objects.get().active_configuration_id,
    )


def test_operational_mail_records_all_outcomes_under_actual_mail_role(routing):
    """One shared cohort covers the six meaningful provider certainty classes."""
    store, actor, _, _ = routing
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address(f"extra{i}@example.org"),
                }
                for i in range(len(FamilyDeliveryStatus) - 2)
            ],
        ).state
        == "applied"
    )
    recipients = allocated(routing)
    expected = {
        "accepted": "delivered",
        "transient": "retry_wait",
        "unavailable": "retry_wait",
        "permanent": "permanent_failure",
        "systemic": "permanent_failure",
        "delivery_unknown": "delivery_unknown",
    }
    for status, recipient in zip(FamilyDeliveryStatus, recipients, strict=True):
        message = recipient.outbox
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            assert (
                bound_operational(_status(TaskRun.objects.get(pk=message.task_id))).pk
                == message.pk
            )
            with CaptureQueriesContext(connection) as queries:
                assert cohort_state(message) == (True, False)
            # One required lock-order proof and one cohort metadata query.
            assert len(queries) == 2
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message, store)
            mail, deadline, attempt = begin(message, execution, store)
            assert isinstance(mail, OperationalMail) and mail.recipients == (
                recipient.address,
            )
            assert attempt == 1 and deadline is not None
            result = finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(status, 1)
            )
            assert result.state.value == expected[status.value]
        message.refresh_from_db()
        assert message.state == expected[status.value]
        assert message.render.subject.startswith("[TESTING] CRITICAL:")
        assert message.sealed_substitutions is None
    assert not OperationalLog.objects.filter(event="mail_provider_failed").exists()


def test_revoked_admin_cancels_without_attempt_and_inflight_result_survives(routing):
    """Current grants control new sends, not already-observed external outcomes."""
    store, actor, _, records = routing
    first, second = allocated(routing)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(first.outbox, store)
        begin(first.outbox, execution, store)
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("replacement@example.org"),
                },
                {
                    "operation": "remove",
                    "section": "login_rules",
                    "id": records[0]["id"],
                },
                {
                    "operation": "remove",
                    "section": "login_rules",
                    "id": records[1]["id"],
                },
            ],
        ).state
        == "applied"
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        other = claim(second.outbox, store)
        assert begin(second.outbox, other, store) is None
        result = finish_submission(
            first.outbox_id,
            execution.claim,
            FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 1),
        )
        assert result.state.value == "delivered"
    second.outbox.refresh_from_db()
    assert second.outbox.state == "cancelled" and second.outbox.attempt == 0


@pytest.mark.parametrize(
    "status", [FamilyDeliveryStatus.ACCEPTED, FamilyDeliveryStatus.SYSTEMIC]
)
def test_maintained_operational_consumer_commits_before_private_transport(
    routing, tmp_path, monkeypatch, status
):
    """Exercise process ownership; replace only the external provider boundary."""
    store, _, _, _ = routing
    recipient = allocated(routing)[0]
    path = tmp_path / "workspace"
    write_private(path, b"synthetic-workspace")
    calls = []

    def submit(candidate, settings, mail, *, seconds, check):
        """Inspect the committed intent while no transaction hides submission."""
        assert not connection.in_atomic_block
        assert candidate == b"synthetic-workspace"
        assert settings["sender"] == mail.sender
        assert 0 < seconds <= 30
        assert isinstance(mail, OperationalMail)
        assert OutboxMessage.objects.get(pk=recipient.outbox_id).state == "submitting"
        check()
        calls.append(mail.semantic_key)
        return FamilyDeliveryResult(status, 1)

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_mail_tasks.submit_operational_mail",
        submit,
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        assert execute_hint(
            recipient.outbox.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={"outbox_delivery": delivery_handler(store, credential_path=path)},
        )
    assert calls == [recipient.pk]
    accepted = status is FamilyDeliveryStatus.ACCEPTED
    assert TaskRun.objects.get(pk=recipient.outbox.task_id).state == (
        "succeeded" if accepted else "failed"
    )
    assert OutboxMessage.objects.get(pk=recipient.outbox_id).state == (
        "delivered" if accepted else "permanent_failure"
    )
    assert OperationalCohort.objects.count() == 1
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.jobs.operational_models import OperationalNotice

    assert OperationalNotice.objects.count() == 1
    assert (
        OperationalLog.objects.filter(level="ERROR", event="task_failed").exists()
        is not accepted
    )


def test_lost_provider_acknowledgement_is_not_retried_or_recursively_notified(
    routing, tmp_path, monkeypatch
):
    """A helper exception after launch is uncertainty, not definitive rejection."""
    from parishkit.stewardship.jobs.operational_models import OperationalNotice

    store, _, _, _ = routing
    recipient = allocated(routing)[0]
    path = tmp_path / "workspace"
    write_private(path, b"synthetic-workspace")
    calls = []

    def interrupted(*args, **kwargs):
        """Simulate an externally ambiguous helper, without opening a socket."""
        calls.append(True)
        raise RuntimeError("synthetic lost acknowledgement")

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_mail_tasks.submit_operational_mail",
        interrupted,
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        handlers = {"outbox_delivery": delivery_handler(store, credential_path=path)}
        assert execute_hint(
            recipient.outbox.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers=handlers,
        )
        assert not execute_hint(
            recipient.outbox.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers=handlers,
        )
    assert calls == [True]
    message = OutboxMessage.objects.get(pk=recipient.outbox_id)
    assert message.state == "delivery_unknown" and message.attempt == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"
    assert OperationalNotice.objects.count() == 1


def test_abandoned_operational_submission_becomes_uncertain(routing, monkeypatch):
    """Use a real short lease and deadline; recovery cannot reopen the provider."""
    from pathlib import Path

    from parishkit.stewardship.jobs.dispatch import recover_hint

    from .test_taskrun_postgresql import act, expire

    store, _, _, _ = routing
    recipient = allocated(routing)[0]
    monkeypatch.setattr(
        "parishkit.stewardship.jobs.operational_dispatch.PROVIDER_SECONDS", 1
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(recipient.outbox, store)
        begin(recipient.outbox, execution, store)
    running = act(
        _status(TaskRun.objects.get(pk=recipient.outbox.task_id)),
        "heartbeat",
        lease_seconds=1,
    )
    expire(running)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert recover_hint(
            recipient.outbox.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={
                "outbox_delivery": delivery_handler(
                    store, credential_path=Path("/unused")
                )
            },
        )
    assert OutboxMessage.objects.get(pk=recipient.outbox_id).state == "delivery_unknown"
    assert TaskRun.objects.get(pk=recipient.outbox.task_id).state == "failed"


def test_allocation_mode_is_historical_but_dispatch_content_uses_current_mode(
    dispatch_worker,
):
    """Actual campaign activation neither deletes nor reroutes operational alerts."""
    from parishkit.stewardship.jobs.operational_content import (
        IncidentKind,
        IncidentLevel,
    )
    from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
    from parishkit.stewardship.jobs.operational_storage import record_observation

    from .response_builders import activate_response_service

    harness, _ = dispatch_worker
    store = harness.service.store
    record_observation(
        IncidentKind.STORAGE_INTEGRITY,
        level=IncidentLevel.CRITICAL,
        policy=IncidentPolicy(),
    )
    (identifier,) = schedule()
    consume(identifier, store)
    message = OutboxMessage.objects.get(purpose="operational")
    assert message.mode == "testing"
    activate_response_service(harness)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        mail, _, _ = begin(message, execution, store)
        assert mail.message()["Subject"].startswith("[PRODUCTION] CRITICAL:")
        assert mail.recipients == ("admin@example.org",)
    message.refresh_from_db()
    assert message.mode == "testing"
    assert message.render.subject.startswith("[PRODUCTION] CRITICAL:")
