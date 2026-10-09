"""Delta refreshes skipped while a bulk Family send is in progress (#440).

The scheduler skips a 15-minute slot during a send and creates it once the
send ends; the nightly full refresh and a manual refresh are never skipped. A
full refresh at another listed daytime time waits like a delta (#465).
The staleness alarm holds within the send's allowance and behaves as before
past it. The send itself is counted from real (temporary, column-subset)
message and Task tables, and the restricted scheduler and worker logins can
read what the check needs.
"""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source import data_age, health, production, send_hold
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.production import SourceProducer, produce_refreshes
from parishkit.stewardship.source.refresh_models import SourceRefreshTick

from .test_background_grants_postgresql import task_login
from .test_refresh_frequency_postgresql import with_frequency, with_schedule
from .test_source_attempts_postgresql import configured
from .test_source_health_postgresql import (
    at_instant,
    future_observation,
    observe,
    overdue_slot,
    publish,
    stale_offset,
)
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)
# 13:23 parish time (America/New_York): a daytime full refresh at 12:00 is due.
NOW = datetime(2026, 9, 11, 17, 23, 42, tzinfo=UTC)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def sending(monkeypatch, active=True):
    """Report a Family send as in progress (or not) without building one.

    Both the scheduler's check and the health sample see the same answer.
    """
    monkeypatch.setattr(send_hold, "family_send_active", lambda: active)


def skipping_deltas(credential):
    """Promote a full refresh and then a delta: the delta cadence is in use.

    No delta is requested after that, as when the scheduler skips them.
    """
    publish(credential)
    publish(credential, kind="delta")


def causes():
    """The causes of every refresh slot the scheduler has created."""
    return sorted(SourceRefreshTick.objects.values_list("command__cause", flat=True))


def durable_holds():
    """The scheduler's durable send-hold entries (``schedule`` schema)."""
    return list(
        OperationalLog.objects.filter(event="source_refresh_held", schema="schedule")
    )


def held_lines(caplog):
    """The process-log lines a skipped slot writes."""
    return [r for r in caplog.records if r.msg is Event.SOURCE_HELD]


def test_delta_slot_is_skipped_during_a_send_and_created_after_it(
    tmp_path, monkeypatch, caplog
):
    """Only the full slot is created; the delta follows once the send ends."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    sending(monkeypatch)
    producer = SourceProducer(uuid4())
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    with scheduler_session() as guard:
        assert len(producer(guard)) == 1
        assert len(producer(guard)) == 1
    assert causes() == ["nightly"]
    # One INFO line per skipped slot, however many loops skip it.
    (line,) = held_lines(caplog)
    assert line.levelno == logging.INFO
    # A held quick slot writes no durable entry: quick updates no longer
    # decide whether the data is current, so they are no catch-up evidence.
    assert not durable_holds()
    # Nothing records the skip as a failure.
    assert not TaskRun.objects.filter(state__in=("failed", "retry_wait")).exists()
    assert not OperationalIncident.objects.exists()
    sending(monkeypatch, active=False)
    with scheduler_session() as guard:
        assert len(producer(guard)) == 2
    assert causes() == ["delta", "nightly"]


def test_both_slots_are_created_when_no_send_is_active(tmp_path, caplog):
    """With no Family mail at all, the real check lets the delta run."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2
    assert causes() == ["delta", "nightly"]
    assert not held_lines(caplog)


def test_an_existing_delta_slot_is_kept_when_a_send_starts(tmp_path, monkeypatch):
    """A slot created before the send keeps its receipt; nothing is withdrawn."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    with scheduler_session() as guard:
        first = produce_refreshes(guard)
    sending(monkeypatch)
    with scheduler_session() as guard:
        assert produce_refreshes(guard) == first


def test_full_and_manual_refreshes_are_not_skipped(tmp_path, monkeypatch):
    """A quarter-hour full slot and an Admin request still queue their work."""
    with_frequency(tmp_path, "quarter_hour")
    monkeypatch.setattr(production, "delta_held", lambda now: True)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 1
    assert causes() == ["nightly"]
    manual = command()
    assert TaskRun.objects.get(pk=manual.task_root_id).state == "queued"


def test_daytime_full_refresh_waits_for_a_send_and_catches_up_after(
    tmp_path, monkeypatch, caplog
):
    """A full refresh at a listed daytime time is held like a delta (#465).

    Nothing is created while the send runs; each held slot logs one INFO
    line; the first loop after the send creates both slots, and the full one
    records the daytime time it was due at.
    """
    with_schedule(tmp_path, full_refresh_times=["02:00", "12:00"])
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    monkeypatch.setattr(production, "delta_held", lambda now: True)
    producer = SourceProducer(uuid4())
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    with scheduler_session() as guard:
        assert producer(guard) == ()
        assert producer(guard) == ()
    assert causes() == []
    assert len(held_lines(caplog)) == 2
    # The held full slot also wrote one durable INFO entry (#510), with the
    # task-free schedule schema, correlated to the slot's would-be command.
    (entry,) = durable_holds()
    assert (entry.level, entry.schema, entry.context) == ("INFO", "schedule", {})
    # A restarted scheduler may record the same held slot again: harmless.
    with scheduler_session() as guard:
        assert SourceProducer(uuid4())(guard) == ()
    assert len(durable_holds()) == 2
    assert not TaskRun.objects.filter(task_type="source_refresh").exists()
    monkeypatch.setattr(production, "delta_held", lambda now: False)
    with scheduler_session() as guard:
        assert len(producer(guard)) == 2
    assert causes() == ["delta", "nightly"]
    tick = SourceRefreshTick.objects.get(command__cause="nightly")
    assert tick.nightly_time == "12:00"
    # Each durable entry is correlated to the held slot's command identity.
    assert {entry.correlation_id for entry in durable_holds()} == {tick.command_id}


def test_nightly_full_refresh_still_runs_during_a_send(tmp_path, monkeypatch):
    """The listed nightly time is the one full refresh a send never holds."""
    with_schedule(tmp_path, full_refresh_times=["02:00", "20:00"])
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    monkeypatch.setattr(production, "delta_held", lambda now: True)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 1
    assert causes() == ["nightly"]
    assert SourceRefreshTick.objects.get().nightly_time == "02:00"


def test_without_promoted_source_nothing_is_skipped(tmp_path, monkeypatch):
    """Before the first promotion there is no current data to protect."""
    configured(tmp_path)
    sending(monkeypatch)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2


def test_skipping_ends_before_the_send_allowance_runs_out(tmp_path, monkeypatch):
    """A long send gets its refreshes back in time for a full one to promote.

    The resume point is measured from the overdue full slot (#510), the
    first scheduled full time after the newest full refresh started, not
    from the newest snapshot of any kind.
    """
    credential, *_ = configured(tmp_path)
    publish(credential)
    sending(monkeypatch)
    overdue = overdue_slot()
    with transaction.atomic():
        now = database_now()
        stale = timedelta(seconds=configured_policy().source_stale_seconds)
        limit = stale + send_hold.SEND_ALLOWANCE - send_hold.RESUME_LEAD
        # Nothing is overdue yet: the data is current, so holding is safe.
        assert send_hold.delta_held(now)
        assert send_hold.delta_held(overdue + limit - timedelta(seconds=1))
        assert not send_hold.delta_held(overdue + limit)


def quarter_hourly(tmp_path):
    """A full refresh every UTC quarter hour: the overdue slot is minutes away."""
    credential, *_ = with_frequency(tmp_path, "quarter_hour")
    return credential


def read_earlier(monkeypatch, minutes):
    """Pretend the newest full refresh started ``minutes`` ago.

    The schedule is treated as in effect since a day earlier, so its due
    times since then count, and the overdue slot lies before requests made
    now. Returns that slot.
    """
    real = data_age.schedule_runs
    with transaction.atomic():
        started = database_now() - timedelta(minutes=minutes)
    for module in (health, data_age):
        monkeypatch.setattr(module, "last_full_started_at", lambda: started)
    monkeypatch.setattr(
        data_age,
        "schedule_runs",
        lambda after: [
            (start - timedelta(days=1), value) for start, value in real(after)
        ],
    )
    return overdue_slot()


def test_stale_alarm_holds_within_the_send_allowance(tmp_path, monkeypatch, settings):
    """Held refreshes age the data on purpose: no alarm, no resolution."""
    skipping_deltas(quarter_hourly(tmp_path))
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch)
    with monkeypatch.context() as patch:
        future_observation(patch, stale_offset())
        observe()
    assert not OperationalIncident.objects.exists()
    # Past the allowance the alarm sounds even during a send.
    allowance = int(send_hold.SEND_ALLOWANCE.total_seconds())
    with monkeypatch.context() as patch:
        future_observation(patch, stale_offset(1 + allowance))
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


def test_stale_alarm_is_unchanged_without_a_send(tmp_path, monkeypatch, settings):
    """With no send in progress, a late full refresh alarms at the margin."""
    skipping_deltas(quarter_hourly(tmp_path))
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch, active=False)
    with monkeypatch.context() as patch:
        future_observation(patch, stale_offset())
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


def test_a_held_sample_does_not_resolve_an_open_stale_alarm(
    tmp_path, monkeypatch, settings
):
    """An alarm raised before the send stays open until a refresh succeeds."""
    skipping_deltas(quarter_hourly(tmp_path))
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    with monkeypatch.context() as patch:
        future_observation(patch, stale_offset())
        sending(patch, active=False)
        observe()
        sending(patch)
        observe()
    assert OperationalIncident.objects.get(kind="source_stale").resolved_at is None


@pytest.mark.parametrize("deltas", ["requested", "unused"])
def test_stale_alarm_is_unchanged_during_a_send_when_nothing_was_skipped(
    tmp_path, monkeypatch, settings, deltas
):
    """A refresh requested but not promoted, or no schedule in use, still alarms.

    "requested": a scheduled refresh was asked for at or after the overdue
    slot's due time and well before the resume point, and has not promoted
    (a failing or stuck refresh). "unused": no scheduled refresh was
    requested within the last day.
    """
    credential = quarter_hourly(tmp_path)
    if deltas == "requested":
        skipping_deltas(credential)
        overdue = read_earlier(monkeypatch, 40)
        command(cause="delta", actor_id=None)
    else:
        publish(credential)
        overdue = overdue_slot()
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch)
    with monkeypatch.context() as patch:
        at_instant(patch, overdue + timedelta(seconds=121))
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


def test_catch_up_delta_keeps_the_alarm_held_until_the_allowance_ends(
    tmp_path, monkeypatch, settings
):
    """The scheduler's own resume-point request does not sound a false alarm.

    The resume point is moved back to the overdue slot's due time, so a
    request made after it is the catch-up, as one made when holding ends
    would be. A sample then still holds; one past the allowance alarms.
    """
    skipping_deltas(quarter_hourly(tmp_path))
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    monkeypatch.setattr(
        send_hold, "RESUME_LEAD", send_hold.SEND_ALLOWANCE + timedelta(seconds=120)
    )
    overdue = read_earlier(monkeypatch, 40)
    command(cause="delta", actor_id=None)
    sending(monkeypatch)
    with monkeypatch.context() as patch:
        at_instant(patch, overdue + timedelta(seconds=121))
        observe()
    assert not OperationalIncident.objects.exists()
    allowance = send_hold.SEND_ALLOWANCE + timedelta(seconds=121)
    with monkeypatch.context() as patch:
        at_instant(patch, overdue + allowance)
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


@contextmanager
def shadow_work(mode="production", paused=False):
    """Temporary message and Task tables with just the columns the check reads.

    Temporary tables are searched before ``public``, so the real statements
    run against them, without building real sends. The runtime and campaign
    copies hold one current campaign in ``mode``, ``paused`` or not.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE stewardship_campaign "
            "(id uuid PRIMARY KEY, delivery_paused boolean)"
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_system_configuration "
            "(id uuid PRIMARY KEY, mode text, current_campaign_id uuid)"
        )
        campaign = uuid4()
        cursor.execute(
            "INSERT INTO pg_temp.stewardship_campaign VALUES (%s,%s)",
            [campaign, paused],
        )
        cursor.execute(
            "INSERT INTO pg_temp.stewardship_system_configuration "
            "VALUES (gen_random_uuid(),%s,%s)",
            [mode, campaign],
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_outbox_message "
            "(id uuid PRIMARY KEY, purpose text, state text, pause_hold_id uuid, "
            "semantic_key uuid, not_before timestamptz DEFAULT statement_timestamp(), "
            "task_id uuid)"
        )
        # Each message's occurrence, for whether it is due yet (BG-12).
        cursor.execute(
            "CREATE TEMP TABLE stewardship_schedule_occurrence "
            "(id uuid PRIMARY KEY, due_at timestamptz)"
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_task_run "
            "(id uuid PRIMARY KEY, task_type text, state text, "
            "not_before timestamptz DEFAULT statement_timestamp())"
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE pg_temp.stewardship_outbox_message")
            cursor.execute("DROP TABLE pg_temp.stewardship_schedule_occurrence")
            cursor.execute("DROP TABLE pg_temp.stewardship_task_run")
            cursor.execute("DROP TABLE pg_temp.stewardship_system_configuration")
            cursor.execute("DROP TABLE pg_temp.stewardship_campaign")


def add(table, rows, retry_in=timedelta(0), deferred=None):
    """Insert ``rows`` of (second column, state[, paused[, due]]) into a shadow table.

    A message's occurrence fell due a minute ago, or with ``due`` False
    falls due in an hour (a reminder prepared ahead, BG-12). Every row's
    retry time (``not_before``) is ``retry_in`` from now. Each message gets
    its delivery Task, waiting to retry with it; with ``deferred`` a sending
    limit put that Task off for that long while the message stays as it is.
    """
    with connection.cursor() as cursor:
        for row in rows:
            if table == "message":
                purpose, state, paused, *rest = row
                occurrence = uuid4()
                cursor.execute(
                    "INSERT INTO pg_temp.stewardship_schedule_occurrence "
                    "VALUES (%s,statement_timestamp()+%s)",
                    [
                        occurrence,
                        timedelta(minutes=-1)
                        if rest == [] or rest[0]
                        else timedelta(hours=1),
                    ],
                )
                task = uuid4()
                held = deferred is not None or state == "retry_wait"
                cursor.execute(
                    "INSERT INTO pg_temp.stewardship_task_run "
                    "VALUES (%s,'outbox_delivery',%s,statement_timestamp()+%s)",
                    [task, "retry_wait" if held else "queued", deferred or retry_in],
                )
                cursor.execute(
                    "INSERT INTO pg_temp.stewardship_outbox_message "
                    "VALUES (gen_random_uuid(),%s,%s,%s,%s,"
                    "statement_timestamp()+%s,%s)",
                    [
                        purpose,
                        state,
                        uuid4() if paused else None,
                        occurrence,
                        retry_in,
                        task,
                    ],
                )
            else:
                task_type, state = row
                cursor.execute(
                    "INSERT INTO pg_temp.stewardship_task_run "
                    "VALUES (gen_random_uuid(),%s,%s,statement_timestamp()+%s)",
                    [task_type, state, retry_in],
                )


def test_send_activity_counts_remaining_family_work():
    """Remaining Family messages and preparations count; settled or paused do not."""
    assert send_hold.PREPARATION_TASK_TYPE == PREPARE
    with transaction.atomic(), shadow_work():
        assert not send_hold.family_send_active()
        # Settled, paused and non-Family mail, and other Tasks, are not a send.
        add(
            "message",
            [("initial", "delivered", False)] * 20
            + [("reminder", "pending", True)] * 20
            + [("receipt", "pending", False)] * 20
            + [("daily_digest", "retry_wait", False)] * 20,
        )
        add("task", [(PREPARE, "succeeded")] * 20 + [("source_refresh", "queued")] * 20)
        assert not send_hold.family_send_active()
        # A send's tail below the minimum lets refreshes run.
        add(
            "message",
            [
                ("initial", "pending", False),
                ("initial", "retry_wait", False),
                ("reminder", "submitting", False),
            ],
        )
        add(
            "task", [(PREPARE, "queued"), (PREPARE, "running"), (PREPARE, "retry_wait")]
        )
        assert not send_hold.family_send_active()
        assert send_hold.family_send_active(minimum=6)
        add("message", [("initial", "pending", False)] * 4)
        assert send_hold.family_send_active()


def test_messages_not_yet_due_do_not_hold_deltas():
    """A reminder prepared ahead (BG-12) counts only once it is due."""
    with transaction.atomic(), shadow_work():
        add("message", [("reminder", "pending", False, False)] * 20)
        assert not send_hold.family_send_active()
        add("message", [("reminder", "pending", False, True)] * 10)
        assert send_hold.family_send_active()


def test_long_retry_waits_do_not_hold_deltas():
    """Throttled mail waiting hours to retry is not a send in progress (#868).

    Ten or more Families the provider throttled wait 15 minutes to 12 hours
    between attempts; they count again only once their retry is near.
    """
    with transaction.atomic(), shadow_work():
        add("message", [("reminder", "retry_wait", False)] * 20, timedelta(hours=4))
        add("task", [(PREPARE, "retry_wait")] * 20, timedelta(hours=1))
        assert not send_hold.family_send_active()
        # Pending and submitting work counts wherever its retry time is.
        add("message", [("reminder", "submitting", False)] * 9, timedelta(hours=4))
        assert not send_hold.family_send_active()
        add("message", [("reminder", "retry_wait", False)], send_hold.RETRY_SOON)
        assert send_hold.family_send_active()


def test_a_send_stopped_by_a_sending_limit_does_not_hold_deltas():
    """A bulk send at the daily cap leaves deltas running until it lifts (#868).

    Over our own daily cap or the provider's mailbox-wide limit, the
    delivery Task is put off and the message stays pending.
    """
    with transaction.atomic(), shadow_work():
        add(
            "message",
            [("initial", "pending", False)] * 30,
            deferred=timedelta(minutes=15),
        )
        assert not send_hold.family_send_active()
        # A deferral about to end, like a short limit wait, is still sending.
        add(
            "message",
            [("initial", "pending", False)] * 10,
            deferred=timedelta(minutes=5),
        )
        assert send_hold.family_send_active()


def test_throttled_stragglers_do_not_hold_quick_slots(tmp_path, monkeypatch):
    """Twelve throttled Families waiting an hour leave quick slots unheld.

    ``delta_held`` is the scheduler's decision for a quick slot (see the
    first test here for how it creates or skips the slot). The shadow tables
    replace the runtime configuration the data-age read needs, so that read
    is taken first, against the real tables, and pinned.
    """
    credential, *_ = configured(tmp_path)
    publish(credential)
    with transaction.atomic():
        now = database_now()
        overdue = send_hold.current_overdue(now)
    assert overdue[1] is not None
    monkeypatch.setattr(send_hold, "current_overdue", lambda _now: overdue)
    with transaction.atomic(), shadow_work():
        add("message", [("initial", "retry_wait", False)] * 12, timedelta(hours=1))
        assert not send_hold.delta_held(now)
        # The same Families about to be retried are a send in progress.
        add("message", [("initial", "retry_wait", False)] * 10, timedelta(minutes=1))
        assert send_hold.delta_held(now)


@pytest.mark.parametrize(
    "mode,paused,active",
    [
        ("production", True, False),
        ("production", False, True),
        ("testing", True, True),
    ],
)
def test_paused_production_counts_messages_only(mode, paused, active):
    """Preparations held by a Production pause do not keep deltas skipped."""
    with transaction.atomic(), shadow_work(mode, paused):
        add("task", [(PREPARE, "queued")] * 20)
        assert send_hold.family_send_active() is active
        # Unpaused messages still count during the pause.
        add("message", [("initial", "pending", False)] * 10)
        assert send_hold.family_send_active()


@pytest.mark.parametrize(
    "role", [ServiceRole.SCHEDULER, ServiceRole.WORKER, ServiceRole.WEB]
)
def test_restricted_logins_can_read_the_send_check(tmp_path, monkeypatch, role):
    """The scheduler decides skips and the collector's worker samples health."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    with task_login(role), transaction.atomic():
        assert not send_hold.family_send_active(minimum=1)
        assert not send_hold.deltas_skipped(database_now(), database_now())
        if role is ServiceRole.SCHEDULER:
            # With a send reported, the current source's age is read too.
            monkeypatch.setattr(send_hold, "family_send_active", lambda: True)
            assert send_hold.delta_held(database_now())


def test_a_go_live_hold_moves_the_stale_alarm_to_its_end(
    tmp_path, monkeypatch, settings
):
    """A full slot due during a go-live hold counts from the hold's end (#462).

    The go-live attempt is stood in for by moving the overdue point, as
    ``go_live_sequencing.held_until`` does for a slot inside a hold; the
    attempt arithmetic itself is tested with real attempts in
    test_go_live_sequencing_postgresql.py.
    """
    skipping_deltas(quarter_hourly(tmp_path))
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch, active=False)
    hold = timedelta(hours=1)
    monkeypatch.setattr(health, "held_until", lambda overdue: overdue + hold)
    with monkeypatch.context() as patch:
        future_observation(patch, stale_offset())
        observe()
    assert not OperationalIncident.objects.exists()
    with monkeypatch.context() as patch:
        future_observation(patch, stale_offset(1 + int(hold.total_seconds())))
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")
