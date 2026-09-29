"""Gmail sending limits defer Family mail instead of failing it (#283)."""

from datetime import UTC, timedelta
from threading import Event

import pytest
from django.utils import timezone

from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult, ProviderHealth
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_dispatch
from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
    DeliveryCircuit,
    preparation_attempts,
)
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
    over_daily_limit,
    sends_in_last_day,
)
from parishkit.stewardship.jobs.models import TaskRun, TaskRunEvent
from parishkit.stewardship.jobs.outbox_models import OutboxEvent
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.storage import _status

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import (  # noqa: F401
    deliver,
    dispatch_worker,
)
from .test_operational_routing_postgresql import routing  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
TASKS = "parishkit.stewardship.jobs.family_mail_delivery_tasks"

DAILY = FamilyDeliveryResult(
    Status.TRANSIENT, 1, health=ProviderHealth.HEALTHY, limit="daily"
)


def submit(message, harness, result):
    """One real restricted attempt that ends in ``result``."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim(message)
        begin_submission(
            message.pk,
            execution.claim,
            private=harness.rings.private,
            public_origin="http://localhost:8000",
        )
        return finish_submission(message.pk, execution.claim, result)


def provider_returning(result, calls):
    """A synthetic private transport that records each call."""

    def provider(value, settings, mail, **kwargs):
        calls.append(mail.semantic_key)
        return result

    return provider


def last_retry(task_id):
    """The newest retryable_failure event of a Task."""
    return (
        TaskRunEvent.objects.filter(run_id=task_id, action="retryable_failure")
        .order_by("-version")
        .first()
    )


@pytest.mark.parametrize(
    "purpose,sent,recipients,over",
    [
        # Bulk mail stops at 1,600 recipients (1,800 less the reserve).
        ("initial", 1599, 1, False),
        ("initial", 1600, 1, True),
        ("reminder", 1599, 2, True),
        ("family_test", 1599, 1, False),
        # Receipts and alerts may use the reserve, up to 1,800.
        ("receipt", 1799, 1, False),
        ("receipt", 1800, 1, True),
        ("operational", 1799, 1, False),
        ("daily_digest", 1798, 3, True),
    ],
)
def test_the_limit_counts_recipients_with_a_reserve(purpose, sent, recipients, over):
    """The exact boundaries of the bulk and reserved limits."""
    assert over_daily_limit(purpose, sent, recipients) is over


def test_daily_limit_waits_an_hour_and_blames_no_address(family_mail):  # noqa: F811
    """The refused message waits for the limit; stored evidence has no limit key."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        status = submit(message, family_mail, DAILY)
    message.refresh_from_db()
    assert status.state.value == message.state == "retry_wait"
    wait = message.not_before - timezone.now()
    assert timedelta(minutes=55) < wait <= timedelta(minutes=61)
    assert ScheduleOccurrence.objects.get(pk=message.semantic_key).state == "pending"
    event = OutboxEvent.objects.filter(message=message).latest("version")
    assert event.reason == "smtp_transient"
    assert '"health":"healthy"' in event.evidence_note
    assert "limit" not in event.evidence_note


@pytest.mark.parametrize("limited", [True, False])
def test_only_a_limit_refusal_spares_the_attempt_budget(
    family_mail,  # noqa: F811
    monkeypatch,
    limited,
):
    """Past the attempt budget, a plain transient fails but a limit still waits."""
    monkeypatch.setattr(family_mail_dispatch, "MAX_ATTEMPTS", 1)
    result = DAILY if limited else FamilyDeliveryResult(Status.TRANSIENT, 1)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        status = submit(message, family_mail, result)
    assert status.state.value == ("retry_wait" if limited else "permanent_failure")


def test_an_old_message_is_not_failed_by_its_first_limit_refusal(
    family_mail,  # noqa: F811
    monkeypatch,
):
    """The give-up clock starts at the first limit refusal, not message creation.

    Launch mail capped past two days, or retried by staff, must still wait out
    its first limit refusal.
    """
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_GIVE_UP", timedelta(0))
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        status = submit(message, family_mail, DAILY)
    assert status.state.value == "retry_wait"


def test_a_limit_refused_continuously_fails_visibly(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """A second limit refusal after LIMIT_GIVE_UP in one run fails the message."""
    harness, path = dispatch_worker
    calls = []
    monkeypatch.setattr(f"{TASKS}.submit_family", provider_returning(DAILY, calls))
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_GIVE_UP", timedelta(0))
    fast = {"daily": 1, "rate": 1}
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_RETRY_SECONDS", fast)
    monkeypatch.setattr(f"{TASKS}.LIMIT_RETRY_SECONDS", fast)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
        message.refresh_from_db()
        assert message.state == "retry_wait"
        Event().wait(1.1)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert len(calls) == 2 and message.state == "permanent_failure"


def test_no_give_up_while_other_mail_is_accepted(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """A long limit run with any acceptance since it began is a draining queue."""
    harness, path = dispatch_worker
    calls = []
    monkeypatch.setattr(f"{TASKS}.submit_family", provider_returning(DAILY, calls))
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_GIVE_UP", timedelta(0))
    fast = {"daily": 1, "rate": 1}
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_RETRY_SECONDS", fast)
    monkeypatch.setattr(f"{TASKS}.LIMIT_RETRY_SECONDS", fast)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
        Event().wait(1.1)
        # Another Family's message is accepted while this one waits.
        monkeypatch.setattr(family_mail_dispatch, "accepted_since", lambda _: True)
        deliver(harness, path, message)
    message.refresh_from_db()
    assert len(calls) == 2 and message.state == "retry_wait"


def test_limit_refusals_do_not_count_toward_the_attempt_budget(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """After several limit refusals an ordinary transient still has its budget."""
    harness, path = dispatch_worker
    results = [DAILY, DAILY, FamilyDeliveryResult(Status.TRANSIENT, 1)]
    calls = []

    def provider(value, settings, mail, **kwargs):
        calls.append(mail.semantic_key)
        return results[len(calls) - 1]

    monkeypatch.setattr(f"{TASKS}.submit_family", provider)
    monkeypatch.setattr(family_mail_dispatch, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(family_mail_dispatch, "RETRY_BASE_SECONDS", 1)
    fast = {"daily": 1, "rate": 1}
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_RETRY_SECONDS", fast)
    monkeypatch.setattr(f"{TASKS}.LIMIT_RETRY_SECONDS", fast)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        for _ in range(3):
            deliver(harness, path, message)
            Event().wait(1.1)
    message.refresh_from_db()
    assert len(calls) == 3 and message.attempt == 3
    assert message.state == "retry_wait"
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
        assert family_mail_dispatch.limit_history(message)[0] == 2


def test_the_limit_run_resets_on_other_outcomes_and_staff_retry():
    """Only an unbroken run of limit refusals starts and keeps the give-up clock."""
    from datetime import datetime

    from parishkit.stewardship.jobs.family_mail_dispatch import limit_run

    t = [datetime(2026, 10, 3, hour, tzinfo=UTC) for hour in range(8)]
    # Pairs whose Task deferred in RECONCILING: the limit refusals. The last
    # limit refusal (fence 5) gave up and failed, so its Task never deferred.
    limited = {("r", 1), ("r", 2), ("r", 4)}
    outcome = [
        ("retry_unaccepted", "submitting", "r", 1, t[0]),  # limit
        ("retry_unaccepted", "submitting", "r", 2, t[1]),  # limit
        ("retry_unaccepted", "submitting", "r", 3, t[2]),  # other transient
        ("retry_unaccepted", "submitting", "r", 4, t[3]),  # limit
        ("retry_unaccepted", "submitting", "r", 6, t[4]),  # other transient
        ("retry_failed", "permanent_failure", None, None, t[5]),  # staff retry
    ]
    assert limit_run(outcome[:2], limited) == (2, t[0])
    assert limit_run(outcome[:3], limited) == (2, None)
    assert limit_run(outcome[:4], limited) == (3, t[3])
    assert limit_run(outcome[:5], limited) == (3, None)
    assert limit_run(outcome[:4] + outcome[5:], limited) == (3, None)
    # Submit events themselves (previous state pending) are not outcomes.
    assert limit_run([("submit", "pending", "r", 1, t[0])], limited) == (0, None)


@pytest.mark.parametrize(
    "result,counted",
    [
        (FamilyDeliveryResult(Status.ACCEPTED, 1), 1),
        (FamilyDeliveryResult(Status.UNKNOWN, 1), 1),
        (FamilyDeliveryResult(Status.TRANSIENT, 1), 0),
        (DAILY, 0),
    ],
)
def test_the_mail_role_counts_what_gmail_counts(
    family_mail,  # noqa: F811
    result,
    counted,
):
    """Accepted and uncertain recipients count toward the day; refusals do not."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        submit(message, family_mail, result)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
            assert sends_in_last_day() == counted


def test_a_full_day_defers_after_the_claim_without_sending(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """Over the limit, the claimed Task waits 15 minutes as a hold, not a failure.

    Deferring after the claim moves not_before forward, so the queue goes quiet
    instead of refusing (and logging) the same claim on every scan.
    """
    harness, path = dispatch_worker
    calls = []
    monkeypatch.setattr(
        f"{TASKS}.submit_family",
        provider_returning(FamilyDeliveryResult(Status.ACCEPTED, 1), calls),
    )
    monkeypatch.setattr(f"{TASKS}.over_daily_limit", lambda *args: True)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        deliver(harness, path, message)
    message.refresh_from_db()
    task = TaskRun.objects.get(pk=message.task_id)
    assert calls == [] and message.attempt == 0 and message.state == "pending"
    assert task.state == "retry_wait"
    assert (
        timedelta(minutes=14)
        < task.not_before - timezone.now()
        <= timedelta(minutes=21)
    )
    assert TaskPhase(last_retry(task.pk).phase) is TaskPhase.RECONCILING


def test_limit_deferrals_never_exhaust_the_preparation_budget(
    dispatch_worker,  # noqa: F811
    monkeypatch,
):
    """More limit deferrals than MAX_ATTEMPTS still leave the whole budget.

    A later crash or lease loss must not turn the message into a failure just
    because it waited out several limits.
    """
    harness, path = dispatch_worker
    calls = []
    monkeypatch.setattr(f"{TASKS}.submit_family", provider_returning(DAILY, calls))
    monkeypatch.setattr(family_mail_dispatch, "MAX_ATTEMPTS", 2)
    fast = {"daily": 1, "rate": 1}
    monkeypatch.setattr(family_mail_dispatch, "LIMIT_RETRY_SECONDS", fast)
    monkeypatch.setattr(f"{TASKS}.LIMIT_RETRY_SECONDS", fast)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        for _ in range(3):
            deliver(harness, path, message)
            Event().wait(1.1)
    message.refresh_from_db()
    assert len(calls) == 3 and message.state == "retry_wait"
    task = TaskRun.objects.get(pk=message.task_id)
    assert task.state == "retry_wait" and task.attempt == 3
    assert preparation_attempts(_status(task)) == 0
    assert TaskPhase(last_retry(task.pk).phase) is TaskPhase.RECONCILING


def test_a_limit_holds_new_sends_until_it_lifts():
    """A limit hold is separate from outages: a healthy result cannot clear it."""
    circuit = DeliveryCircuit()
    assert circuit.hold(3600)
    assert not circuit.hold(60)
    circuit.observe(ProviderHealth.HEALTHY)
    assert 3500 < circuit.limit_remaining() <= 3600
    assert not circuit.halted.is_set() and circuit.failures == 0
    # A healthy observation followed by a limit still starts one hold.
    other = DeliveryCircuit()
    other.observe(ProviderHealth.HEALTHY)
    assert other.hold(900) and other.limit_remaining() > 0


def test_the_daily_count_is_reused_briefly(monkeypatch):
    """Thousands of queued messages read the count once per window."""
    calls = []
    monkeypatch.setattr(f"{TASKS}.sends_in_last_day", lambda: calls.append(1) or 5)
    circuit = DeliveryCircuit()
    assert [circuit.daily_sends() for _ in range(100)] == [5] * 100
    assert len(calls) == 1


@pytest.mark.parametrize("limited", [True, False])
def test_an_alert_at_a_limit_keeps_its_budget(routing, monkeypatch, limited):  # noqa: F811
    """Operational alerts wait out a sending limit instead of failing."""
    from parishkit.stewardship.jobs import operational_dispatch

    from .test_operational_dispatch_postgresql import allocated, begin
    from .test_operational_dispatch_postgresql import claim as claim_alert

    monkeypatch.setattr(family_mail_dispatch, "MAX_ATTEMPTS", 1)
    store = routing[0]
    message = allocated(routing)[0].outbox
    result = DAILY if limited else FamilyDeliveryResult(Status.TRANSIENT, 1)
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim_alert(message, store)
        begin(message, execution, store)
        status = operational_dispatch.finish_submission(
            message.pk, execution.claim, result
        )
    assert status.state.value == ("retry_wait" if limited else "permanent_failure")


def test_accepted_since_reads_real_acceptances(family_mail):  # noqa: F811
    """The give-up guard sees an acceptance under the exact mail role."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(family_mail)
        before = timezone.now() - timedelta(minutes=1)
        submit(message, family_mail, FamilyDeliveryResult(Status.ACCEPTED, 1))
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
            assert family_mail_dispatch.accepted_since(before)
            assert not family_mail_dispatch.accepted_since(
                timezone.now() + timedelta(minutes=1)
            )
