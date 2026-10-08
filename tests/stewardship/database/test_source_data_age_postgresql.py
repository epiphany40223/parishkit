"""Data age, the bulk-send hold's allowance and the catch-up evidence (#510).

Each scenario runs on a virtual day three days after the real schedule was
applied, so every due time on it counts. The inputs that carry their own
times are placed on that day: the newest full refresh's start, the scheduled
refresh requests (a temporary table with just the columns the hold reads,
searched before ``public``), and the scheduler's durable send-hold entries
and failures (operational log rows with explicit times). Everything else is
the real configuration, schedule reader and health check.

Full refreshes run at 00:00 and every two hours from 08:00 to 20:00 parish
time; the margin is the default 30 minutes, so the allowance ends at 12:30
and the resume point is 12:00 for an overdue 10:00 slot.
"""

from dataclasses import replace
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, transaction

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.source import (
    data_age,
    health,
    production,
    send_hold,
    snapshots,
)
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.cadence import RefreshSlot
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.leases import acquire_source
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)

from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_refresh_frequency_postgresql import with_schedule
from .test_source_attempts_postgresql import configured
from .test_source_health_postgresql import publish
from .test_source_requests_postgresql import claim, command

pytestmark = pytest.mark.django_db(transaction=True)
FULL_TIMES = ["00:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]
# A full refresh starts a few seconds after its due time.
READ = timedelta(seconds=5)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


@pytest.fixture
def day(tmp_path):
    """``day(quick)`` applies the schedule and returns ``at`` on the virtual day.

    ``quick`` is the quick-update setting (every 15 minutes by default);
    ``at(hour, minute, second)`` is that parish time on the virtual day.
    """

    def start(quick="quarter_hour", times=FULL_TIMES):
        """Apply the schedule with ``quick``; return the virtual day's clock."""
        start.configured = with_schedule(
            tmp_path,
            nightly_time=times[0],
            full_refresh_times=times,
            delta_refresh=quick,
        )
        with transaction.atomic():
            active = SystemConfiguration.objects.values_list(
                "active_configuration_id", flat=True
            ).get()
            zone = data_age.source_timezone(active)
            today = database_now().astimezone(ZoneInfo(zone)).date()
        virtual = today + timedelta(days=3)

        def at(hour, minute=0, second=0):
            """The instant of ``hour:minute:second`` parish time that day."""
            return resolve_local(
                datetime.combine(virtual, time(hour, minute, second)), zone
            )

        return at

    return start


@pytest.fixture
def scheduled():
    """Scheduled refresh requests at chosen times: ``add(cause, instant)``.

    A temporary table shadows the real one for the hold's two reads (cause
    and creation time), as the send check's tests shadow message tables.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE stewardship_source_refresh_command "
            "(id uuid PRIMARY KEY, cause text, created_at timestamptz)"
        )

    def add(cause, instant):
        """Record one request made at ``instant``."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO pg_temp.stewardship_source_refresh_command "
                "VALUES (%s,%s,%s)",
                [uuid4(), cause, instant],
            )

    yield add
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE pg_temp.stewardship_source_refresh_command")


def morning(at, add, *, quick=True, until=(9, 30)):
    """The requests of a normal morning up to ``until``.

    The 00:00 and 08:00 full refreshes and, with ``quick``, every quarter
    hour's quick update except where a full refresh runs.
    """
    add("nightly", at(0) + timedelta(seconds=1))
    add("nightly", at(8) + timedelta(seconds=1))
    if quick:
        instant = at(0, 15)
        while instant <= at(*until):
            if instant != at(8):
                add("delta", instant + timedelta(seconds=1))
            instant += timedelta(minutes=15)


def held_entry(instant, schema="schedule"):
    """A durable ``source_refresh_held`` INFO entry written at ``instant``.

    The ``schedule`` schema is the scheduler holding a full slot for a send;
    the ``task`` schema is a refresh task's own held retry (lease contention).
    """
    OperationalLog.objects.create(
        event="source_refresh_held",
        level="INFO",
        schema=schema,
        context={} if schema == "schedule" else {"outcome": "retry"},
        created_at=instant,
    )


def sample(monkeypatch, instant, *, last_full, sending):
    """Observe at ``instant``; return whether a ``source_stale`` alarm is open."""
    original = health.require_source_refresh
    with monkeypatch.context() as patch:
        patch.setattr(
            health,
            "require_source_refresh",
            lambda **kwargs: replace(original(**kwargs), instant=instant),
        )
        patch.setattr(health, "last_full_started_at", lambda: last_full)
        patch.setattr(send_hold, "family_send_active", lambda: sending)
        with work_transaction():
            health.observe_source_health()
    return OperationalIncident.objects.filter(
        kind="source_stale", resolved_at__isnull=True
    ).exists()


def test_worked_example_send_ends_before_the_resume_point(day, scheduled, monkeypatch):
    """A send from 09:30 holds the 10:00 full refresh; it ends at 11:00.

    No alarm at 10:30 (nothing was requested from 10:00 on, so the hold's
    allowance applies); when the send ends, the 10:00 catch-up and the 11:00
    quick update are requested, and the catch-up keeps the allowance until
    it promotes at about 11:07. The alarm never sounds.
    """
    at = day()
    morning(at, scheduled)
    held_entry(at(10))
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(10, 30, 1), last_full=last_full, sending=True)
    assert not sample(monkeypatch, at(10, 59), last_full=last_full, sending=True)
    scheduled("nightly", at(11) + READ)
    scheduled("delta", at(11) + READ)
    # The send has ended and the catch-up is running: the durable hold entry
    # is the evidence, without a send in progress.
    assert not sample(monkeypatch, at(11, 3), last_full=last_full, sending=False)
    # Promoted: the overdue slot is now 12:00, not yet due.
    assert not sample(monkeypatch, at(11, 8), last_full=at(11) + READ, sending=False)
    assert not OperationalIncident.objects.exists()


def test_worked_example_send_still_running_at_the_resume_point(
    day, scheduled, monkeypatch
):
    """The hold ends at 12:00; the 12:00 full refresh runs mid-send.

    Requests at or after the resume point are the scheduler's own catch-up,
    so the allowance holds until it promotes, and the 10:00 slot is never
    needed. Without the allowance the alarm would have sounded at 10:30.
    """
    at = day()
    morning(at, scheduled)
    held_entry(at(10))
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(11, 59), last_full=last_full, sending=True)
    scheduled("nightly", at(12) + READ)
    scheduled("delta", at(12) + READ)
    assert not sample(monkeypatch, at(12, 10), last_full=last_full, sending=True)
    assert not sample(monkeypatch, at(12, 29), last_full=last_full, sending=True)
    assert not sample(monkeypatch, at(12, 25), last_full=at(12) + READ, sending=True)
    assert not OperationalIncident.objects.exists()


def test_the_allowance_applies_with_quick_updates_off(day, scheduled, monkeypatch):
    """Held daytime full slots count as held refreshes, not only quick ones."""
    at = day("off")
    morning(at, scheduled, quick=False)
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(10, 31), last_full=last_full, sending=True)
    # Past the allowance the alarm sounds, send or not.
    assert sample(monkeypatch, at(12, 31), last_full=last_full, sending=True)


def test_without_a_send_a_late_full_refresh_alarms_at_the_margin(
    day, scheduled, monkeypatch
):
    """With quick updates off, a missed 10:00 refresh alarms at 10:30."""
    at = day("off")
    morning(at, scheduled, quick=False)
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(10, 29), last_full=last_full, sending=False)
    assert sample(monkeypatch, at(10, 30), last_full=last_full, sending=False)


@pytest.mark.parametrize("lease_retry", [False, True])
def test_an_on_time_full_refresh_that_hangs_gets_no_allowance(
    day, scheduled, monkeypatch, lease_retry
):
    """The 10:00 full refresh was requested on time and never promoted.

    A send that started afterwards does not excuse it, and neither does the
    refresh task's own durable ``source_refresh_held`` entry for waiting on
    the source lease (``task`` schema): only the scheduler's send-hold entry
    is catch-up evidence.
    """
    at = day()
    morning(at, scheduled)
    scheduled("nightly", at(10) + READ)
    if lease_retry:
        held_entry(at(10, 2), schema="task")
    last_full = at(8) + READ
    assert sample(monkeypatch, at(10, 31), last_full=last_full, sending=True)


@pytest.mark.parametrize("sending", [False, True])
def test_a_later_slots_hold_does_not_excuse_an_earlier_slot_that_ran(
    day, scheduled, monkeypatch, sending
):
    """Full refreshes at 08:00 and 08:15: the 08:00 one ran on time and hangs.

    A send then holds the 08:15 full slot, which writes a hold entry after
    the overdue 08:00 slot fell due. Because the 08:00 refresh was requested
    before that entry, it was not held, so the entry is no evidence for it,
    and the alarm sounds at 08:30.
    """
    at = day(times=["00:00", "08:00", "08:15"])
    scheduled("nightly", at(0) + READ)
    scheduled("nightly", at(8) + READ)
    held_entry(at(8, 15))
    last_full = at(0) + READ
    assert not sample(monkeypatch, at(8, 29), last_full=last_full, sending=sending)
    assert sample(monkeypatch, at(8, 31), last_full=last_full, sending=sending)


def test_a_failing_catch_up_alarms_without_the_allowance(day, scheduled, monkeypatch):
    """The catch-up keeps the allowance until it promotes or fails."""
    at = day()
    morning(at, scheduled)
    held_entry(at(10))
    scheduled("nightly", at(11) + READ)
    OperationalLog.objects.create(
        event="source_provider_failed",
        level="CRITICAL",
        schema="task",
        context={"outcome": "failed"},
        created_at=at(11, 5),
    )
    last_full = at(8) + READ
    assert sample(monkeypatch, at(11, 6), last_full=last_full, sending=False)


def test_a_send_ending_just_before_a_scheduler_loop(day, scheduled, monkeypatch):
    """Between the send's end and the next loop nothing is requested yet."""
    at = day()
    morning(at, scheduled)
    held_entry(at(10))
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(10, 59, 45), last_full=last_full, sending=False)


def test_scheduler_downtime_across_a_full_time(day, scheduled, monkeypatch):
    """The 10:00 slot was held; the scheduler was down from 11:00 to 12:10.

    Its first loop creates the latest due slot, 12:00, as the catch-up; the
    10:00 slot's hold entry is still the evidence.
    """
    at = day()
    morning(at, scheduled)
    held_entry(at(10))
    scheduled("nightly", at(12, 10))
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(12, 15), last_full=last_full, sending=False)


def test_a_send_ending_while_the_resume_point_catch_up_runs(
    day, scheduled, monkeypatch
):
    """The send ends at 12:05 while the 12:00 catch-up is still running."""
    at = day()
    morning(at, scheduled)
    held_entry(at(10))
    scheduled("nightly", at(12) + READ)
    scheduled("delta", at(12) + READ)
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(12, 10), last_full=last_full, sending=False)
    # The allowance never extends past its end.
    assert sample(monkeypatch, at(12, 30), last_full=last_full, sending=False)


def test_a_schedule_change_while_a_full_slot_is_overdue_keeps_the_alarm(
    day, scheduled, monkeypatch
):
    """At 10:05 the Administrator switches to the nightly refresh at 02:00.

    The 10:00 slot of the old schedule is already overdue and stays the
    overdue slot, so the alarm still sounds at 10:30; the new schedule's own
    earlier times never count.
    """
    at = day()
    with transaction.atomic():
        before = database_now() - timedelta(minutes=1)
    _, store, _, actor = day.configured
    current = store.active()
    integration = current.document()["sections"]["integrations"][0]
    settings = integration["values"]["settings"] | {
        "nightly_time": "02:00",
        "full_refresh_times": ["02:00"],
        "delta_refresh": "hourly",
    }
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": integration["id"],
            "values": {"settings": settings},
        }
    ]
    assert change(store, current, actor, patch).state == "applied"
    real = data_age.schedule_runs

    def runs(after):
        """The real runs (old schedule, then new), at virtual times."""
        old, new = real(before)[-2:]
        return [(at(0) - timedelta(days=1), old[1]), (at(10, 5), new[1])]

    monkeypatch.setattr(data_age, "schedule_runs", runs)
    morning(at, scheduled)
    last_full = at(8) + READ
    assert not sample(monkeypatch, at(10, 29), last_full=last_full, sending=False)
    assert sample(monkeypatch, at(10, 30), last_full=last_full, sending=False)


@pytest.mark.parametrize("quick", ["quarter_hour", "hourly", "off"])
def test_the_resume_point_does_not_depend_on_quick_updates(day, monkeypatch, quick):
    """The scheduler holds until 12:00 for an overdue 10:00 slot, whatever quick is.

    Before 10:00 nothing is overdue and the data is current, so holding is
    safe; quick updates no longer move the point.
    """
    at = day(quick)
    monkeypatch.setattr(data_age, "last_full_started_at", lambda: at(8) + READ)
    monkeypatch.setattr(send_hold, "family_send_active", lambda: True)
    with transaction.atomic():
        assert send_hold.delta_held(at(9, 45))
        assert send_hold.delta_held(at(11, 59, 59))
        assert not send_hold.delta_held(at(12))
    monkeypatch.setattr(send_hold, "family_send_active", lambda: False)
    with transaction.atomic():
        assert not send_hold.delta_held(at(9, 45))


def test_the_worker_reads_the_catch_up_evidence_and_the_digest_facts(day):
    """The health check filters on the entry's schema; the worker may read it.

    ``SELECT (schema)`` on the operational log is the worker's new column
    grant (#510); the digest's data-age facts read the same columns.
    """
    at = day()
    held_entry(at(10))
    held_entry(at(10, 1), schema="task")
    with task_login(ServiceRole.WORKER), transaction.atomic():
        assert send_hold.catch_up_held(at(10))
        assert not send_hold.catch_up_held(at(10, 0, 1))
        age = data_age.data_age_at(database_now())
    assert age.connection.state == "unknown" and age.data_as_of is None


def failed_attempt(credential):
    """One refresh attempt that called ParishSoft and failed (a 503, retrying)."""
    execution = claim(command())
    with execution.effect():
        lease = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )
    attempt = begin_refresh_attempt(execution, lease, credential)
    result = settle_failed_read(
        execution, ParishSoftAPIError(503, "x", "x"), source_claim=lease
    )
    assert result.state == "retry_wait"
    return attempt.snapshot


def status():
    """The pages' status, read by the web login, with the schedule and clock."""
    with task_login(ServiceRole.WEB), transaction.atomic():
        now = database_now()
        configuration = SystemConfiguration.objects.select_related(
            "active_configuration", "current_campaign"
        ).get()
        return full_refresh_status(refresh_schedule(configuration), now)


def test_connection_and_data_age_follow_real_attempts(tmp_path):
    """Connection: unknown before any attempt, then working.

    An empty quick update proves the connection but leaves "data as of" at
    the full refresh.
    """
    credential, *_ = configured(tmp_path)
    first = status()
    assert first.connection.state == "unknown" and first.data_as_of is None
    full = publish(credential)
    after_full = status()
    assert after_full.connection.state == "working"
    assert after_full.data_as_of == full.started_at == after_full.full_started_at
    assert not after_full.out_of_date and after_full.overdue_at is None
    quick = publish(credential, kind="delta")
    after_quick = status()
    assert after_quick.data_as_of == full.started_at
    assert after_quick.connection == Connection("working", quick.completed_at)


def test_a_failed_attempt_reads_failing_since_its_start(tmp_path):
    """The newest attempt failed: "Failing since" the first failure after success."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    failed = failed_attempt(credential)
    assert status().connection == Connection("failing", failed.started_at)


def test_a_refusal_before_calling_parishsoft_is_not_an_attempt(tmp_path):
    """A refresh refused before it called ParishSoft leaves the line alone."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    working = status().connection
    settle_failed_read(claim(command()), CryptographicError("x"))
    assert status().connection == working


def test_a_quick_update_that_changed_records_moves_data_as_of(tmp_path, monkeypatch):
    """Data as of follows a quick update whose cursor counted a change."""
    credential, *_ = configured(tmp_path)
    full = publish(credential)
    monkeypatch.setattr(snapshots, "_changes", lambda snapshot, manifest: {"family": 2})
    quick = publish(credential, kind="delta")
    found = status()
    assert found.data_as_of == quick.started_at
    assert found.full_started_at == full.started_at and found.shows_full_separately


def test_the_digest_reads_data_age_as_known_at_its_observation(tmp_path):
    """The worker reads the digest's facts; later refreshes do not change them."""
    credential, *_ = configured(tmp_path)
    full = publish(credential)
    with transaction.atomic():
        observed = database_now()
    publish(credential)
    with task_login(ServiceRole.WORKER), transaction.atomic():
        age = data_age.data_age_at(observed)
    assert age.data_as_of == full.started_at == age.full_started_at
    assert age.connection.state == "working"


def test_scheduler_records_a_held_full_slot_under_its_login(tmp_path):
    """The scheduler login may write its durable send-hold entry (#510)."""
    with_schedule(tmp_path, full_refresh_times=["02:00", "12:00"])
    slot = RefreshSlot("nightly", database_now_outside(), "a" * 64, uuid4(), "12:00")
    with task_login(ServiceRole.SCHEDULER), transaction.atomic():
        production._log_skip(slot)
    (entry,) = OperationalLog.objects.filter(schema="schedule")
    assert entry.correlation_id == slot.command_id


def database_now_outside():
    """The database clock, read in its own short transaction."""
    with transaction.atomic():
        return database_now()


def test_the_web_login_can_check_for_a_send_on_a_long_gap(tmp_path):
    """Pages ask whether a send is holding refreshes before "not checked"."""
    configured(tmp_path)
    with task_login(ServiceRole.WEB), transaction.atomic():
        assert not send_hold.family_send_active()


def test_a_catch_up_request_counts_as_a_scheduled_full_refresh(day, scheduled):
    """The schedule-change catch-up (#632) is a scheduled full refresh.

    Requested on time after the overdue slot, it means refreshes are not
    being held (``deltas_skipped``), and like a full slot that ran it voids a
    later hold entry as evidence for that slot (``catch_up_held``). Before
    the overdue slot it changes nothing.
    """
    at = day("off")
    morning(at, scheduled, quick=False)
    held_entry(at(10, 30))
    with transaction.atomic():
        assert send_hold.deltas_skipped(at(10), at(10, 31))
        assert send_hold.catch_up_held(at(10))
    scheduled("catch_up", at(10, 5))
    with transaction.atomic():
        assert not send_hold.deltas_skipped(at(10), at(10, 31))
        assert not send_hold.catch_up_held(at(10))
        # Measured from 10:10, the 10:05 catch-up is before the span.
        assert send_hold.catch_up_held(at(10, 10))
