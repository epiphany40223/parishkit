"""Real recovery and authorization for provider-free Family preparation."""

from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import F

from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Execution, recover_hint
from parishkit.stewardship.jobs.family_mail_tasks import (
    TASK_TYPE,
    retry_preparation,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import (
    allocate,
    claim,
    family_mail,  # noqa: F401
    handler,
)
from .test_policy_postgresql import user
from .test_runtime_auth_grants_postgresql import web_login
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


def recover(ticket, owner):
    """Exercise the compiled recovery callback and journal admission together."""
    return recover_hint(
        ticket.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: owner},
    )


@pytest.mark.parametrize("committed", [False, True])
def test_abandoned_preparation_recovers_exact_local_outcome(
    family_mail,  # noqa: F811
    monkeypatch,
    committed,
):
    """Absent a receipt retry; with a receipt complete without preparing twice."""
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        if committed:

            def crash(*args, **kwargs):
                """Lose only the acknowledgment after the preparation transaction."""
                raise RuntimeError("synthetic lost acknowledgment")

            with task_login(ServiceRole.WORKER, exact=True):
                execution = claim(ticket, owner)
                with monkeypatch.context() as patch:
                    patch.setattr(Execution, "transition", crash)
                    with pytest.raises(RuntimeError, match="lost acknowledgment"):
                        owner.execute(execution)
            running = _status(TaskRun.objects.get(pk=ticket.task_id))
            running = act(running, "heartbeat", lease_seconds=1)
        else:
            running = act(
                _status(TaskRun.objects.get(pk=ticket.task_id)),
                "claim",
                lease_seconds=1,
            )
        expire(running)
        with task_login(ServiceRole.WORKER, exact=True):
            assert recover(ticket, owner)
        task = TaskRun.objects.get(pk=ticket.task_id)
        assert task.state == ("succeeded" if committed else "retry_wait")
        assert OutboxMessage.objects.count() == int(committed)


def test_dirty_source_holds_claim_and_abandoned_recovery(family_mail):  # noqa: F811
    """Transient population work does not consume the bounded render retry budget."""
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        CampaignCredentialState.objects.update(
            population_dirty=True, version=F("version") + 1
        )
        with (
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(PermissionError, match="source reconciliation"),
        ):
            claim(ticket, owner)
        assert TaskRun.objects.get(pk=ticket.task_id).attempt == 0
        CampaignCredentialState.objects.update(
            population_dirty=False, version=F("version") + 1
        )
        running = act(
            _status(TaskRun.objects.get(pk=ticket.task_id)), "claim", lease_seconds=1
        )
        expire(running)
        CampaignCredentialState.objects.update(
            population_dirty=True, version=F("version") + 1
        )
        with task_login(ServiceRole.WORKER, exact=True):
            assert not recover(ticket, owner)
        assert TaskRun.objects.get(pk=ticket.task_id).state == "abandoned"


def test_abandoned_old_epoch_is_cancelled(family_mail):  # noqa: F811
    """An invalidated rehearsal proves cancellation, never retry to today's epoch."""
    from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        expire(
            act(
                _status(TaskRun.objects.get(pk=ticket.task_id)),
                "claim",
                lease_seconds=1,
            )
        )
        invalidate_rehearsal(campaign_id=family_mail.campaign.pk, admit=lambda _: True)
        with task_login(ServiceRole.WORKER, exact=True):
            assert recover(ticket, owner)
        assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"


def test_exhausted_preparation_can_be_retried_only_by_current_admin(family_mail):  # noqa: F811
    """A corrected failure resumes the original root without creating another ticket."""
    admin, outsider = user("admin@example.org"), user("outsider@example.net")
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        task = _status(TaskRun.objects.get(pk=ticket.task_id))
        for attempt in range(5):
            task = act(task, "claim", lease_seconds=1 if attempt == 4 else 60)
            if attempt < 4:
                task = act(task, "retryable_failure")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_sleep(1.05)")
        expire(task)
        with task_login(ServiceRole.WORKER, exact=True):
            assert recover(ticket, owner)
        assert TaskRun.objects.get(pk=ticket.task_id).state == "failed"
        command = uuid4()
        with web_login():
            with pytest.raises(PermissionError, match="requires an Administrator"):
                retry_preparation(
                    family_mail.service.store,
                    outsider.pk,
                    ticket.pk,
                    command_id=command,
                )
            retry = retry_preparation(
                family_mail.service.store, admin.pk, ticket.pk, command_id=command
            )
            assert (
                retry_preparation(
                    family_mail.service.store, admin.pk, ticket.pk, command_id=command
                ).run_id
                == retry.run_id
            )
        assert retry.root_id == ticket.task_id and retry.run_id != ticket.task_id

        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim(replace_ticket_task(ticket, retry.run_id), owner)
            owner.execute(execution)
        assert OutboxMessage.objects.count() == 1
        with web_login():
            replay = retry_preparation(
                family_mail.service.store, admin.pk, ticket.pk, command_id=command
            )
        assert replay.run_id == retry.run_id and replay.state == "succeeded"


def test_source_removed_family_drains_as_skipped(family_mail):  # noqa: F811
    """An absent source row does not strand an admitted Family metadata ticket."""
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence

    from .response_builders import response_source
    from .test_recipient_suppressions_postgresql import refresh

    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(family_mail)
        data = response_source()
        del data.families[1]
        data.members.clear()
        data.member_contactinfos.clear()
        data.ministry_type_memberships.clear()
        refresh(family_mail, data)
        with task_login(ServiceRole.WORKER, exact=True):
            owner.execute(claim(ticket, owner))
        row = ScheduleOccurrence.objects.get(pk=ticket.occurrence_id)
        assert row.state == "skipped" and row.reason == "family_ineligible"
        assert TaskRun.objects.get(pk=ticket.task_id).state == "cancelled"
        assert not OutboxMessage.objects.exists()


def replace_ticket_task(ticket, task_id):
    """Change only a transport hint object, never the immutable database ticket."""
    from types import SimpleNamespace

    return SimpleNamespace(task_id=task_id)


def test_production_pause_holds_preparation(family_mail):  # noqa: F811
    """Current durable pause denies claims rather than cancelling pending mail."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.controls import change_control

    from .campaign_builders import admit_test_work

    harness = activate_response_service(family_mail)
    complete_empty_catchup(harness.campaign, uuid4())
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        ticket, owner = allocate(), handler(harness)
        harness.campaign.refresh_from_db()
        change_control(
            campaign_id=harness.campaign.pk,
            request_id=uuid4(),
            action="pause",
            expected_version=harness.campaign.version,
            expected_runtime_version=SystemConfiguration.objects.get().version,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=admit_test_work,
            reason="reviewed",
        )
        with (
            task_login(ServiceRole.WORKER, exact=True),
            work_transaction(),
            pytest.raises(PermissionError, match="paused"),
        ):
            claim(ticket, owner)
        assert TaskRun.objects.get(pk=ticket.task_id).state == "queued"
