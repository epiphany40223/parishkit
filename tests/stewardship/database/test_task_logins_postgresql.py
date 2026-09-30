"""Task claims and transitions are bound to the executing database login.

The execution guard trusts ``actor_id = worker_id``, and ``actor_id`` is
supplied by the caller, so SQL also binds every task write to the login that
executes its type (#306 M1). Each test runs as the exact provisioned login name
with that service's real runtime grants.
"""

import time
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import enqueue

from .test_background_grants_postgresql import task_login
from .test_taskrun_postgresql import act, permit

pytestmark = pytest.mark.django_db(transaction=True)

WORKER_TYPE = "branding_cleanup"
MAIL_TYPE = "outbox_delivery"
OWNED = [(ServiceRole.WORKER, WORKER_TYPE), (ServiceRole.MAIL_DISPATCH, MAIL_TYPE)]
REFUSAL = "This login cannot change this task"


def queued(task_type):
    """Create a waiting task as the schema owner, as a producer would."""
    return enqueue(
        task_type=task_type,
        domain_request_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
    )


def refused(call, *, guard=True):
    """Expect a privilege refusal, by default the login guard's own."""
    with pytest.raises(DatabaseError) as error:
        call()
    assert error.value.__cause__.sqlstate == "42501"
    if guard:
        assert REFUSAL in str(error.value)


def cancel_sql(run_id, actor_id):
    """Cancel with only the cancel columns, which web and the scheduler hold."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_task_run SET state='cancelled',"
            "action='safe_cancel',version=version+1,actor_id=%s,"
            "correlation_id=%s,lease_expires_at=NULL WHERE id=%s",
            [actor_id, uuid4(), run_id],
        )


@pytest.mark.parametrize(("role", "task_type"), OWNED)
def test_login_claims_and_transitions_its_own_types(role, task_type):
    """Claim, heartbeat, progress, finish, cancel, expire and recover."""
    done, failed, cancelled, lost = (queued(task_type) for _ in range(4))
    with task_login(role, exact=True):
        running = act(done, "claim")
        running = act(running, "heartbeat", lease_seconds=60)
        running = act(running, "progress", progress=(1, 2))
        assert act(running, "complete").state == "succeeded"
        assert act(act(failed, "claim"), "permanent_failure").state == "failed"
        assert act(cancelled, "safe_cancel").state == "cancelled"
        running = act(lost, "claim", lease_seconds=1)
        time.sleep(1.1)
        abandoned = act(running, "lease_expired")
        assert act(abandoned, "recovery_fail").state == "failed"


@pytest.mark.parametrize(
    ("role", "task_type"),
    [(ServiceRole.WORKER, MAIL_TYPE), (ServiceRole.MAIL_DISPATCH, WORKER_TYPE)],
)
def test_login_is_refused_on_another_logins_types(role, task_type):
    """Neither a waiting nor another login's running task can be touched."""
    waiting, running = queued(task_type), queued(task_type)
    running = act(running, "claim")
    with task_login(role, exact=True):
        refused(lambda: act(waiting, "claim"))
        refused(lambda: act(waiting, "safe_cancel"))
        refused(lambda: act(running, "complete"))
        refused(lambda: act(running, "permanent_failure"))
        refused(lambda: act(running, "safe_cancel"))
    assert TaskRun.objects.get(pk=waiting.run_id).state == "queued"
    assert TaskRun.objects.get(pk=running.run_id).state == "running"


def test_web_cancels_waiting_cleanup_but_never_claims():
    """Web creates any known type and cancels only an Admin's waiting cleanup."""
    cleanup, other, running = (
        queued("production_cleanup"),
        queued(WORKER_TYPE),
        act(queued("production_cleanup"), "claim"),
    )
    with task_login(ServiceRole.WEB, exact=True):
        created = queued(MAIL_TYPE)
        # Web lacks the claim columns, and the guard refuses a claim that
        # stays within the cancel columns.
        refused(lambda: act(cleanup, "claim"), guard=False)
        refused(lambda: cancel_sql(other.run_id, uuid4()))
        refused(lambda: cancel_sql(running.run_id, running.worker_id))
        assert act(cleanup, "safe_cancel").state == "cancelled"
    assert TaskRun.objects.get(pk=created.run_id).state == "queued"
    assert TaskRun.objects.get(pk=other.run_id).state == "queued"
    assert TaskRun.objects.get(pk=running.run_id).state == "running"


@pytest.mark.parametrize("role", [ServiceRole.MAIL_DISPATCH, ServiceRole.WEB])
def test_forged_worker_actor_is_refused(role):
    """Knowing a live claim's worker UUID and fence grants nothing to another login."""
    running = act(queued(WORKER_TYPE), "claim")
    with task_login(role, exact=True):
        if role is ServiceRole.WEB:
            refused(lambda: cancel_sql(running.run_id, running.worker_id))
        else:
            for action, extra in (
                ("heartbeat", {"lease_seconds": 60}),
                ("complete", {}),
                ("permanent_failure", {}),
            ):
                refused(lambda a=action, e=extra: act(running, a, **e))
    row = TaskRun.objects.get(pk=running.run_id)
    assert (row.state, row.version) == ("running", running.version)


def test_scheduler_cancels_only_source_work():
    """The scheduler's cancel columns cannot retire any other waiting type."""
    waiting = queued(WORKER_TYPE)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        refused(lambda: cancel_sql(waiting.run_id, uuid4()))
        refused(lambda: act(waiting, "claim"), guard=False)
    assert TaskRun.objects.get(pk=waiting.run_id).state == "queued"


@pytest.mark.parametrize(
    "role", [ServiceRole.WEB, ServiceRole.WORKER, ServiceRole.SCHEDULER]
)
def test_unknown_task_type_cannot_be_created(role):
    """A creating login may only name a type some service executes."""
    with task_login(role, exact=True), pytest.raises(DatabaseError) as error:
        queued("unknown_probe")
    assert error.value.__cause__.sqlstate == "42501"
    assert "Unknown task type" in str(error.value)
