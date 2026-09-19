"""Withdrawal shares the real cancellation and boundary serialization protocols."""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

from uuid import uuid4

import pytest
from django.db import transaction
from django.db.models import F

from parishkit.stewardship.accounts import withdrawal_commands as commands
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import ScheduleDefinition
from parishkit.stewardship.campaigns.schedules import create_occurrence
from parishkit.stewardship.campaigns.withdrawal_models import ProductionWithdrawal
from parishkit.stewardship.jobs.delivery_states import DeliveryAction as Action
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    SealedSubstitutions,
    substitution_context,
)
from parishkit.stewardship.storage import StaleRecordError

from ..test_outbox_validation import rendering
from .campaign_builders import admit_test_work, advance, campaign_clock
from .test_outbox_postgresql import change, claim, permit, provider_evidence, submit
from .test_setup_mail_views_postgresql import web_login
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)

pytestmark = pytest.mark.django_db(transaction=True)


def future_message(item):
    """Stage guarded future work using only a domain clock, not disabled guards.

    Ordinary pre-start production creates no occurrences. Move the domain clock
    temporarily into the interval to exercise the defensive retained-work path;
    session and provider lease clocks remain real throughout.
    """
    definition = ScheduleDefinition.objects.select_related("current_revision").get(
        kind="initial"
    )
    family = FamilyCampaign.objects.filter(campaign=item.campaign).first()
    actor = item.arguments[0].portal_session.principal_id
    with campaign_clock(item.campaign.active_configuration.starts_at):
        row = create_occurrence(
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
            mode="production",
            target=f"family:{family.pk}",
            slot="once",
            due_at=definition.current_revision.due_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        identity = DeliveryIdentity(
            scope_id=item.campaign.pk,
            campaign_id=item.campaign.pk,
            family_id=family.pk,
            semantic_key=row.pk,
            mode="production",
            routing="production",
            purpose="initial",
            credential_namespace="production",
        )
        render = rendering(
            configuration_id=item.campaign.active_configuration.configuration_id,
            routed_recipients=("member@example.org",),
        )
        sealed = SealedSubstitutions(
            item.ring.public.encrypt(
                b"synthetic-token", context=substitution_context(identity, render)
            ),
            token_generation_id=item.confirmation.generation_id,
            credential_epoch_id=item.confirmation.generation.credential_epoch,
        )
        message = create_message(
            identity=identity,
            render=render,
            sealed=sealed,
            actor_id=actor,
            command_id=uuid4(),
            correlation_id=uuid4(),
            admit=permit,
        )
        type(row).objects.filter(pk=row.pk).update(
            outbox_id=message.message_id, version=F("version") + 1
        )
    return row, message


def fail_retained(row, message):
    """Retain a genuinely failed task, occurrence and proven-unaccepted message."""
    from .test_taskrun_postgresql import act

    with campaign_clock(row.due_at):
        task = claim(message)
        row = advance(
            row, task.worker_id, "running", task_id=task.run_id, fence=task.fence
        )
        message = change(
            submit(message, task=task),
            Action.FAIL_UNACCEPTED,
            evidence=provider_evidence(),
        )
        advance(row, task.worker_id, "failed", fence=task.fence)
        act(task, "permanent_failure")
    return message


def test_uncertain_work_blocks_then_proven_unsent_work_cancels_atomically(scheduled):
    """Never equate retry_wait with nonacceptance; retain all delivery evidence."""
    item = scheduled
    with web_login():
        _, empty = commands.preview(
            *item.arguments, reason="Fix schedule", acknowledged=True
        )
    occurrence, message = future_message(item)
    with web_login():
        with pytest.raises(StaleRecordError):
            commands.withdraw(*item.arguments, token=empty)
        binding, ready = commands.preview(
            *item.arguments, reason="Fix schedule", acknowledged=True
        )
        assert (
            binding["inventory"]["cancellable"] == binding["inventory"]["messages"] == 1
        )
        assert ready
    with campaign_clock(item.campaign.active_configuration.starts_at):
        message = submit(message)
    with web_login():
        with pytest.raises(StaleRecordError):
            commands.withdraw(*item.arguments, token=ready)
        blocked, token = commands.preview(
            *item.arguments, reason="Fix schedule", acknowledged=True
        )
        assert blocked["inventory"]["blocking"] and token is None
    message = change(message, Action.MARK_UNKNOWN)
    with web_login():
        blocked, token = commands.preview(
            *item.arguments, reason="Fix schedule", acknowledged=True
        )
        assert blocked["inventory"]["blocking"] and token is None
    with transaction.atomic():
        change(
            message,
            Action.RETRY_IDEMPOTENT,
            retry_seconds=30,
            evidence=provider_evidence(),
        )
        # This branch probes the shared read projection under the fixture owner;
        # all actual withdrawal writes below still use the restricted web login.
        blocked, token = commands.preview(
            *item.arguments, reason="Fix schedule", acknowledged=True
        )
        assert blocked["inventory"]["blocking"] and token is None
        transaction.set_rollback(True)
    # A verified negative result permits cancellation, unlike an uncertain retry.
    message = change(
        message, Action.RETRY_UNACCEPTED, retry_seconds=30, evidence=provider_evidence()
    )
    with web_login():
        binding, token = commands.preview(
            *item.arguments, reason="Fix schedule", acknowledged=True
        )
        assert not binding["inventory"]["blocking"]
        commands.withdraw(*item.arguments, token=token)
    occurrence.refresh_from_db()
    retained = OutboxMessage.objects.get(pk=message.message_id)
    assert occurrence.state == "skipped" and occurrence.reason == "production_withdrawn"
    assert retained.state == "cancelled" and retained.sealed_substitutions is None
    assert retained.events.filter(action="mark_unknown").exists()
    assert retained.events.filter(action="retry_unaccepted").exists()
    assert retained.renders.count() == 1
    assert ProductionWithdrawal.objects.count() == 1


def test_waiting_withdrawal_rechecks_start_and_boundary_winner(scheduled):
    """Observe a real competing SQL wait, then execute the actual start owner."""
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue

    from django.contrib.sessions.backends.db import SessionStore
    from django.db import connection
    from django.test import RequestFactory

    from parishkit.stewardship.campaigns.boundary_production import produce_boundaries
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.jobs.scheduler import scheduler_session

    from .test_activation_sql_races_postgresql import wait_for_work_lock
    from .test_background_grants_postgresql import task_login
    from .test_boundary_tasks_postgresql import run

    item = scheduled
    login, service, campaign_id = item.arguments
    with web_login():
        _, token = commands.preview(
            *item.arguments, reason="Before start", acknowledged=True
        )
    started = Queue()

    def competing():
        """A separate restricted session reloads the original browser cookie."""
        connection.close()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
                cursor.execute("SET statement_timeout='5s'")
                cursor.execute("SELECT pg_backend_pid()")
                started.put(cursor.fetchone()[0])
            request = RequestFactory().post(item.path)
            request.session = SessionStore(login.session.session_key)
            with pytest.raises(StaleRecordError):
                commands.withdraw(request, service, campaign_id, token=token)
            return "rejected"
        finally:
            connection.close()

    with web_login():
        # Keep the actual web login provisioned for the competing connection;
        # only this fixture connection changes the synthetic domain clock.
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        with campaign_clock(item.campaign.active_configuration.starts_at):
            with ThreadPoolExecutor(max_workers=1) as pool:
                with work_transaction():
                    result = pool.submit(competing)
                    wait_for_work_lock(started.get(timeout=3))
                assert result.result(timeout=5) == "rejected"
            assert not ProductionWithdrawal.objects.exists()
            with (
                task_login(ServiceRole.SCHEDULER, exact=True, reconnect=True),
                scheduler_session() as guard,
            ):
                (start,) = produce_boundaries(guard)
            with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
                assert run(start)
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
            with pytest.raises(StaleRecordError):
                commands.withdraw(*item.arguments, token=token)
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
    item.campaign.refresh_from_db()
    assert item.campaign.state == "active" and item.campaign.ever_active
    assert not ProductionWithdrawal.objects.exists()
