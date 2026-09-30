"""Two mail consumer processes never send one Family message twice.

Mail dispatch runs two consumers on the same queues, each with its own
batched helper and SMTP connection (runtime_process.MailConsumer). The
scheduler's hints may reach both for the same Task. Each message is exactly
one Task, and its claim (a row lock, a state check and a fence) admits one
executor; these tests race two real handlers, each with its own session,
against the fake Gmail helper on PostgreSQL.
"""

import threading
from uuid import uuid4

import pytest
from django.db import connections

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import family_mail_delivery_tasks as tasks
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.outbox_models import OutboxEvent, OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue

from ..family_mail_session_fakes import FakeGmailHelpers
from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import (  # noqa: F401
    dispatch_worker,
    family_owner,
)

pytestmark = pytest.mark.django_db(transaction=True)


def consumers(harness, path, gmail):
    """Two installed MAIL handlers, as two consumer processes hold them."""
    owners = []
    for _ in range(2):
        owner = family_owner(harness, path)
        owner.execute.keywords["session"].spawn = gmail.spawn
        owners.append(owner)
    return owners


def hint(owner, message):
    """One consumer takes a hint: claim the Task, then run it if claimed."""
    try:
        execution = claim_hint(
            message.task_id,
            queue=WorkQueue.MAIL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: owner},
        )
        if execution is None:
            return False
        with maintain_execution(execution):
            owner.execute(execution)
        return True
    finally:
        connections.close_all()


def sent_once(gmail, message):
    """Exactly one DATA at Gmail and one settled provider outcome."""
    message.refresh_from_db()
    assert message.state == "delivered"
    assert gmail.data(message) == 1
    assert (
        OutboxEvent.objects.filter(message=message, previous_state="submitting").count()
        == 1
    )


def test_a_hint_taken_by_both_consumers_at_once_sends_once(dispatch_worker):  # noqa: F811
    """Both consumers race the same hint; one runs it, the other claims nothing."""
    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(path.parent / "gmail")
    owners = consumers(harness, path, gmail)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        start, results, errors = threading.Barrier(2), [], []

        def race(owner):
            """Start together, as two processes woken by one hint each."""
            try:
                start.wait(10)
                results.append(hint(owner, message))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            threads = [threading.Thread(target=race, args=(o,)) for o in owners]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
    for owner in owners:
        owner.execute.keywords["session"].close()
    assert errors == []
    assert sorted(results) == [False, True]
    sent_once(gmail, message)


def test_a_second_consumer_cannot_take_a_message_already_in_flight(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Held after its claim, one consumer blocks nothing but a second claim.

    The first consumer is stopped inside preparation, after its claim; the
    second then takes the same hint and must claim nothing. Once the first
    finishes, a late duplicate hint claims nothing either.
    """
    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(path.parent / "gmail")
    first, second = consumers(harness, path, gmail)
    claimed, release = threading.Event(), threading.Event()
    real = tasks.begin_submission

    def held(*args, **kwargs):
        """Pause the first consumer between its claim and its submission."""
        claimed.set()
        assert release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(tasks, "begin_submission", held)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        outcome, errors = {}, []

        def run_first():
            """The consumer that wins the claim."""
            try:
                outcome["first"] = hint(first, message)
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            thread = threading.Thread(target=run_first)
            thread.start()
            assert claimed.wait(30)
            # The first consumer holds a live claim: the second gets nothing.
            outcome["second"] = hint(second, message)
            release.set()
            thread.join(60)
            outcome["late"] = hint(second, message)
    for owner in (first, second):
        owner.execute.keywords["session"].close()
    assert errors == []
    assert outcome == {"first": True, "second": False, "late": False}
    sent_once(gmail, message)
    assert OutboxMessage.objects.get(pk=message.pk).attempt == 1


def test_both_consumers_really_claim_the_same_hint_at_once(dispatch_worker):  # noqa: F811
    """#370 L7: the claims overlap for certain, and still only one runs.

    Each consumer is held at its claim's first lock, after it has read the
    Task as still queued and chosen its handler, until the other has too.
    Neither can arrive after the other has finished; from there the
    deployment-wide work-order lock and the row lock admit one claim, and
    the other finds the Task already running.
    """
    from contextlib import contextmanager
    from dataclasses import replace

    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(path.parent / "gmail")
    together = threading.Barrier(2)
    gated = threading.local()

    def gate(scope):
        """The handler's own scope, entered once both claims are at its door."""

        @contextmanager
        def entered():
            if not getattr(gated, "passed", False):
                gated.passed = True
                together.wait(10)
            with scope():
                yield

        return entered

    owners = [
        replace(owner, scope=gate(owner.scope))
        for owner in consumers(harness, path, gmail)
    ]
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        results, errors = [], []

        def race(owner):
            """One consumer taking the hint."""
            try:
                results.append(hint(owner, message))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            threads = [threading.Thread(target=race, args=(o,)) for o in owners]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
    for owner in owners:
        owner.execute.keywords["session"].close()
    assert errors == []
    # Both claims reached the lock together: the barrier was passed.
    assert not together.broken
    assert sorted(results) == [False, True]
    sent_once(gmail, message)


class WorkerCrash(BaseException):
    """One mail consumer process dying in the middle of a message."""


def test_the_other_consumer_recovers_a_dead_consumers_message_as_unknown(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """#370 L7: a consumer dies inside DATA; the other never resends it.

    Before the message's provider deadline the other consumer's recovery
    leaves it alone; after it, the message becomes delivery unknown with no
    second DATA. The dead consumer's late outcome is refused by its fence.
    """
    from parishkit.stewardship.family_delivery import FamilyDeliveryResult
    from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
    from parishkit.stewardship.jobs import family_mail_dispatch
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.ownership import TaskOwnershipLost
    from parishkit.stewardship.jobs.storage import _status

    from .test_family_mail_session_postgresql import wait_past
    from .test_taskrun_postgresql import act, expire

    monkeypatch.setattr(family_mail_dispatch, "PROVIDER_SECONDS", 6)
    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(path.parent / "gmail")
    dying, survivor = consumers(harness, path, gmail)
    claims = []
    real_check = tasks._check

    def crashing(execution):
        """Die as soon as the helper is inside DATA."""
        if gmail.count("hang"):
            claims.append(execution.claim)
            raise WorkerCrash()
        real_check(execution)

    def recover(message):
        """The surviving consumer takes a recovery hint for the message."""
        try:
            return recover_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: survivor},
            )
        finally:
            connections.close_all()

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        gmail.script(("data", ["hang"]))
        monkeypatch.setattr(tasks, "_check", crashing)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            with pytest.raises(WorkerCrash):
                hint(dying, message)
            monkeypatch.setattr(tasks, "_check", real_check)
            message.refresh_from_db()
            assert message.state == "submitting"
            dying.execute.keywords["session"].close()
            # The dead consumer's lease runs out; its Task is abandoned.
            running = act(
                _status(TaskRun.objects.get(pk=message.task_id)),
                "heartbeat",
                lease_seconds=1,
            )
            expire(running)
            # Inside the provider deadline nothing is decided or resent.
            recover(message)
            message.refresh_from_db()
            assert message.state == "submitting"
            wait_past(message.provider_deadline)
            assert recover(message)
            message.refresh_from_db()
            assert message.state == "delivery_unknown"
            # The dead consumer's outcome, arriving late, is fenced out.
            # lock_task_claim refuses the dead consumer's stale fence.
            with pytest.raises(TaskOwnershipLost):
                finish_submission(
                    message.pk, claims[0], FamilyDeliveryResult(Status.ACCEPTED, 1)
                )
            connections.close_all()
    survivor.execute.keywords["session"].close()
    message.refresh_from_db()
    assert message.state == "delivery_unknown"
    assert TaskRun.objects.get(pk=message.task_id).state == "failed"
    assert gmail.data(message) == 1


@pytest.mark.parametrize("stop", ["other_consumer", "outage"])
def test_a_stop_between_claim_and_send_holds_the_message_until_restart(
    dispatch_worker,  # noqa: F811
    tmp_path,
    stop,
):
    """A stop landing after a real claim holds the message; a restart sends it.

    Either the other consumer's SYSTEMIC marker or this consumer's own
    outage pause arrives between the claim and its effect. The claimed
    message is deferred without spending a preparation attempt and never
    reaches the provider; after a restart (marker gone, fresh handler) it is
    sent exactly once.
    """
    from parishkit.stewardship.family_delivery import ProviderHealth
    from parishkit.stewardship.installer_health import mark_stopped
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_family_mail_session_postgresql import wait_past

    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(path.parent / "gmail")
    marker = tmp_path / "private" / "mail-systemic-stop"

    def handler():
        """One mail consumer's handler, sharing the container's stop marker."""
        owner = tasks.delivery_handler(
            harness.service.store,
            private=harness.rings.private,
            public_origin="http://localhost:8000",
            credential_path=path,
            shared_stop=marker,
        )
        owner.execute.keywords["session"].spawn = gmail.spawn
        return owner

    owner = handler()
    circuit = owner.execute.keywords["circuit"]
    real_count = circuit.daily_sends

    def stopping():
        """The stop lands after the claim: daily_sends runs just before effect."""
        if stop == "other_consumer":
            mark_stopped(marker)
        else:
            for _ in range(3):
                circuit.observe(ProviderHealth.UNAVAILABLE)
        return real_count()

    circuit.daily_sends = stopping
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            assert hint(owner, message) is True
            run = TaskRun.objects.get(pk=message.task_id)
            assert run.state == "retry_wait"
            assert tasks.preparation_attempts(_status(run)) == 0
            message.refresh_from_db()
            assert message.state == "pending" and message.attempt == 0
            assert gmail.data(message) == 0
            assert not OutboxEvent.objects.filter(
                message=message, previous_state="submitting"
            ).exists()
            owner.execute.keywords["session"].close()
            # Restart: the main process clears the marker, and every process
            # starts with a fresh circuit.
            marker.unlink(missing_ok=True)
            restarted = handler()
            wait_past(run.not_before)
            assert hint(restarted, message) is True
            restarted.execute.keywords["session"].close()
    sent_once(gmail, message)
    assert OutboxMessage.objects.get(pk=message.pk).attempt == 1
    assert TaskRun.objects.get(pk=message.task_id).state == "succeeded"
