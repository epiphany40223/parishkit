"""A mail consumer sends Family messages on its kept connection (#365).

The broker runs ``connection_reuse.refresh()`` before each hint and
``release()`` after it. With reuse on, every message (claim, "submitting"
commit, the in-flight check during SMTP and the outcome) runs on one
database session, which leaves no session-level state behind. A session the
server ended between messages, or during SMTP, is replaced, and the message
is still recorded as sent.
"""

import threading
from uuid import uuid4

import pytest
from django.db import connection, connections

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import connection_reuse, family_mail_delivery_tasks
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.outbox_models import OutboxEvent, OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue

from . import test_family_mail_bulk_postgresql as bulk
from .test_background_grants_postgresql import task_login
from .test_family_mail_bulk_postgresql import families  # noqa: F401
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
# Session settings the mail path changes only transaction-locally.
SETTINGS = (
    "join_collapse_limit",
    "from_collapse_limit",
    "statement_timeout",
    "lock_timeout",
    "idle_in_transaction_session_timeout",
)


def backend():
    """This thread's database session id."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def leftovers():
    """Session advisory locks and session-level settings this session holds."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks "
            "WHERE locktype='advisory' AND pid=pg_backend_pid()"
        )
        locks = cursor.fetchone()[0]
        cursor.execute(
            "SELECT name FROM pg_settings WHERE source='session' AND name=ANY(%s)",
            [list(SETTINGS)],
        )
        return locks, [row[0] for row in cursor.fetchall()]


def terminate(pid):
    """End ``pid`` from another session, as a restart or an operator would."""
    done = []

    def run():
        """Use this thread's own connection, then close it."""
        try:
            with connection.cursor() as cursor:
                # The fixture's reconnect hook made this session the mail
                # login; the superuser behind it ends the consumer's session.
                cursor.execute("RESET SESSION AUTHORIZATION")
                cursor.execute("SELECT pg_terminate_backend(%s, 5000)", [pid])
                done.append(cursor.fetchone()[0])
        finally:
            connections.close_all()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30)
    assert done == [True]


class Provider:
    """A fake Gmail that runs the in-flight check, then accepts.

    ``during`` runs on the consumer's thread after the check, while the
    message is out at "Gmail", as a PostgreSQL restart mid-SMTP would.
    """

    def __init__(self, during=None):
        """Remember the hook; count calls."""
        self.during, self.calls = during, 0

    def __call__(self, value, settings, mail, *, seconds, check, session):
        """Check ownership as the real helper does, then accept."""
        check()
        self.calls += 1
        if self.during is not None:
            self.during(self.calls)
        return FamilyDeliveryResult(Status.ACCEPTED, len(mail.recipients))


def send_each(harness, path, monkeypatch, provider, between=None):
    """Prepare every Family's invitation, then send them one hint at a time.

    Returns the session id after each message and every in-flight check's
    session id. ``between(n)`` runs before the n-th hint (from 1).
    """
    checks, after = [], []
    real_check = family_mail_delivery_tasks._check

    def recording_check(execution):
        """Run the real in-flight check and note its session."""
        verified = real_check(execution)
        checks.append(backend())
        return verified

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    monkeypatch.setattr(family_mail_delivery_tasks, "_check", recording_check)
    monkeypatch.setattr(family_mail_delivery_tasks, "INFLIGHT_VERIFY_SECONDS", 0)
    monkeypatch.setattr(connection_reuse, "_enabled", True)
    with bulk.campaign_clock(bulk.due()):
        bulk.plan()
        bulk.prepare_all(harness)
        owner = bulk.mail_owner(harness, path, bulk=None)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            connections.close_all()
            first = backend()
            count = 0
            while (run_id := bulk.family_hint()) is not None:
                count += 1
                if between is not None:
                    between(count)
                connection_reuse.refresh()
                assert execute_hint(
                    run_id,
                    queue=WorkQueue.MAIL,
                    worker_id=uuid4(),
                    handlers={TASK_TYPE: owner},
                )
                connection_reuse.release()
                assert leftovers() == (0, [])
                after.append(backend())
            connections.close_all()
    return first, after, checks


def delivered_once():
    """Every invitation is delivered, with one provider outcome each."""
    messages = list(OutboxMessage.objects.all())
    assert messages and {message.state for message in messages} == {"delivered"}
    for message in messages:
        assert (
            OutboxEvent.objects.filter(
                message=message, previous_state="submitting"
            ).count()
            == 1
        )
    return len(messages)


def test_every_message_runs_on_one_kept_session(families, monkeypatch):  # noqa: F811
    """Several messages, and their in-flight checks, share the first session."""
    harness, path = families
    provider = Provider()
    first, after, checks = send_each(harness, path, monkeypatch, provider)
    count = delivered_once()
    assert count == bulk.EXTRA + 1 >= 2 and provider.calls == count
    assert after == [first] * count
    assert checks and set(checks) == {first}


def test_a_session_ended_between_messages_is_replaced(families, monkeypatch):  # noqa: F811
    """refresh() replaces an ended session; the next message still sends."""
    harness, path = families
    ended = []

    def between(n):
        """End the kept session before the second message."""
        if n == 2:
            ended.append(backend())
            terminate(ended[0])

    first, after, _ = send_each(harness, path, monkeypatch, Provider(), between)
    count = delivered_once()
    assert after[0] == first == ended[0]
    assert after[1] != first and set(after[1:]) == {after[1]}
    assert len(after) == count


def test_a_restart_during_smtp_still_records_the_send(families, monkeypatch):  # noqa: F811
    """An accepted message is recorded as sent, never left delivery_unknown."""
    harness, path = families

    def during(n):
        """End the kept session while the first message is at Gmail."""
        if n == 1:
            terminate(backend())

    first, after, _ = send_each(harness, path, monkeypatch, Provider(during))
    assert delivered_once() == len(after)
    assert not OutboxMessage.objects.filter(state="delivery_unknown").exists()
    assert after[0] != first
