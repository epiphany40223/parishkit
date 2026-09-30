"""A batch of Family mail through one long-lived helper keeps exact state (#284).

Each test runs the real MAIL handler, Task leases, commit-before-send and
settlement, with the handler's batched session talking to real helper
processes and a scripted fake Gmail (tests/stewardship/fake_gmail_helper.py).
Other Families' messages go through the same session before and after, so
the database message is always in the middle of a batch.
"""

import json
import time
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.family_delivery_process import submit_family
from parishkit.stewardship.jobs import family_mail_delivery_tasks as tasks
from parishkit.stewardship.jobs import family_mail_dispatch
from parishkit.stewardship.jobs.dispatch import recover_hint
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxEvent, OutboxMessage
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.observability import task_scope

from ..family_mail_session_fakes import LIMIT, FakeGmailHelpers
from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import (  # noqa: F401
    KEY,
    deliver,
    dispatch_worker,
    family_owner,
)
from .test_mail_daily_limit_postgresql import last_retry
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)
TASKS = "parishkit.stewardship.jobs.family_mail_delivery_tasks"
# The settings the installed worker passes for this fixture's messages; the
# first test asserts it, so other Families' mail shares the same helper.
SETTINGS = {
    "delegated_email": "sender@example.org",
    "sender": "sender@example.org",
    "reply_to": "reply@example.org",
    "sender_name": "Example Parish",
}


class WorkerCrash(BaseException):
    """The mail worker process dying in the middle of one message."""


@pytest.fixture
def batch(dispatch_worker, tmp_path, monkeypatch):  # noqa: F811
    """The installed handler, its batched session wired to the fake Gmail."""
    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(tmp_path / "gmail")
    owner = family_owner(harness, path)
    session = owner.execute.keywords["session"]
    session.spawn = gmail.spawn
    seen = []
    real = tasks.submit_family

    def recording(value, settings, mail, **kwargs):
        """Pass through, noting the settings the worker used."""
        seen.append(settings)
        return real(value, settings, mail, **kwargs)

    monkeypatch.setattr(f"{TASKS}.submit_family", recording)
    yield SimpleNamespace(
        harness=harness,
        path=path,
        gmail=gmail,
        owner=owner,
        session=session,
        seen=seen,
    )
    session.close()


def other(batch):
    """Another Family's message, sent through the same batched session."""
    value = FamilyDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        ("other@example.org",),
        "Campaign",
        "<p>Another Family</p>",
        "Another Family",
    )
    result = submit_family(
        KEY, SETTINGS, value, seconds=10, check=lambda: None, session=batch.session
    )
    return value, result.status


def send(batch, message):
    """Deliver the database message through the shared handler."""
    deliver(batch.harness, batch.path, message, batch.owner)
    message.refresh_from_db()
    return message


def task(message):
    """The message's Task row."""
    return TaskRun.objects.get(pk=message.task_id)


def wait_past(instant, circuit=None):
    """Poll PostgreSQL's wall clock (and a limit hold) rather than sleep a guess.

    ``instant`` is a deadline or ``not_before`` the database itself compares
    with ``clock_timestamp()``; a fixed host sleep can end early on a slow or
    loaded runner. ``circuit`` also waits out the worker's in-memory hold.
    """
    limit = time.monotonic() + 30
    while True:
        with transaction.atomic():
            passed = database_now() > instant
        if passed and (circuit is None or circuit.limit_remaining() == 0):
            return
        assert time.monotonic() < limit, "the deadline never passed"
        Event().wait(0.05)


def fast_limits(monkeypatch):
    """Sending-limit holds of one second, in both places they are read."""
    fast = {"daily": 1, "rate": 1, "message": 1}
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_RETRY_SECONDS", fast)
    monkeypatch.setattr(f"{TASKS}.LIMIT_RETRY_SECONDS", fast)


def test_a_batch_shares_one_helper_and_settles_each_message(batch):
    """One process, token and connection; each message has its own outcome."""
    reaped = []
    real_reap = batch.session.reap

    def reap():
        """Note the message's state when the worker reaps retired helpers."""
        reaped.append(OutboxMessage.objects.get().state)
        real_reap()

    batch.session.reap = reap
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        before, first = other(batch)
        message = send(batch, prepare(batch.harness))
    # Reaping happens before the message commits "submitting".
    assert reaped == ["pending"]
    after, last = other(batch)
    assert batch.seen == [SETTINGS]
    assert (first, last) == (Status.ACCEPTED, Status.ACCEPTED)
    assert message.state == "delivered" and task(message).state == "succeeded"
    gmail = batch.gmail
    assert [gmail.data(value) for value in (before, message, after)] == [1, 1, 1]
    assert gmail.count("spawn") == gmail.count("token") == 1
    assert gmail.count("connect") == 1


def test_a_worker_crash_mid_batch_leaves_the_message_unknown(batch, monkeypatch):
    """The in-flight message becomes delivery unknown; nothing is resent.

    The worker dies while the helper is inside DATA. The helper dies with it
    (here: killed; in production it reads EOF). Recovery, after the
    message's provider deadline, records the uncertain attempt; the rest of
    the batch continues on a fresh helper.
    """
    monkeypatch.setattr(family_mail_dispatch, "PROVIDER_SECONDS", 3)
    gmail = batch.gmail
    real_check = tasks._check

    def crashing(execution):
        """Die as soon as the helper is inside DATA."""
        if gmail.count("hang"):
            raise WorkerCrash()
        real_check(execution)

    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        before, _ = other(batch)
        message = prepare(batch.harness)
        gmail.script(("data", ["hang"]))
        monkeypatch.setattr(f"{TASKS}._check", crashing)
        with pytest.raises(WorkerCrash):
            send(batch, message)
        monkeypatch.setattr(f"{TASKS}._check", real_check)
        message.refresh_from_db()
        assert message.state == "submitting" and batch.session.process is None
        running = act(_status(task(message)), "heartbeat", lease_seconds=1)
        expire(running)
        # Recovery waits out the provider deadline before deciding.
        wait_past(message.provider_deadline)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            assert recover_hint(
                message.task_id,
                queue=WorkQueue.MAIL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: batch.owner},
            )
        after, status = other(batch)
    message.refresh_from_db()
    assert message.state == "delivery_unknown" and task(message).state == "failed"
    assert status is Status.ACCEPTED and gmail.count("spawn") == 2
    assert [gmail.data(value) for value in (before, message, after)] == [1, 1, 1]


def test_a_limit_mid_batch_defers_only_that_message(batch, monkeypatch):
    """A daily-limit refusal holds the message without spending its budget.

    It is retried after the hold on a fresh helper and sent exactly once;
    other Families' messages keep their own outcomes meanwhile.
    """
    fast_limits(monkeypatch)
    gmail = batch.gmail
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        before, _ = other(batch)
        gmail.script(("mail", LIMIT))
        message = send(batch, prepare(batch.harness))
        assert message.state == "retry_wait" and gmail.data(message) == 0
        assert TaskPhase(last_retry(message.task_id).phase) is TaskPhase.RECONCILING
        circuit = batch.owner.admit.keywords["circuit"]
        assert circuit.limit_remaining() > 0 and batch.session.process is None
        after, status = other(batch)
        wait_past(task(message).not_before, circuit)
        message = send(batch, message)
    assert status is Status.ACCEPTED
    assert message.state == "delivered" and message.attempt == 2
    assert [gmail.data(value) for value in (before, message, after)] == [1, 1, 1]


@pytest.mark.parametrize("stage", ["mail", "data"])
def test_a_connection_drop_mid_batch_never_sends_twice(batch, stage):
    """Before DATA the helper reconnects and sends once; during DATA, unknown."""
    gmail = batch.gmail
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        before, _ = other(batch)
        gmail.script((stage, ["disconnect"]))
        message = send(batch, prepare(batch.harness))
        after, status = other(batch)
    assert status is Status.ACCEPTED
    assert [gmail.data(value) for value in (before, message, after)] == [1, 1, 1]
    if stage == "mail":
        assert message.state == "delivered" and message.attempt == 1
        assert gmail.count("spawn") == 1 and gmail.count("connect") == 2
    else:
        assert message.state == "delivery_unknown"
        assert task(message).state == "failed"
        latest = OutboxEvent.objects.filter(message=message).latest("version")
        assert latest.reason == "smtp_delivery_unknown"


def test_a_temporary_refusal_mid_batch_is_retried_and_tracked(batch, monkeypatch):
    """A 4xx DATA refusal spends one attempt, backs off and is retried."""
    monkeypatch.setattr(family_mail_dispatch, "RETRY_BASE_SECONDS", 1)
    gmail = batch.gmail
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        other(batch)
        gmail.script(("data", ["reply", 452, "4.2.2 Mailbox full"]))
        message = send(batch, prepare(batch.harness))
        assert message.state == "retry_wait" and message.attempt == 1
        latest = OutboxEvent.objects.filter(message=message).latest("version")
        assert latest.reason == "smtp_transient"
        # An ordinary refusal is a spent attempt, not an admission hold.
        assert TaskPhase(last_retry(message.task_id).phase) is not (
            TaskPhase.RECONCILING
        )
        wait_past(task(message).not_before)
        message = send(batch, message)
    assert message.state == "delivered" and message.attempt == 2
    # Refused once, then accepted once: two DATA commands, one acceptance.
    assert gmail.data(message) == 2 and gmail.accepted(message) == 1
    assert gmail.count("spawn") == 1


def test_a_permanent_refusal_mid_batch_fails_only_that_message(batch):
    """An invalid address is recorded as failed; the batch carries on."""
    gmail = batch.gmail
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        before, _ = other(batch)
        gmail.script(("rcpt", ["reply", 550, "5.1.1 No such user"]))
        message = send(batch, prepare(batch.harness))
        after, status = other(batch)
    assert message.state == "permanent_failure" and task(message).state == "failed"
    latest = OutboxEvent.objects.filter(message=message).latest("version")
    assert latest.reason == "smtp_permanent"
    assert status is Status.ACCEPTED and gmail.count("spawn") == 1
    assert [gmail.data(value) for value in (before, message, after)] == [1, 0, 1]


def test_a_deadline_kill_mid_batch_is_logged_and_unknown(batch, monkeypatch):
    """A hung submission is killed at the provider deadline and logged."""
    monkeypatch.setattr(family_mail_dispatch, "PROVIDER_SECONDS", 2)
    gmail = batch.gmail
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        other(batch)
        gmail.script(("data", ["hang"]))
        message = prepare(batch.harness)
        # execute_hint binds the running task so the kill entry can name it.
        with task_scope(message.task_id):
            message = send(batch, message)
        after, status = other(batch)
    assert message.state == "delivery_unknown" and gmail.data(message) == 1
    assert status is Status.ACCEPTED and gmail.data(after) == 1
    entry = OperationalLog.objects.get(event="helper_timed_out")
    assert entry.context["helper"] == "family_delivery_worker"
    assert entry.context["what"] == "mail_helper"
    assert entry.context["task_id"] == str(message.task_id)


@pytest.mark.parametrize("transport", ["batched", "per_message"])
def test_both_transports_deliver_through_the_real_handler(
    dispatch_worker,  # noqa: F811
    tmp_path,
    monkeypatch,
    transport,
):
    """The fallback switch picks the transport; the Task semantics are the same.

    ``per_message`` is the one-helper-per-message path from before #284; its
    helper launch is replaced by a synthetic acceptance so no network is used.
    """
    from parishkit.stewardship.family_delivery import FamilyDeliveryResult

    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(tmp_path / "gmail")
    owner = tasks.delivery_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin="http://localhost:8000",
        credential_path=path,
        batched=transport == "batched",
    )
    session = owner.execute.keywords["session"]
    if session is not None:
        session.spawn = gmail.spawn
    launched = []

    def one_helper(payload, **kwargs):
        """Stand in for the one-message helper and its closed result."""
        launched.append(kwargs["helper"])
        result = FamilyDeliveryResult(Status.ACCEPTED, 1).wire_payload()
        return kwargs["decode"](json.dumps(result).encode())

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", one_helper
    )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message, owner)
    message.refresh_from_db()
    assert message.state == "delivered" and task(message).state == "succeeded"
    if transport == "batched":
        assert launched == [] and gmail.data(message) == 1
        session.close()
    else:
        assert launched == ["family_delivery_worker"] and gmail.events() == []
