"""Exact operational MAIL ownership, provider certainty and current authorization."""

from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
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
from .test_operational_fanout_postgresql import consume, schedule
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
family_mail = family_mail_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def allocated(routing):
    """Use the compiled scheduler and preparation worker before attempting MAIL."""
    store, _, _, _ = routing
    (identifier,) = schedule()
    assert consume(identifier, store)
    return list(
        OperationalRecipient.objects.select_related("outbox").order_by("address")
    )


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
    family_mail,
):
    """Actual campaign activation neither deletes nor reroutes operational alerts."""
    from parishkit.stewardship.jobs.operational_content import (
        IncidentKind,
        IncidentLevel,
    )
    from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
    from parishkit.stewardship.jobs.operational_storage import record_observation

    from .response_builders import activate_response_service

    store = family_mail.service.store
    record_observation(
        IncidentKind.STORAGE_INTEGRITY,
        level=IncidentLevel.CRITICAL,
        policy=IncidentPolicy(),
    )
    (identifier,) = schedule()
    consume(identifier, store)
    message = OutboxMessage.objects.get(purpose="operational")
    assert message.mode == "testing"
    activate_response_service(family_mail)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message, store)
        mail, _, _ = begin(message, execution, store)
        assert mail.message()["Subject"].startswith("[PRODUCTION] CRITICAL:")
        assert mail.recipients == ("admin@example.org",)
    message.refresh_from_db()
    assert message.mode == "testing"
    assert message.render.subject.startswith("[PRODUCTION] CRITICAL:")
