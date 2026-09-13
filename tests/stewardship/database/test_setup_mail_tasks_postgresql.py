"""Maintained mail Task execution, with fake provider IO and actual journal SQL."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts import setup_mail_tasks as tasks
from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_mail import recover_pending
from parishkit.stewardship.accounts.setup_mail_handoff import WorkspaceCandidate
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.provider_checks import ProviderCheckOwnershipLost
from parishkit.stewardship.readiness_delivery import DeliveryOutcome

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_handoff_discovery_postgresql import key
from .test_setup_exchange_postgresql import target_login
from .test_setup_mail_postgresql import (
    age_delivery,
    claim,
    prepared,
)
from .test_setup_staging_postgresql import setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def execute(delivery):
    """Use the real dispatcher and independently maintained Task lifetime."""
    return execute_hint(
        delivery.task_id,
        queue=WorkQueue.MAIL,
        worker_id=uuid4(),
        handlers={tasks.TASK_TYPE: tasks.setup_mail_handler()},
    )


@pytest.mark.parametrize(
    "outcome", [*DeliveryOutcome, RuntimeError("private-provider-error")]
)
def test_dispatcher_commits_before_one_fake_submission_and_records_outcome(
    setup_service, monkeypatch, tmp_path, outcome
):
    """The provider boundary sees no SQL transaction and an already committed marker."""
    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    receiver = Mock(return_value=WorkspaceCandidate(b"synthetic-private-workspace"))
    monkeypatch.setattr(tasks, "_receive", receiver)
    calls = []

    def provider(candidate, settings, mail, *, seconds, check):
        """Verify production ownership checks while keeping all provider IO offline."""
        assert not connection.in_atomic_block
        assert candidate == b"synthetic-private-workspace"
        assert settings["recipient"] == mail.recipient
        assert SetupMailDelivery.objects.get().state == "submitting"
        assert 0 < seconds <= 30
        check()
        calls.append(mail.delivery_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(tasks, "submit_sample", provider)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        assert execute(delivery)
        assert not execute(delivery)
    expected = (
        outcome.value if isinstance(outcome, DeliveryOutcome) else "delivery_unknown"
    )
    assert SetupMailDelivery.objects.get().state == expected
    assert TaskRun.objects.get(pk=delivery.task_id).state == (
        "succeeded" if outcome is DeliveryOutcome.ACCEPTED else "failed"
    )
    assert calls == [delivery.identifier] and receiver.call_count == 1


def test_failed_private_relay_is_terminal_unsent_and_does_not_block_new_test(
    setup_service, monkeypatch, tmp_path
):
    """A target failure before the marker never fabricates a submitted outcome."""
    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    monkeypatch.setattr(tasks, "_receive", Mock(side_effect=TimeoutError("private")))
    provider = Mock()
    monkeypatch.setattr(tasks, "submit_sample", provider)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert execute(delivery)
    provider.assert_not_called()
    assert TaskRun.objects.get(pk=delivery.task_id).state == "failed"
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert recover_pending() == 1
        assert recover_pending() == 0
    row = SetupMailDelivery.objects.get()
    assert row.state == "cancelled" and row.submitted_at is None


def test_graceful_stop_drains_started_send_without_allowing_another(
    setup_service, monkeypatch, tmp_path
):
    """A process stop during the provider call preserves its definitive acceptance."""
    from threading import Event

    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    stop = Event()
    monkeypatch.setattr(
        tasks, "_receive", Mock(return_value=WorkspaceCandidate(b"fixture"))
    )

    def provider(candidate, settings, mail, *, seconds, check):
        """The irreversible marker already exists when shutdown begins."""
        assert SetupMailDelivery.objects.get().state == "submitting"
        stop.set()
        check()
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(tasks, "submit_sample", provider)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        assert execute_hint(
            delivery.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={tasks.TASK_TYPE: tasks.setup_mail_handler()},
            stop=stop,
        )
    assert SetupMailDelivery.objects.get().state == "accepted"
    assert TaskRun.objects.get(pk=delivery.task_id).state == "succeeded"


def test_cancel_while_waiting_for_private_relay_settles_task_without_submission(
    setup_service, monkeypatch, tmp_path
):
    """A cancellation between credential polls is not a permanently failed send."""
    from parishkit.stewardship.accounts.setup_staging import cancel_setup

    from .test_runtime_auth_grants_postgresql import web_login

    request, attempt, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    publish = tasks.publish_recipient

    def cancel_after_publish(recipient):
        """Commit original-browser cancellation before the first receive poll."""
        result = publish(recipient)
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            with web_login():
                cancel_setup(request, setup_service, attempt.attempt_id)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_mail_dispatch")
        return result

    monkeypatch.setattr(tasks, "publish_recipient", cancel_after_publish)
    provider, receiver = Mock(), Mock()
    monkeypatch.setattr(tasks, "submit_sample", provider)
    monkeypatch.setattr(tasks, "receive_credential", receiver)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        assert execute(delivery)
    assert TaskRun.objects.get(pk=delivery.task_id).state == "cancelled"
    assert SetupMailDelivery.objects.get().submitted_at is None
    provider.assert_not_called()
    receiver.assert_not_called()


def test_lost_owner_cannot_claim_success_or_trigger_automatic_resubmission(
    setup_service, monkeypatch, tmp_path
):
    """Fatal IPC ownership loss leaves the irreversible marker for fenced recovery."""
    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    monkeypatch.setattr(
        tasks, "_receive", Mock(return_value=WorkspaceCandidate(b"fixture"))
    )
    provider = Mock(side_effect=ProviderCheckOwnershipLost("Ownership lost."))
    monkeypatch.setattr(tasks, "submit_sample", provider)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        with pytest.raises(ProviderCheckOwnershipLost):
            execute(delivery)
        assert not execute(delivery)
    assert provider.call_count == 1
    assert SetupMailDelivery.objects.get().state == "submitting"
    assert TaskRun.objects.get(pk=delivery.task_id).state == "running"


def test_real_ephemeral_relay_reaches_one_maintained_provider_boundary(
    setup_service, monkeypatch, tmp_path
):
    """Only role provisioning and external SMTP are substituted in this composition."""
    from parishkit.stewardship.accounts.setup_mail_exchange import relay_pending

    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    publish = tasks.publish_recipient

    def target_reply(recipient):
        """Model the separately running target using its real restricted SQL role."""
        identifier = publish(recipient)
        # Only the disposable schema owner provisions the second role. Every
        # application operation below still executes as its exact runtime owner.
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            with target_login("google_workspace"):
                assert relay_pending(key("google_workspace", material=b"g" * 32))
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_mail_dispatch")
        return identifier

    provider = Mock(return_value=DeliveryOutcome.ACCEPTED)
    monkeypatch.setattr(tasks, "publish_recipient", target_reply)
    monkeypatch.setattr(tasks, "submit_sample", provider)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        assert execute(delivery)
    provider.assert_called_once()
    from ..test_integration_candidates import account

    assert provider.call_args.args[0] == account()
    assert SetupMailDelivery.objects.get().state == "accepted"


@pytest.mark.parametrize("outcome", [None, *DeliveryOutcome])
def test_abandoned_task_uses_journal_evidence_and_never_retries(
    setup_service, monkeypatch, tmp_path, outcome
):
    """A dead claim becomes completed or failed from evidence, never a new send."""
    from parishkit.stewardship.accounts.setup_mail import (
        begin_submission,
        finish_submission,
    )

    _, _, _, _, delivery = prepared(setup_service, monkeypatch, tmp_path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(delivery)
        if outcome is not None:
            begin_submission(delivery.identifier, owner)
            finish_submission(delivery.identifier, owner, outcome)
    age_delivery(delivery.identifier, task=True)
    provider = Mock()
    monkeypatch.setattr(tasks, "submit_sample", provider)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert recover_hint(
            delivery.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={tasks.TASK_TYPE: tasks.setup_mail_handler()},
        )
    assert TaskRun.objects.get(pk=delivery.task_id).state == (
        "succeeded" if outcome is DeliveryOutcome.ACCEPTED else "failed"
    )
    assert TaskRun.objects.filter(task_type=tasks.TASK_TYPE).count() == 1
    provider.assert_not_called()
