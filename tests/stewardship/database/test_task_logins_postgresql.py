"""Task writes are bound to the database login, by task type.

The execution guard trusts ``actor_id = worker_id``, and ``actor_id`` is
supplied by the caller, so SQL also binds every task write to the login that
executes its type (#306 M1), and every task INSERT (a new root or an explicit
retry) to the logins whose code creates that type (#389). Each test runs as
the exact provisioned login name with that service's real runtime grants.
"""

import time
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import enqueue, retry_failed

from ..test_task_creators import derived_creators
from .test_background_grants_postgresql import task_login
from .test_taskrun_postgresql import act, permit

pytestmark = pytest.mark.django_db(transaction=True)

WORKER_TYPE = "branding_cleanup"
MAIL_TYPE = "outbox_delivery"
OWNED = [(ServiceRole.WORKER, WORKER_TYPE), (ServiceRole.MAIL_DISPATCH, MAIL_TYPE)]
REFUSAL = "This login cannot change this task"
CREATE_REFUSAL = "This login cannot create this task type"


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
    """Web creates its own types and cancels only an Admin's waiting cleanup."""
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


def installed_creators():
    """The creator map as the database holds it, for every executed type."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT t, stewardship_task_type_creators_v1(t) FROM unnest(%s::text[]) t",
            [sorted(derived_creators())],
        )
        return {name: frozenset(logins) for name, logins in cursor.fetchall()}


def test_installed_creator_map_is_the_derived_map():
    """The migrated database holds exactly the map the code implies."""
    assert installed_creators() == derived_creators()
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_task_type_creators_v1('unknown_probe')")
        assert cursor.fetchone() == (None,)


@pytest.mark.parametrize(
    "role",
    [
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
    ],
)
def test_each_login_creates_exactly_its_mapped_types(role):
    """Every mapped (type, login) INSERT passes the guard; every other one is refused.

    A few types also have their own domain pins (a Production link task needs
    its committed intent, for example), which a synthetic task cannot meet.
    Those still prove the guard admitted the login: they fail later, with an
    integrity error rather than the guard's privilege refusal.
    """
    login = "pk_stewardship_" + role.value.replace("-", "_")
    creators = derived_creators()
    created, pinned, refusals = [], [], []
    with task_login(role, exact=True):
        for task_type, logins in sorted(creators.items()):
            if login in logins:
                try:
                    created.append(queued(task_type).run_id)
                except DatabaseError as error:
                    assert error.__cause__.sqlstate != "42501", (task_type, error)
                    pinned.append(task_type)
                continue
            with pytest.raises(DatabaseError) as error:
                queued(task_type)
            assert error.value.__cause__.sqlstate == "42501", task_type
            refusals.append(str(error.value))
    allowed = sorted(t for t, logins in creators.items() if login in logins)
    assert len(created) + len(pinned) == len(allowed)
    assert TaskRun.objects.filter(pk__in=created).count() == len(created)
    # Only the types with their own insert pins fail for domain reasons.
    assert set(pinned) <= {
        "production_token_cleanup",
        "production_tokens",
        "report_fact_verification",
    }, pinned
    if role is ServiceRole.MAIL_DISPATCH:
        # Mail dispatch holds no INSERT at all and creates nothing.
        assert not allowed and len(refusals) == len(creators)
    else:
        assert allowed
        assert all(CREATE_REFUSAL in text for text in refusals)


def failed(task_type):
    """A failed run of ``task_type``, created and failed as the schema owner."""
    return act(act(queued(task_type), "claim"), "permanent_failure")


def retry(status):
    """Explicitly retry a failed run, as an Admin retry command would."""
    return retry_failed(
        run_id=status.run_id,
        command_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
    )


def test_explicit_retries_are_bound_to_the_creating_logins():
    """A retry is an INSERT too: only a creating login may retry a type."""
    web_only, scheduler_only = failed("setup_source_load"), failed("branding_cleanup")
    with task_login(ServiceRole.WORKER, exact=True):
        with pytest.raises(DatabaseError) as error:
            retry(web_only)
        assert CREATE_REFUSAL in str(error.value)
    with task_login(ServiceRole.WEB, exact=True):
        with pytest.raises(DatabaseError) as error:
            retry(scheduler_only)
        assert CREATE_REFUSAL in str(error.value)
        retried = retry(web_only)
    assert TaskRun.objects.get(pk=retried.run_id).action == "explicit_retry"
    assert TaskRun.objects.filter(root_id=scheduler_only.root_id).count() == 1


def test_an_unrecognized_login_creates_nothing():
    """A login with web's grants but another name cannot create any type."""
    with (
        task_login(ServiceRole.WEB, exact=False),
        pytest.raises(DatabaseError) as error,
    ):
        queued("setup_source_load")
    assert error.value.__cause__.sqlstate == "42501"
    assert CREATE_REFUSAL in str(error.value)


def test_a_definer_body_creates_any_type_for_its_caller():
    """SECURITY DEFINER bodies run as the owner, which the guard exempts.

    The confirmation effect inserts ``activation_catchup`` this way on
    web's behalf; a scheduler-only type shows the exemption is the owner's,
    not web's map entry. EXECUTE stays the definer's binding.
    """
    run_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE FUNCTION test_task_definer_insert(task uuid) RETURNS void "
            "LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ "
            "INSERT INTO public.stewardship_task_run(id,root_id,version,"
            "retry_sequence,task_type,idempotency_key,domain_request_id,"
            "initiated_by_id,actor_id,correlation_id) VALUES(task,task,1,0,"
            "'branding_cleanup',task::text,task,NULL,NULL,task) $$"
        )
    try:
        with task_login(ServiceRole.WEB, exact=True):
            with pytest.raises(DatabaseError) as error:
                queued("branding_cleanup")
            assert CREATE_REFUSAL in str(error.value)
            with connection.cursor() as cursor:
                cursor.execute("SELECT test_task_definer_insert(%s)", [run_id])
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP FUNCTION test_task_definer_insert(uuid)")
    assert TaskRun.objects.get(pk=run_id).task_type == "branding_cleanup"
