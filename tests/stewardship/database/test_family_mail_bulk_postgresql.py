"""Bulk Family send (#430): batched planning, preparation and sending.

Each test runs the real restricted logins (scheduler, worker, mail) against
several synthetic Families, with a fake provider standing in for Gmail.
"""

from datetime import timedelta
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from django.db import connection, connections

from parishkit.stewardship.campaigns.credential_models import RehearsalCredential
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_production import (
    FamilyScheduleProducer,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import (
    family_mail_bulk,
    family_mail_delivery_tasks,
    family_mail_dispatch,
)
from parishkit.stewardship.jobs.dispatch import execute_hint, recover_hint
from parishkit.stewardship.jobs.family_mail_bulk import BulkSettings
from parishkit.stewardship.jobs.family_mail_delivery_tasks import delivery_handler
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE
from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.family_mail_tasks import preparation_handler
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session

from . import response_builders
from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import (  # noqa: F401
    KEY,
    dispatch_worker,
)
from .test_outbox_boundaries_postgresql import control

pytestmark = pytest.mark.django_db(transaction=True)
# Synthetic Families added to the response corpus's one mailable Family.
EXTRA = 5
ON = BulkSettings(True, 20)


class OneCredential:
    """The fixture logs in as Family 1; the other Families only need to exist."""

    class objects:
        @staticmethod
        def get():
            return RehearsalCredential.objects.get(family__family_duid=1)


@pytest.fixture
def families(request, monkeypatch):
    """The dispatch fixture with EXTRA more mailable Families, in Testing mode."""
    original = response_builders.response_source

    def source():
        """The response corpus plus EXTRA Families with one email head each."""
        data = original()
        family, member = data.families[1], data.members[3]
        for index in range(EXTRA):
            duid = 10000 + index
            data.families[duid] = family | {
                "familyDUID": duid,
                "familyID": duid,
                "lastName": f"Household{index}",
                "eMailAddress": f"family{index}@example.org",
            }
            head = 100000 + 2 * index
            data.members[head] = member | {
                "memberDUID": head,
                "familyDUID": duid,
                "firstName": f"Head{index}",
                "emailAddress": f"head{index}@example.org",
            }
        return data

    monkeypatch.setattr(response_builders, "response_source", source)
    monkeypatch.setattr(response_builders, "RehearsalCredential", OneCredential)
    harness, path = request.getfixturevalue("dispatch_worker")
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )

    initialize_key_inventories(harness.rings.private)
    return harness, path


class Provider:
    """A fake Gmail that records each message's recipients."""

    def __init__(self, status=Status.ACCEPTED, *, on_call=None):
        """Answer ``status``; ``on_call(n)`` runs before the n-th answer."""
        self.calls, self.status, self.on_call = [], status, on_call
        self.keys = []

    def __call__(self, value, settings, mail, *, seconds, check, session):
        """Record the message, as Gmail would receive it, then answer."""
        assert not connection.in_atomic_block and 0 < seconds <= 30
        assert value == KEY
        check()
        self.calls.append(mail.recipients)
        self.keys.append(mail.semantic_key)
        if self.on_call is not None:
            self.on_call(len(self.calls))
        return FamilyDeliveryResult(self.status, len(mail.recipients))


def plan(bulk=True):
    """Run one scheduler Family sweep under the real scheduler login."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        FamilyScheduleProducer(uuid4(), bulk=bulk)(guard)


def prepare_owner(harness, bulk=ON):
    """The worker's preparation handler, with or without the bulk path."""
    return preparation_handler(
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        public_origin="http://localhost:8000",
        bulk=bulk,
    )


def prepare_all(harness, bulk=ON):
    """Run the worker's bulk preparation once, as a hint would."""
    owner = prepare_owner(harness, bulk)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        owner.bulk(uuid4())
    connections.close_all()


def mail_owner(harness, path, bulk=ON):
    """The Family MAIL handler, with or without the bulk path."""
    return delivery_handler(
        harness.service.store,
        private=harness.rings.private,
        public_origin="http://localhost:8000",
        credential_path=path,
        bulk=bulk,
    )


def send_all(harness, path, owner=None):
    """Run one mail consumer's bulk send once, as a hint would."""
    owner = owner or mail_owner(harness, path)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        owner.bulk(uuid4())
    connections.close_all()
    return owner


def single(harness, path, run_id, owner=None):
    """The one-at-a-time hint path for one task (execute, else recover)."""
    owner = owner or mail_owner(harness, path, bulk=None)
    options = dict(queue=WorkQueue.MAIL, worker_id=uuid4(), handlers={TASK_TYPE: owner})
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        if not execute_hint(run_id, **options):
            recover_hint(run_id, **options)
    connections.close_all()


def due():
    """The first scheduled invitation's due time (the campaign clock)."""
    return ScheduleDefinition.objects.get().current_revision.due_at


def test_switch_off_builds_no_bulk_path(families):
    """Off (the default), both consumers keep exactly the one-at-a-time path."""
    harness, path = families
    assert prepare_owner(harness, None).bulk is None
    assert prepare_owner(harness, BulkSettings()).bulk is None
    assert mail_owner(harness, path, None).bulk is None
    assert mail_owner(harness, path, BulkSettings()).bulk is None
    assert preparation_handler(scheduler=True, bulk=ON).bulk is None
    assert prepare_owner(harness).bulk is not None
    assert mail_owner(harness, path).bulk is not None
    with pytest.raises(ValueError):
        BulkSettings(True, 0)
    with pytest.raises(ValueError):
        BulkSettings(True, 101)


@pytest.mark.parametrize("production", [False, True])
def test_bulk_send_delivers_each_family_exactly_once(families, monkeypatch, production):
    """Plan, prepare and send in batches: one accepted message per Family."""
    harness, path = families
    if production:
        harness = activate_response_service(harness)
        complete_empty_catchup(harness.campaign, uuid4())
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        assert FamilyMailPreparation.objects.count() == EXTRA + 1
        prepare_all(harness)
        assert OutboxMessage.objects.filter(state="pending").count() == EXTRA + 1
        assert set(
            TaskRun.objects.filter(task_type=PREPARE).values_list("state", flat=True)
        ) == {"succeeded"}
        send_all(harness, path)
        # A second pass (another hint) finds nothing left to send.
        send_all(harness, path)
    messages = OutboxMessage.objects.all()
    assert {message.state for message in messages} == {"delivered"}
    assert len(provider.calls) == len(messages) == EXTRA + 1
    assert len({message.family_id for message in messages}) == EXTRA + 1
    if not production:
        # Testing routes every message to the Testing recipient only.
        assert set(provider.calls) == {("test@example.org",)}
    assert set(
        TaskRun.objects.filter(task_type=TASK_TYPE).values_list("state", flat=True)
    ) == {"succeeded"}
    assert ScheduleFulfillment.objects.filter(disposition="delivered").count() == (
        EXTRA + 1
    )


def test_bulk_and_single_paths_finish_each_others_work(families, monkeypatch):
    """Work prepared in bulk is sent by the single path, and vice versa."""
    harness, path = families
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan(bulk=False)
        prepare_all(harness)
        first, *rest = OutboxMessage.objects.order_by("id")
        # The switch turned off mid-send: one message goes the old way.
        single(harness, path, first.task_id)
        send_all(harness, path)
    assert set(OutboxMessage.objects.values_list("state", flat=True)) == {"delivered"}
    assert len(provider.calls) == EXTRA + 1


def test_a_refused_family_does_not_hold_up_the_batch(families, monkeypatch):
    """One Family whose preparation fails rolls back alone; the rest proceed."""
    harness, path = families
    from parishkit.stewardship.jobs import family_mail_preparation

    real = family_mail_preparation.load_family_mail_source
    bad = []

    def refuse_one(family):
        """Fail the first Family asked for, every time."""
        if not bad:
            bad.append(family.pk)
        if family.pk == bad[0]:
            raise PermissionError("synthetic refusal")
        return real(family)

    monkeypatch.setattr(family_mail_preparation, "load_family_mail_source", refuse_one)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
    assert OutboxMessage.objects.count() == EXTRA
    assert not OutboxMessage.objects.filter(family_id=bad[0]).exists()
    # The refused task is untouched, left queued for the one-at-a-time path.
    states = list(
        TaskRun.objects.filter(task_type=PREPARE).values_list("state", flat=True)
    )
    assert sorted(states) == ["queued"] + ["succeeded"] * EXTRA


def test_two_bulk_senders_never_send_a_message_twice(families, monkeypatch):
    """Both mail consumers batching at once still send each message once."""
    harness, path = families
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        owners = [mail_owner(harness, path) for _ in range(2)]
        start, errors = Barrier(2), []

        def run(owner):
            """One consumer's bulk pass, started together with the other."""
            try:
                start.wait()
                owner.bulk(uuid4())
            except Exception as error:  # noqa: BLE001
                errors.append(error)
            finally:
                connections.close_all()

        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            connections.close_all()
            threads = [Thread(target=run, args=(owner,)) for owner in owners]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(120)
    assert not errors
    assert len(provider.calls) == EXTRA + 1
    assert set(OutboxMessage.objects.values_list("state", flat=True)) == {"delivered"}


def test_pause_mid_batch_sends_nothing_more(families, monkeypatch):
    """A pause during a batch: messages not yet sent are recorded unsent."""
    harness, path = families
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())

    def pause_after_first(count):
        """Pause Production delivery as soon as the first message is sent."""
        if count == 1:
            # The pause is an Administrator's, not the mail login's: step out
            # of the mail role on this idle connection for it, then back.
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
            control(harness.campaign, "pause")
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_mail_dispatch")

    provider = Provider(on_call=pause_after_first)
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            mail_owner(harness, path).bulk(uuid4())
            connections.close_all()
    assert len(provider.calls) == 1
    states = sorted(OutboxMessage.objects.values_list("state", flat=True))
    # One sent; the rest of its batch definitely unsent (retry after the
    # pause), and later batches left untouched for the single path's hold.
    assert states[0] == "delivered" and "retry_wait" in states
    assert set(states[1:]) <= {"retry_wait", "pending"}
    # Another pass while paused sends nothing: the batch leaves held mail
    # to the single path, which holds it as it always has.
    with campaign_clock(due() + timedelta(hours=1)):
        send_all(harness, path)
    assert len(provider.calls) == 1


def test_crash_after_submitting_never_resends(families, monkeypatch):
    """A batch that dies after its "submitting" commit becomes unknown, not resent."""
    harness, path = families
    provider = Provider()
    # A short lease and deadline, so recovery is due within the test.
    monkeypatch.setattr(family_mail_bulk, "SEND_LEASE_SECONDS", 5)
    monkeypatch.setattr(family_mail_bulk, "LEASE_MARGIN_SECONDS", 1)
    monkeypatch.setattr(family_mail_bulk, "FIRST_DEADLINE_SECONDS", 4)
    monkeypatch.setattr(family_mail_bulk, "DEADLINE_STEP_SECONDS", 0)

    class Killed(BaseException):
        """Stands in for the process being killed mid-send."""

    def killed(*args, **kwargs):
        """Kill the consumer after sending, before any outcome is recorded."""
        provider(*args, **kwargs)
        raise Killed

    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", killed)
    # The outcome commit never runs: the kill happens before it.
    real_finish = family_mail_dispatch.finish_submission
    monkeypatch.setattr(family_mail_dispatch, "finish_submission", _never)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        with pytest.raises(Killed):
            send_all(harness, path)
    assert len(provider.calls) == 1
    submitting = list(OutboxMessage.objects.filter(state="submitting"))
    assert submitting
    # Restart: after the lease, recovery records every message of the dead
    # batch delivery_unknown, and the rest are sent; none is sent twice.
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    monkeypatch.setattr(family_mail_dispatch, "finish_submission", real_finish)
    _wait_for_lease_expiry()
    with campaign_clock(due()):
        for message in submitting:
            single(harness, path, message.task_id)
        send_all(harness, path)
    assert len(provider.keys) == len(set(provider.keys))
    unknown = {message.pk for message in submitting}
    for message in OutboxMessage.objects.all():
        assert message.state == (
            "delivery_unknown" if message.pk in unknown else "delivered"
        )
    assert len(provider.keys) == 1 + (EXTRA + 1 - len(unknown))
    assert not ScheduleOccurrence.objects.filter(state="pending").exists()


def _never(*args, **kwargs):
    """An outcome commit that never happens (the process is gone)."""
    raise AssertionError("unreachable after the kill")


def _wait_for_lease_expiry(limit=15):
    """Wait until every running delivery task's lease has expired (DB clock)."""
    from time import monotonic, sleep

    deadline = monotonic() + limit
    while monotonic() < deadline:
        if not TaskRun.objects.filter(
            task_type=TASK_TYPE, state="running", lease_expires_at__gt=_db_now()
        ).exists():
            return
        sleep(0.2)
    pytest.fail("Delivery leases did not expire in time.")


def _db_now():
    """PostgreSQL's wall clock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def test_a_mode_switch_before_sending_sends_no_testing_mail(families, monkeypatch):
    """Testing mail prepared before go-live is never sent once Production starts."""
    harness, path = families
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        assert set(OutboxMessage.objects.values_list("mode", flat=True)) == {"testing"}
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    with campaign_clock(due()):
        send_all(harness, path)
        for message in OutboxMessage.objects.filter(mode="testing"):
            single(harness, path, message.task_id)
    assert provider.calls == []
    assert not OutboxMessage.objects.filter(
        mode="testing", state__in=("delivered", "submitting", "delivery_unknown")
    ).exists()
