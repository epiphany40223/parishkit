"""The integrated refresh schedule under the real scheduler and tick guard (#632).

More than eight full times and daylight-saving days pass the database's
refresh-tick guard exactly where the scheduler puts them, and nowhere else;
the schedule-change catch-up is admitted only at the instant the current
schedule took effect; and the guard's schedule normalization agrees with
``cadence.schedule_identity``. Quick updates were removed (#1147): a stored
schedule's quick times create no tick.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns import work_locks
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source import data_age, production
from parishkit.stewardship.source.cadence import catch_up_slot, schedule_identity
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.production import produce_refreshes
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshCommand,
    SourceRefreshTick,
)
from parishkit.stewardship.source.refresh_rules import stored_settings

from .campaign_builders import change
from .test_refresh_frequency_postgresql import (
    forged_tick,
    scope_of,
    with_schedule,
    with_stored_schedule,
)
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2026, 9, 11, 17, 23, 42, tzinfo=UTC)
# Thirteen full refreshes (00:00 and every hour from 08:00 to 20:00).
BUSY = stored_settings(
    {
        "rules": [
            {"kind": "full", "at": "00:00"},
            {"kind": "full", "every": 60, "from": "08:00", "to": "20:00"},
        ],
        "skips": [],
        "skip_around_family_emails": False,
    }
)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def produce(monkeypatch, instant):
    """Run one scheduler loop as if the database clock read ``instant``."""
    monkeypatch.setattr(production, "database_now", lambda: instant)
    with scheduler_session() as guard:
        return produce_refreshes(guard)


def local(tick):
    """The tick's due time as parish ``HH:MM``."""
    value = tick.due_at.astimezone(ZoneInfo(tick.timezone))
    return f"{value.hour:02d}:{value.minute:02d}"


def forge(tick, cause, slot):
    """Try to insert a scheduler tick for ``slot`` under a new ``cause`` command."""
    with scheduler_session(), work_transaction():
        receipt = command(cause=cause, actor_id=None)
        SourceRefreshTick.objects.create(
            command_id=receipt.command_id,
            **forged_tick(
                tick,
                due_at=slot.due_at,
                slot_key=slot.slot_key,
                nightly_time=slot.nightly_time,
            ),
        )


def test_more_than_eight_full_times_pass_the_guard(tmp_path, monkeypatch):
    """At 13:23 EDT the 13:00 full refresh is due, and it alone runs."""
    with_schedule(tmp_path, **BUSY)
    assert len(produce(monkeypatch, NOW)) == 1
    (full,) = SourceRefreshTick.objects.all()
    assert full.command.cause == "nightly"
    assert (full.nightly_time, local(full)) == ("13:00", "13:00")


def test_a_stored_schedules_quick_times_create_no_tick(tmp_path, monkeypatch):
    """A schedule stored before #1147 with listed quick times runs only full.

    Such a version is carried unchanged (a new one may not list quick
    times), so it is applied as a stored one (``with_stored_schedule``).
    """
    rules = BUSY["refresh_rules"]
    stored = BUSY | {
        "refresh_rules": rules
        | {
            "rules": [
                *rules["rules"],
                {"kind": "quick", "every": 15, "from": "06:00", "to": "07:45"},
            ]
        },
        "delta_refresh": "times",
        "quick_refresh_times": ["06:00", "06:15", "06:30", "06:45", "07:00"],
    }
    with_stored_schedule(tmp_path, **stored)
    morning = datetime(2026, 9, 11, 11, 40, tzinfo=UTC)
    assert len(produce(monkeypatch, morning)) == 1
    assert set(SourceRefreshTick.objects.values_list("command__cause", flat=True)) == {
        "nightly"
    }


@pytest.mark.parametrize(
    "instant,full",
    [
        # Spring forward, 2026-03-08: 02:00–02:59 EST do not exist.
        (datetime(2026, 3, 8, 7, 5, tzinfo=UTC), "00:00"),
        # Fall back, 2025-11-02: the repeated hour runs once.
        (datetime(2025, 11, 2, 6, 40, tzinfo=UTC), "00:00"),
    ],
)
def test_daylight_saving_days_pass_the_guard(tmp_path, monkeypatch, instant, full):
    """Full times around the missing or repeated hour resolve as the scheduler says."""
    settings = stored_settings(
        {
            "rules": [
                {"kind": "full", "at": "00:00"},
                {"kind": "full", "every": 15, "from": "01:00", "to": "03:00"},
            ],
            "skips": [],
            "skip_around_family_emails": False,
        }
    )
    with_schedule(tmp_path, **settings)
    assert len(produce(monkeypatch, instant)) == 1
    (tick,) = SourceRefreshTick.objects.all()
    assert tick.command.cause == "nightly"
    assert settings["nightly_time"] == full


def schedule_change(tmp_path, monkeypatch, *, new):
    """Apply the old three-hourly schedule, then ``new``, with an old slot overdue.

    The real activations decide the instant the new schedule took effect;
    the newest full refresh is placed three hours earlier, under the old
    schedule, so one of its full slots was already overdue then, as the
    10:00 slot is in the operations spec's 10:05 example.
    """
    old = {
        "nightly_time": "00:00",
        # Every three hours (eight times, the old limit), so a slot falls
        # due in any three hours.
        "full_refresh_times": [f"{hour:02d}:00" for hour in range(0, 24, 3)],
        "delta_refresh": "quarter_hour",
    }
    _, store, _, actor = with_schedule(tmp_path, **old)
    current = store.active()
    integration = current.document()["sections"]["integrations"][0]
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": integration["id"],
            "values": {"settings": integration["values"]["settings"] | new},
        }
    ]
    assert change(store, current, actor, patch).state == "applied"
    with transaction.atomic():
        real = data_age.schedule_runs(database_now() - timedelta(days=1))
    effective = real[-1][0]
    # The old schedule's run, moved a day back so its 3-hourly slots fall
    # between the last full refresh and the change.
    monkeypatch.setattr(
        data_age,
        "schedule_runs",
        lambda after: [(effective - timedelta(days=1), real[-2][1]), real[-1]],
    )
    monkeypatch.setattr(
        data_age, "last_full_started_at", lambda: effective - timedelta(hours=3)
    )
    return effective


NEW_DEFAULT = stored_settings(
    {
        "rules": [{"kind": "full", "at": "02:00"}],
        "skips": [],
        "skip_around_family_emails": False,
    }
)


def test_a_schedule_change_requests_one_catch_up_when_a_slot_was_overdue(
    tmp_path, monkeypatch
):
    """The operations spec's 10:05 example, at the real activation instant.

    While a send holds it, the catch-up is held like a daytime full slot and
    leaves durable evidence; once the send ends the next loop requests it,
    due when the new schedule took effect, and SQL admits it. Later loops
    find the same slot.
    """
    effective = schedule_change(tmp_path, monkeypatch, new=NEW_DEFAULT)
    with transaction.atomic():
        now = database_now()
    monkeypatch.setattr(production, "daytime_held", lambda instant: True)
    produce(monkeypatch, now)
    assert not SourceRefreshCommand.objects.filter(cause="catch_up").exists()
    assert OperationalLog.objects.filter(
        event="source_refresh_held", schema="schedule"
    ).exists()
    monkeypatch.setattr(production, "daytime_held", lambda instant: False)
    receipts = produce(monkeypatch, now)
    tick = SourceRefreshTick.objects.select_related("command").get(
        command__cause="catch_up"
    )
    assert tick.due_at == effective.replace(microsecond=0)
    assert tick.nightly_time == "02:00"
    assert tick.command.kind == "full"
    assert receipts == produce(monkeypatch, now)
    assert SourceRefreshCommand.objects.filter(cause="catch_up").count() == 1
    # Under another scope (say, a new campaign window) the catch-up at that
    # instant already exists, so none is requested for the guard to refuse.
    with transaction.atomic():
        assert production._catch_up(effective, tick.timezone, "f" * 64) is None
        assert (
            production._catch_up(effective, tick.timezone, scope_of(tick)) is not None
        )


def test_an_idle_loop_still_requests_a_pending_catch_up(tmp_path, monkeypatch):
    """Every regular slot has its refresh, yet the catch-up is still owed.

    The first loop runs without the catch-up (as if the change had not yet
    taken effect), so the regular slots are ticked. The next loop must not
    take the idle shortcut: the catch-up is one of the current slots, and
    the locked pass requests it (#715). The loop after that has nothing to
    do, takes no work-order lock and returns the same receipts.
    """
    effective = schedule_change(tmp_path, monkeypatch, new=NEW_DEFAULT)
    with transaction.atomic():
        now = database_now()
    with monkeypatch.context() as early:
        early.setattr(production, "_catch_up_at", lambda: None)
        produce(monkeypatch, now)
    assert not SourceRefreshCommand.objects.filter(cause="catch_up").exists()
    taken, real = [], work_locks.lock_work_order

    def counting():
        """Count the lock, then take the real one."""
        taken.append(True)
        return real()

    monkeypatch.setattr(work_locks, "lock_work_order", counting)
    receipts = produce(monkeypatch, now)
    assert taken
    tick = SourceRefreshTick.objects.get(command__cause="catch_up")
    assert tick.due_at == effective.replace(microsecond=0)
    assert tick.command_id in {receipt.command_id for receipt in receipts}
    taken.clear()
    assert produce(monkeypatch, now) == receipts
    assert taken == []


def test_no_catch_up_without_an_overdue_slot(tmp_path, monkeypatch):
    """When the last full refresh is newer than every old due time, none is needed."""
    effective = schedule_change(tmp_path, monkeypatch, new=NEW_DEFAULT)
    monkeypatch.setattr(
        data_age, "last_full_started_at", lambda: effective - timedelta(seconds=1)
    )
    with transaction.atomic():
        now = database_now()
    produce(monkeypatch, now)
    assert not SourceRefreshCommand.objects.filter(cause="catch_up").exists()


def test_the_guard_admits_a_catch_up_only_when_the_schedule_took_effect(
    tmp_path, monkeypatch
):
    """A catch-up due at any other instant, or with another time, is refused."""
    effective = schedule_change(tmp_path, monkeypatch, new=NEW_DEFAULT)
    with transaction.atomic():
        now = database_now()
    produce(monkeypatch, now)
    tick = SourceRefreshTick.objects.select_related("command__request").get(
        command__cause="nightly"
    )
    early = catch_up_slot(
        effective_at=effective - timedelta(seconds=1),
        timezone=tick.timezone,
        scope_fingerprint=scope_of(tick),
    )
    with pytest.raises(IntegrityError, match="Catch-up tick must be due"):
        forge(tick, "catch_up", early)
    # The right instant but the wrong recorded time is refused too.
    exact = catch_up_slot(
        effective_at=effective, timezone=tick.timezone, scope_fingerprint=scope_of(tick)
    )
    with pytest.raises(IntegrityError, match="applied cadence"):
        forge(tick, "catch_up", replace(exact, nightly_time="03:00"))


def test_a_schedule_that_never_changed_has_no_catch_up(tmp_path, monkeypatch):
    """The guard refuses a catch-up when every activation has the same schedule."""
    with_schedule(tmp_path)
    produce(monkeypatch, NOW)
    tick = SourceRefreshTick.objects.select_related("command__request").get(
        command__cause="nightly"
    )
    with transaction.atomic():
        now = database_now()
    slot = catch_up_slot(
        effective_at=now - timedelta(minutes=1),
        timezone=tick.timezone,
        scope_fingerprint=scope_of(tick),
    )
    with pytest.raises(IntegrityError, match="Catch-up tick must be due"):
        forge(tick, "catch_up", slot)


@pytest.mark.parametrize(
    "first,second",
    [
        ({}, {"nightly_time": "02:00"}),
        ({}, {"full_refresh_times": ["02:00"]}),
        ({}, {"delta_refresh": "quarter_hour", "full_refresh": "daily"}),
        ({"nightly_time": "03:00"}, {"full_refresh_times": ["03:00"]}),
        ({}, {"delta_refresh": "hourly"}),
        ({}, {"nightly_time": "03:00", "full_refresh_times": ["03:00"]}),
        (NEW_DEFAULT, NEW_DEFAULT | {"refresh_rules": BUSY["refresh_rules"]}),
        (NEW_DEFAULT, BUSY),
        ({"delta_refresh": "off"}, NEW_DEFAULT | {"quick_refresh_times": []}),
        ({}, {"delta_refresh": "off"}),
        ({"quick_refresh_times": ["06:00"]}, {}),
        ({"organization_id": "1"}, {"organization_id": "2"}),
    ],
)
def test_the_guard_normalizes_a_schedule_as_the_scheduler_does(first, second):
    """The SQL normalization and ``schedule_identity`` agree on equality.

    The guard still compares the removed quick settings (#1147, no
    migration), so ``schedule_identity`` does too.
    """
    import json

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_refresh_schedule_v1(%s::jsonb)"
            " = stewardship_refresh_schedule_v1(%s::jsonb)",
            [json.dumps(first), json.dumps(second)],
        )
        (equal,) = cursor.fetchone()
    assert equal == (schedule_identity(first) == schedule_identity(second))


def test_the_scheduler_login_requests_and_inserts_the_catch_up(tmp_path, monkeypatch):
    """The guard's activation walk and normalization run under the scheduler login."""
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    effective = schedule_change(tmp_path, monkeypatch, new=NEW_DEFAULT)
    with transaction.atomic():
        now = database_now()
    monkeypatch.setattr(production, "database_now", lambda: now)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        produce_refreshes(guard)
    tick = SourceRefreshTick.objects.get(command__cause="catch_up")
    assert tick.due_at == effective.replace(microsecond=0)


def test_a_refused_catch_up_keeps_the_loops_other_slots(tmp_path, monkeypatch, caplog):
    """A guard refusal rolls back only the catch-up and is logged once.

    Here the catch-up is decided (before the work-order lock) at an instant
    the guard does not accept, as if the activations moved in between. The
    refusals and the holds are remembered apart: refused twice (one
    WARNING), then held by a send (its hold is still recorded), then refused
    again (logged again, since the hold came in between).
    """
    import logging

    from parishkit.stewardship.observability import FailureKind

    effective = schedule_change(tmp_path, monkeypatch, new=NEW_DEFAULT)
    monkeypatch.setattr(
        production, "_catch_up_at", lambda: effective - timedelta(seconds=1)
    )
    with transaction.atomic():
        now = database_now()
    monkeypatch.setattr(production, "database_now", lambda: now)
    producer = production.SourceProducer(uuid4())
    caplog.set_level(logging.WARNING)
    for held in (False, False, True, False):
        monkeypatch.setattr(production, "daytime_held", lambda instant, h=held: h)
        with scheduler_session() as guard:
            produce_refreshes(guard, skipped=producer.skipped, refused=producer.refused)
    assert not SourceRefreshCommand.objects.filter(cause="catch_up").exists()
    assert set(SourceRefreshTick.objects.values_list("command__cause", flat=True)) == {
        "nightly"
    }
    refusals = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and FailureKind.REFRESH_CATCH_UP_REFUSED in str(record.__dict__)
    ]
    assert len(refusals) == 2
    # Not worded as a failed refresh.
    assert not any("source_refresh_invalid" in str(r.msg) for r in caplog.records)
    slot = catch_up_slot(
        effective_at=effective - timedelta(seconds=1),
        timezone=SourceRefreshTick.objects.get(command__cause="nightly").timezone,
        scope_fingerprint=scope_of(
            SourceRefreshTick.objects.select_related("command__request").get(
                command__cause="nightly"
            )
        ),
    )
    assert (
        OperationalLog.objects.filter(
            event="source_refresh_held",
            schema="schedule",
            correlation_id=slot.command_id,
        ).count()
        == 1
    )
