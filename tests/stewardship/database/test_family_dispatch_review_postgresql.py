"""Review regressions for real worker admission races and pre-provider evidence."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_delivery_tasks as worker
from parishkit.stewardship.jobs.family_mail_dispatch import (
    TASK_TYPE,
    FamilyDeliveryHeld,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import _status

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import deliver, dispatch_worker  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def no_provider(*args, **kwargs):
    """Any invocation here is a regression; never touch a real mail provider."""
    raise AssertionError("Unexpected private provider call")


def test_resume_between_metadata_checks_cannot_commit_submission(
    dispatch_worker,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    """The no-send caller cannot discard a newly submitting mail tuple."""
    from .test_outbox_boundaries_postgresql import control

    harness, path = dispatch_worker
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    original = worker.begin_submission

    def resumed(*args, **kwargs):
        assert kwargs["metadata_only"] is True
        # The fixture's other actor resumes through the real lifecycle owner.
        # The worker call itself immediately resumes its exact restricted role.
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            control(harness.campaign, "resume")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_mail_dispatch")
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, "begin_submission", resumed)
    monkeypatch.setattr(worker, "submit_family", no_provider)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        control(harness.campaign, "pause")
        deliver(harness, path, message)
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
    task = TaskRun.objects.get(pk=message.task_id)
    assert task.state == "retry_wait"
    assert worker.preparation_attempts(_status(task)) == 0


@pytest.mark.parametrize("fault", ["fingerprint", "configuration", "hold", "deadline"])
def test_pre_provider_failures_remain_definitively_unsent(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    fault,  # noqa: F811
):
    """A known no-launch result must never create delivery_unknown."""
    harness, path = dispatch_worker
    original = worker.begin_submission
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        return no_provider()

    def altered(*args, **kwargs):
        if fault == "configuration":
            kwargs["configuration_id"] = uuid4()
        if fault == "hold":
            raise FamilyDeliveryHeld("Synthetic scope reconciliation hold")
        result = original(*args, **kwargs)
        if fault == "deadline":
            with work_transaction():
                expired = database_now() - timedelta(seconds=1)
            return result[0], expired, *result[2:]
        return result

    monkeypatch.setattr(worker, "begin_submission", altered)
    monkeypatch.setattr(worker, "submit_family", forbidden)
    if fault == "hold":
        monkeypatch.setattr(worker, "MAX_ATTEMPTS", 1)
    if fault == "fingerprint":
        write_private(path, b"different synthetic key")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert not calls
    assert message.state == ("retry_wait" if fault == "deadline" else "pending")
    assert message.attempt == int(fault == "deadline")
    task = TaskRun.objects.get(pk=message.task_id)
    assert task.state == "retry_wait"
    if fault in {"hold", "configuration"}:
        assert worker.preparation_attempts(_status(task)) == 0


def test_systemic_result_stops_new_admission_on_same_handler(
    dispatch_worker,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    """Real delivery sets the process-owned halt, not merely the result row."""
    harness, path = dispatch_worker
    monkeypatch.setattr(
        worker,
        "submit_family",
        lambda *args, **kwargs: FamilyDeliveryResult(Status.SYSTEMIC, 1),
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        owner = deliver(harness, path, message)
    # Isolate the halt decision after the real worker sets it. Binding and
    # disposition have independent real-role coverage; neither may mask a halt.
    monkeypatch.setattr(
        worker, "bound_dispatch", lambda status: SimpleNamespace(state="pending")
    )
    monkeypatch.setattr(worker, "disposition", lambda row: None)
    monkeypatch.setattr(worker, "mail_authority", no_provider)
    assert owner.admit("hint", None) is False
    assert owner.admit("claim", None) is False


def test_pre_submission_crash_recovery_respects_budget(
    dispatch_worker,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    """A restart cannot make repeated crashes bypass the bounded retry policy."""
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.queues import WorkQueue

    from .test_taskrun_postgresql import act, expire

    harness, path = dispatch_worker
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 1)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            claim(message)
        running = act(
            _status(TaskRun.objects.get(pk=message.task_id)),
            "heartbeat",
            lease_seconds=1,
        )
        expire(running)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert recover_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: worker.delivery_handler(None, credential_path=path)
                },
            )
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"


def test_failed_occurrence_update_rolls_back_uncertainty(
    dispatch_worker,  # noqa: F811
    monkeypatch,  # noqa: F811
):
    """An optimistic-version mismatch cannot settle only half of crash recovery."""
    from threading import Event

    from django.db.models import QuerySet

    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.storage import StorageInvariantError

    from .test_taskrun_postgresql import act, expire

    harness, path = dispatch_worker
    original = QuerySet.update

    def lost_version(query, **values):
        if (
            query.model is ScheduleOccurrence
            and values.get("state") == "delivery_unknown"
        ):
            return 0
        return original(query, **values)

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_dispatch.PROVIDER_SECONDS", 1
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            worker.begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin="http://localhost:8000",
            )
        running = act(
            _status(TaskRun.objects.get(pk=message.task_id)),
            "heartbeat",
            lease_seconds=1,
        )
        expire(running)
        Event().wait(0.05)
        monkeypatch.setattr(QuerySet, "update", lost_version)
        with (
            task_login(ServiceRole.MAIL_DISPATCH, exact=True),
            pytest.raises(StorageInvariantError),
        ):
            recover_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: worker.delivery_handler(None, credential_path=path)
                },
            )
    message.refresh_from_db()
    assert message.state == "submitting"
    assert ScheduleOccurrence.objects.get(pk=message.semantic_key).state == "running"


def test_mail_sql_cannot_submit_without_live_dispatch_claim(dispatch_worker):  # noqa: F811
    """A broad table UPDATE grant remains fenced by the exact compiled owner."""
    from django.db import IntegrityError, transaction
    from django.db.models import F

    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    harness, _ = dispatch_worker
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with (
            task_login(ServiceRole.MAIL_DISPATCH, exact=True),
            pytest.raises(IntegrityError),
            transaction.atomic(),
        ):
            OutboxMessage.objects.filter(pk=message.pk).update(
                action="submit", actor_id=uuid4(), version=F("version") + 1
            )
    message.refresh_from_db()
    assert message.state == "pending" and message.attempt == 0
