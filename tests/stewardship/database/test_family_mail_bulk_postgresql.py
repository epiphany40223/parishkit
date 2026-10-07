"""Bulk Family send (#430): batched planning, preparation and sending.

Each test runs the real restricted logins (scheduler, worker, mail) against
several synthetic Families, with a fake provider standing in for Gmail.
"""

import json
from datetime import timedelta
from threading import Barrier, Thread
from time import sleep
from uuid import uuid4

import pytest
from django.db import connection, connections, transaction

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
    # One pass per call: no idle wait for work that will never come here.
    monkeypatch.setattr(family_mail_bulk, "IDLE_SECONDS", 0)
    harness, path = request.getfixturevalue("dispatch_worker")
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )

    initialize_key_inventories(harness.rings.private)
    return harness, path


class Provider:
    """A fake Gmail that records each message's recipients."""

    def __init__(self, status=Status.ACCEPTED, *, on_call=None, delay=0.0):
        """Answer ``status`` after ``delay`` s; ``on_call(n)`` runs before answer n."""
        self.calls, self.status, self.on_call = [], status, on_call
        self.keys, self.delay, self.seconds = [], delay, []

    def __call__(self, value, settings, mail, *, seconds, check, session):
        """Record the message, as Gmail would receive it, then answer."""
        assert not connection.in_atomic_block and 0 < seconds <= 30
        assert value == KEY
        self.seconds.append(seconds)
        if self.delay:
            sleep(self.delay)
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
    hint = TaskRun.objects.filter(task_type=PREPARE).values_list("pk", flat=True)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        owner.bulk(hint.first())
    connections.close_all()


def family_hint():
    """A due scheduled-Family delivery task, as the scheduler would hint it."""
    return (
        TaskRun.objects.filter(
            task_type=TASK_TYPE,
            state__in=("queued", "retry_wait"),
            domain_request_id__in=OutboxMessage.objects.filter(
                purpose__in=("initial", "reminder")
            ).values("pk"),
        )
        .values_list("pk", flat=True)
        .first()
    )


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
    hint = family_hint()
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        if hint is not None:
            owner.bulk(hint)
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
        start, errors, hint = Barrier(2), [], family_hint()

        def run(owner):
            """One consumer's bulk pass, started together with the other."""
            try:
                start.wait()
                owner.bulk(hint)
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
    _one_batch(monkeypatch)

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
        send_all(harness, path)
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
    monkeypatch.setattr(family_mail_bulk, "MIN_LAUNCH_SECONDS", 1)

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


def _as_owner(statements):
    """Run SQL as the test owner with triggers off, then resume the mail login.

    Simulates a lifecycle transition landing mid-batch (the real transitions
    need an Administrator session): only rows the bulk send reads before its
    next launch change. The mail role's connection is idle between sends.
    """
    with connection.cursor() as cursor:
        cursor.execute("RESET SESSION AUTHORIZATION")
        cursor.execute("SET session_replication_role = replica")
        for statement in statements:
            cursor.execute(statement)
        cursor.execute("SET session_replication_role = origin")
        cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_mail_dispatch")


def _one_batch(monkeypatch):
    """Let one send batch take every message (a long hold budget)."""
    monkeypatch.setattr(family_mail_bulk, "HOLD_SECONDS", 60)


def _reconciling(message):
    """Whether the message's task waits as an unspent hold (RECONCILING)."""
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        preparation_attempts,
    )
    from parishkit.stewardship.jobs.storage import _status

    task = TaskRun.objects.get(root_id=message.task_id)
    return task.phase == "reconciling" and preparation_attempts(_status(task)) == 0


def _timeouts(what):
    """Durable timeout entries the bulk send recorded for ``what``."""
    from parishkit.stewardship.audit.models import OperationalLog

    sleep(0.5)  # record_timeout_within writes on its own thread
    return [
        row.context
        for row in OperationalLog.objects.filter(event="task_timed_out")
        if row.context.get("what") == what
    ]


@pytest.mark.parametrize("change", ["go_live", "close", "mode"])
def test_a_transition_mid_batch_sends_nothing_more(families, monkeypatch, change):
    """Go-live, close or a mode switch after the first send: no later send.

    Unlaunched messages are recorded definitely unsent. A close is a hold
    that spends no attempt; a mode switch or a closed go-live gate makes the
    Testing messages definitively unsendable, recorded failed (never sent,
    never unknown), as finish_submission records a changed mode.
    """
    harness, path = families
    _one_batch(monkeypatch)
    # The close moves the campaign clock a day past the campaign's own end,
    # never relative to today: the fixture campaign lies far in the future
    # (tests/stewardship/fixture_calendar.py), so a real-clock offset would
    # still fall before it opens.
    harness.campaign.refresh_from_db()
    closed = harness.campaign.active_configuration.ends_at + timedelta(days=1)
    statements = {
        "go_live": ["UPDATE stewardship_campaign_credentials SET go_live_gate=true"],
        "close": [
            "CREATE OR REPLACE FUNCTION stewardship_campaign_now_v1() "
            "RETURNS timestamptz LANGUAGE sql STABLE AS "
            f"$$SELECT '{closed.isoformat()}'::timestamptz$$"
        ],
        "mode": ["UPDATE stewardship_system_configuration SET mode='production'"],
    }[change]

    def transition(count):
        """Apply the transition right after the first message is sent."""
        if count == 1:
            _as_owner(statements)

    provider = Provider(on_call=transition)
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        send_all(harness, path)
    assert len(provider.calls) == 1
    rest = list(OutboxMessage.objects.exclude(state="delivered"))
    assert len(rest) == EXTRA
    if change in {"mode", "go_live"}:
        assert {message.state for message in rest} == {"permanent_failure"}
    else:
        assert {message.state for message in rest} == {"retry_wait"}
        assert all(_reconciling(message) for message in rest)


def test_late_positions_are_released_unsent_not_rushed(families, monkeypatch):
    """A message whose deadline leaves under MIN_LAUNCH_SECONDS is not launched.

    Per-position deadlines step by DEADLINE_STEP_SECONDS (here 1 s, with a
    2 s provider), so alternate messages fall short; each is released as an
    unspent hold and the shortfall is logged durably.
    """
    harness, path = families
    _one_batch(monkeypatch)
    monkeypatch.setattr(family_mail_bulk, "FIRST_DEADLINE_SECONDS", 30)
    monkeypatch.setattr(family_mail_bulk, "DEADLINE_STEP_SECONDS", 1)
    monkeypatch.setattr(family_mail_bulk, "MIN_LAUNCH_SECONDS", 29)
    provider = Provider(delay=2.0)
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        send_all(harness, path)
    held = list(OutboxMessage.objects.filter(state="retry_wait"))
    assert held and provider.calls
    assert len(held) + len(provider.calls) == EXTRA + 1
    assert all(seconds >= 29 for seconds in provider.seconds)
    assert all(_reconciling(message) for message in held)
    (entry,) = _timeouts("mail_helper")
    assert entry["limit_seconds"] == 29 and entry["count"] == len(held)


def test_the_launch_cutoff_releases_the_rest_and_is_logged(families, monkeypatch):
    """Past SEND_CUTOFF_SECONDS no message is launched; the cut is logged."""
    harness, path = families
    _one_batch(monkeypatch)
    monkeypatch.setattr(family_mail_bulk, "SEND_CUTOFF_SECONDS", 1)
    provider = Provider(delay=1.2)
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        send_all(harness, path)
    assert len(provider.calls) == 1
    held = list(OutboxMessage.objects.filter(state="retry_wait"))
    assert len(held) == EXTRA and all(_reconciling(message) for message in held)
    (entry,) = _timeouts("lease")
    assert entry["limit_seconds"] == 1 and entry["count"] == EXTRA


def test_the_lease_margin_stops_a_late_helper_as_unknown(families, monkeypatch):
    """A helper still running at the lease margin is stopped: unknown, logged."""
    harness, path = families
    _one_batch(monkeypatch)
    monkeypatch.setattr(family_mail_bulk, "SEND_LEASE_SECONDS", 60)
    monkeypatch.setattr(family_mail_bulk, "LEASE_MARGIN_SECONDS", 59)
    monkeypatch.setattr(family_mail_bulk, "SEND_CUTOFF_SECONDS", 1)
    provider = Provider(delay=1.2)
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        send_all(harness, path)
    states = sorted(OutboxMessage.objects.values_list("state", flat=True))
    assert states == ["delivery_unknown"] + ["retry_wait"] * EXTRA
    entries = _timeouts("lease")
    # The lease-margin stop (limit 60 - 59) and the cutoff (limit 1).
    assert sorted(entry.get("count", 1) for entry in entries) == [1, EXTRA]
    assert {entry["limit_seconds"] for entry in entries} == {1}


def test_an_unrecorded_outcome_is_recovered_as_unknown(families, monkeypatch):
    """An outcome the database refuses leaves its message for recovery only.

    The lease is short so recovery is due within the test, but long enough
    (15 s against a batch of about a second) that a busy CI CPU cannot run
    the batch past it. With the 5 s lease once used here, packed CI
    partitions ran a batch long enough that a sent message's outcome was
    not recorded either, and the delivered count fell short (#697).
    """
    harness, path = families
    _one_batch(monkeypatch)
    monkeypatch.setattr(family_mail_bulk, "SEND_LEASE_SECONDS", 15)
    monkeypatch.setattr(family_mail_bulk, "LEASE_MARGIN_SECONDS", 1)
    monkeypatch.setattr(family_mail_bulk, "FIRST_DEADLINE_SECONDS", 14)
    monkeypatch.setattr(family_mail_bulk, "DEADLINE_STEP_SECONDS", 0)
    monkeypatch.setattr(family_mail_bulk, "MIN_LAUNCH_SECONDS", 1)
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    real_finish, failed = family_mail_dispatch.finish_submission, []

    def refuse_first(identifier, claim, result, **options):
        """The database refuses the first outcome once."""
        if not failed:
            failed.append(identifier)
            raise RuntimeError("synthetic refusal of an outcome")
        return real_finish(identifier, claim, result, **options)

    monkeypatch.setattr(family_mail_dispatch, "finish_submission", refuse_first)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        send_all(harness, path)
    assert OutboxMessage.objects.get(pk=failed[0]).state == "submitting"
    assert (
        OutboxMessage.objects.filter(state="delivered").count()
        == len(provider.calls) - 1
    )
    _wait_for_lease_expiry(limit=30)
    message = OutboxMessage.objects.get(pk=failed[0])
    with campaign_clock(due()):
        single(harness, path, message.task_id)
        send_all(harness, path)
    message.refresh_from_db()
    assert message.state == "delivery_unknown"
    assert len(provider.keys) == len(set(provider.keys)) == EXTRA + 1


def test_a_kill_before_the_first_send_leaves_only_unknowns(families, monkeypatch):
    """Killed between the "submitting" commit and the first send: no resend."""
    harness, path = families
    _one_batch(monkeypatch)
    monkeypatch.setattr(family_mail_bulk, "SEND_LEASE_SECONDS", 5)
    monkeypatch.setattr(family_mail_bulk, "LEASE_MARGIN_SECONDS", 1)
    monkeypatch.setattr(family_mail_bulk, "FIRST_DEADLINE_SECONDS", 4)
    monkeypatch.setattr(family_mail_bulk, "DEADLINE_STEP_SECONDS", 0)
    monkeypatch.setattr(family_mail_bulk, "MIN_LAUNCH_SECONDS", 1)
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)

    class Killed(BaseException):
        """Stands in for SIGKILL right after the commit."""

    def killed(*args, **kwargs):
        raise Killed

    monkeypatch.setattr(family_mail_bulk, "_held_now", killed)
    real_finish = family_mail_dispatch.finish_submission
    monkeypatch.setattr(family_mail_dispatch, "finish_submission", _never)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        with pytest.raises(Killed):
            send_all(harness, path)
    assert provider.calls == []
    submitting = list(OutboxMessage.objects.filter(state="submitting"))
    assert len(submitting) == EXTRA + 1
    monkeypatch.setattr(family_mail_dispatch, "finish_submission", real_finish)
    _wait_for_lease_expiry()
    with campaign_clock(due()):
        for message in submitting:
            single(harness, path, message.task_id)
        send_all(harness, path)
    # Never sent, but its outcome cannot be known: settled by an Administrator.
    assert provider.calls == []
    assert set(OutboxMessage.objects.values_list("state", flat=True)) == {
        "delivery_unknown"
    }


def test_a_receipt_hint_does_not_wait_behind_a_drain(families, monkeypatch):
    """A receipt's hint starts no drain; the receipt goes out at once."""
    from .test_receipt_dispatch_postgresql import receipt

    harness, path = families
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
        message = receipt(harness)
        owner = mail_owner(harness, path)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
            assert owner.bulk(message.task_id) == 0
        connections.close_all()
        assert provider.calls == []
        single(harness, path, message.task_id, owner)
    message.refresh_from_db()
    assert message.state == "delivered" and len(provider.calls) == 1
    assert not OutboxMessage.objects.filter(
        purpose="initial", state="delivered"
    ).exists()


def _rehearsal_wait(sql, instant):
    """One rehearsal wait predicate (rehearsal.PREPARED_SQL or SETTLED_SQL).

    The operator script polls it with psql at real time; here the clock is
    the fixture campaign's, so ``clock_timestamp()`` is bound to two minutes
    past the due time and only the occurrence, message and task conditions
    decide the answer.
    """
    from parishkit.stewardship.local.rehearsal import bind_due

    query = bind_due(sql).replace("clock_timestamp()", "%(now)s::timestamptz")
    with connection.cursor() as cursor:
        cursor.execute(query, {"due": instant, "now": instant + timedelta(minutes=2)})
        return cursor.fetchone()[0]


def test_a_bulk_send_is_measured_by_the_rehearsal(families, monkeypatch, caplog):
    """BG-12 PR 1: the waits, the timing lines and the measure step on a real send.

    A Production bulk send logs one timing line per lock transaction
    (preparation, the "submitting" commit and the outcome chunks). The
    operator script's waits answer at the right moments: prepared once every
    occurrence has its message (a prepared occurrence stays pending with its
    outbox id set), settled once every message has its outcome. The measure
    step reads every message, outcome and send statistic in the real schema
    under the login it runs as in the VM, the schema owner (the offline
    migration identity), in a READ ONLY snapshot; the web login could not.
    """
    import logging

    from django.db import DatabaseError

    from parishkit.stewardship.local import rehearsal_report as report
    from parishkit.stewardship.local.rehearsal import (
        OUTCOMES_SQL,
        PREPARED_SQL,
        SETTLED_SQL,
        measure_snapshot,
    )

    harness, path = families
    harness = activate_response_service(harness)
    complete_empty_catchup(harness.campaign, uuid4())
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", Provider())
    instant = due()
    with (
        caplog.at_level(logging.DEBUG, logger="parishkit.stewardship.debug"),
        campaign_clock(instant),
    ):
        assert _rehearsal_wait(PREPARED_SQL, instant) is False
        plan()
        # Planned, not yet prepared: occurrences pending without a message.
        assert _rehearsal_wait(PREPARED_SQL, instant) is False
        prepare_all(harness)
        assert set(
            ScheduleOccurrence.objects.values_list("state", "reason").distinct()
        ) == {("pending", "prepared")}
        assert not ScheduleOccurrence.objects.filter(outbox_id__isnull=True).exists()
        assert _rehearsal_wait(PREPARED_SQL, instant) is True
        assert _rehearsal_wait(SETTLED_SQL, instant) is False
        send_all(harness, path)
        assert _rehearsal_wait(SETTLED_SQL, instant) is True
    lines = [
        json.dumps({"extra": {"debug": {"message": record.getMessage()}}})
        for record in caplog.records
    ]
    bulk, _ = report.parse_timings(lines)
    holds = report.hold_summary(bulk)
    assert set(holds) == {"prepare", "commit", "outcome"}
    assert holds["prepare"]["items"] == holds["commit"]["items"] == EXTRA + 1
    assert holds["outcome"]["items"] == EXTRA + 1
    # Every prepared and committed item spent measurable work under the lock.
    assert holds["prepare"]["work_ms"]["n"] == EXTRA + 1
    assert holds["commit"]["work_ms"]["n"] == EXTRA + 1
    # None: the current campaign, read inside the snapshot (the VM's call).
    document = measure_snapshot(None, instant)
    assert document == measure_snapshot(harness.campaign.pk, instant)
    assert document["kinds"] == ["initial"] and document["modes"] == ["production"]
    assert len(document["messages"]) == len(document["outcomes"]) == EXTRA + 1
    assert all(row["stats"]["submit_ms"] >= 0 for row in document["outcomes"])
    assert document["targets_with_two_messages"] == 0
    assert document["targets_with_two_fulfillments"] == 0
    # The fixture's campaign clock is decades ahead of the rows' real
    # created_at, so exactly the two due-time orderings read as broken here;
    # in the VM the clock is real and they hold. The query ran all the same.
    assert document["invariant_violations"] == {
        "messages before their occurrence": EXTRA + 1,
        "fulfillments before their occurrence": EXTRA + 1,
    }
    document["invariant_violations"] = {}
    send = report.send_summary(
        {"due_at": instant.isoformat()}, json.loads(json.dumps(document))
    )
    assert send["correctness"]["passed"], send["correctness"]
    # Why the measure step does not run as web: its grants omit the outcome
    # events' submission instant and evidence.
    message = OutboxMessage.objects.values_list("pk", flat=True).first()
    with (
        task_login(ServiceRole.WEB),
        pytest.raises(DatabaseError, match="permission denied"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(OUTCOMES_SQL, [[message]])
