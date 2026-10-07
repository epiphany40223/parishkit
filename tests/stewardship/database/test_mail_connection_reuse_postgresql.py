"""A mail consumer sends a Family message on its kept connection (#365).

The broker runs ``connection_reuse.refresh()`` before each hint and
``release()`` after it. With reuse on, a whole Family message (claim,
"submitting" commit, the in-flight check during SMTP and the outcome) runs
on one database session, which stays open for the next message. A session
the server ended between messages is replaced before the hint, so the
message is still delivered.
"""

import threading
from uuid import uuid4

import pytest
from django.db import connection, connections

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import connection_reuse
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.lifetime import maintain_execution
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
from .test_mail_consumers_postgresql import sent_once

pytestmark = pytest.mark.django_db(transaction=True)


def backend():
    """This thread's database session id."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def hint(owner, message):
    """Run one hint as the broker does with reuse on."""
    connection_reuse.refresh()
    execution = claim_hint(
        message.task_id,
        queue=WorkQueue.MAIL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: owner},
    )
    assert execution is not None
    with maintain_execution(execution):
        owner.execute(execution)
    connection_reuse.release()


def terminate(pid):
    """End ``pid`` from another session, as an operator's pg_terminate_backend."""
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


@pytest.mark.parametrize("ended", [False, True], ids=["kept", "server-ended"])
def test_a_family_message_runs_on_the_kept_connection(
    dispatch_worker,  # noqa: F811
    monkeypatch,
    ended,
):
    """One session serves the whole message and stays open for the next."""
    harness, path = dispatch_worker
    gmail = FakeGmailHelpers(path.parent / "gmail")
    owner = family_owner(harness, path)
    owner.execute.keywords["session"].spawn = gmail.spawn
    monkeypatch.setattr(connection_reuse, "_enabled", True)
    opened = []
    wrapper = type(connections["default"])
    real_connect = wrapper.connect

    def counting_connect(self):
        """Record every new session, with the thread that opened it."""
        opened.append(threading.current_thread())
        return real_connect(self)

    try:
        with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
            message = prepare(harness)
            with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
                connections.close_all()
                before = backend()
                if ended:
                    terminate(before)
                wrapper.connect = counting_connect
                try:
                    hint(owner, message)
                finally:
                    wrapper.connect = real_connect
                after = backend()
                main_opens = [t for t in opened if t is threading.main_thread()]
    finally:
        owner.execute.keywords["session"].close()
        connection_reuse._enabled = False
        connections.close_all()
    sent_once(gmail, message)
    if ended:
        # The ended session was replaced once, before the hint's work.
        assert after != before and len(main_opens) == 1
    else:
        assert after == before and main_opens == []
