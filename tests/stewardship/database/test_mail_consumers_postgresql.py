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
