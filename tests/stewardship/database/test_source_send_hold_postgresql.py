"""Delta refreshes skipped while a bulk Family send is in progress (#440).

The scheduler skips a 15-minute slot during a send and creates it once the
send ends; the scheduled full refresh and a manual refresh are never skipped.
The staleness alarm holds within the send's allowance and behaves as before
past it. The send itself is counted from real (temporary, column-subset)
message and Task tables, and the restricted scheduler and worker logins can
read what the check needs.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.family_mail_tasks import TASK_TYPE as PREPARE
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source import health, production, send_hold
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.production import SourceProducer, produce_refreshes
from parishkit.stewardship.source.refresh_models import SourceRefreshTick

from .test_background_grants_postgresql import task_login
from .test_refresh_frequency_postgresql import with_frequency
from .test_source_attempts_postgresql import configured
from .test_source_health_postgresql import future_observation, observe, publish
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)


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
    monkeypatch.setattr(health, "family_send_active", lambda: active)


def skipping_deltas(credential):
    """Promote a full refresh and then a delta: the delta cadence is in use.

    No delta is requested after that, as when the scheduler skips them.
    """
    publish(credential)
    publish(credential, kind="delta")


def causes():
    """The causes of every refresh slot the scheduler has created."""
    return sorted(SourceRefreshTick.objects.values_list("command__cause", flat=True))


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


def test_without_promoted_source_nothing_is_skipped(tmp_path, monkeypatch):
    """Before the first promotion there is no current data to protect."""
    configured(tmp_path)
    sending(monkeypatch)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2


def test_skipping_ends_before_the_send_allowance_runs_out(tmp_path, monkeypatch):
    """A long send gets its deltas back in time for one to finish."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    sending(monkeypatch)
    with transaction.atomic():
        now = database_now()
        stale = timedelta(seconds=configured_policy().source_stale_seconds)
        limit = stale + send_hold.SEND_ALLOWANCE - send_hold.RESUME_LEAD
        started = SourceCurrent.objects.get().snapshot.started_at
        assert send_hold.delta_held(now)
        assert send_hold.delta_held(started + limit - timedelta(seconds=1))
        assert not send_hold.delta_held(started + limit)


def test_stale_alarm_holds_within_the_send_allowance(tmp_path, monkeypatch, settings):
    """Skipped deltas age the source on purpose: no alarm, no resolution."""
    credential, *_ = configured(tmp_path)
    skipping_deltas(credential)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch)
    with monkeypatch.context() as patch:
        future_observation(patch, 121)
        observe()
    assert not OperationalIncident.objects.exists()
    # Past the allowance the alarm sounds even during a send.
    allowance = int(send_hold.SEND_ALLOWANCE.total_seconds())
    with monkeypatch.context() as patch:
        future_observation(patch, 121 + allowance)
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


def test_stale_alarm_is_unchanged_without_a_send(tmp_path, monkeypatch, settings):
    """With no send in progress, staleness alarms at the configured threshold."""
    credential, *_ = configured(tmp_path)
    skipping_deltas(credential)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch, active=False)
    with monkeypatch.context() as patch:
        future_observation(patch, 121)
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


def test_a_held_sample_does_not_resolve_an_open_stale_alarm(
    tmp_path, monkeypatch, settings
):
    """An alarm raised before the send stays open until a refresh succeeds."""
    credential, *_ = configured(tmp_path)
    skipping_deltas(credential)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    with monkeypatch.context() as patch:
        future_observation(patch, 121)
        sending(patch, active=False)
        observe()
        sending(patch)
        observe()
    assert OperationalIncident.objects.get(kind="source_stale").resolved_at is None


@pytest.mark.parametrize("deltas", ["requested", "unused"])
def test_stale_alarm_is_unchanged_during_a_send_when_nothing_was_skipped(
    tmp_path, monkeypatch, settings, deltas
):
    """A delta requested but not promoted, or no deltas at all, still alarms.

    "requested": a delta was asked for after the current source was read and
    well before the resume point, and has not promoted (a failing or stuck
    refresh). "unused": no delta was requested within the last day, as with
    a quarter-hour full refresh.
    """
    credential, *_ = configured(tmp_path)
    if deltas == "requested":
        skipping_deltas(credential)
        command(cause="delta", actor_id=None)
    else:
        publish(credential)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    sending(monkeypatch)
    with monkeypatch.context() as patch:
        future_observation(patch, 121)
        observe()
    assert OperationalIncident.objects.get(kind="source_stale")


def test_catch_up_delta_keeps_the_alarm_held_until_the_allowance_ends(
    tmp_path, monkeypatch, settings
):
    """The scheduler's own resume-time delta does not sound a false alarm.

    The resume point is moved back to the current source's read, so the
    delta requested just after it is a catch-up request, as one made when
    skipping ends would be. A sample then still holds; one past the
    allowance alarms.
    """
    credential, *_ = configured(tmp_path)
    skipping_deltas(credential)
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    monkeypatch.setattr(
        send_hold, "RESUME_LEAD", send_hold.SEND_ALLOWANCE + timedelta(seconds=120)
    )
    command(cause="delta", actor_id=None)
    sending(monkeypatch)
    with monkeypatch.context() as patch:
        future_observation(patch, 121)
        observe()
    assert not OperationalIncident.objects.exists()
    allowance = int(send_hold.SEND_ALLOWANCE.total_seconds())
    with monkeypatch.context() as patch:
        future_observation(patch, 121 + allowance)
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
            "(id uuid PRIMARY KEY, purpose text, state text, pause_hold_id uuid)"
        )
        cursor.execute(
            "CREATE TEMP TABLE stewardship_task_run "
            "(id uuid PRIMARY KEY, task_type text, state text)"
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE pg_temp.stewardship_outbox_message")
            cursor.execute("DROP TABLE pg_temp.stewardship_task_run")
            cursor.execute("DROP TABLE pg_temp.stewardship_system_configuration")
            cursor.execute("DROP TABLE pg_temp.stewardship_campaign")


def add(table, rows):
    """Insert ``rows`` of (second column, state[, paused]) into a shadow table."""
    with connection.cursor() as cursor:
        for row in rows:
            if table == "message":
                purpose, state, paused = row
                cursor.execute(
                    "INSERT INTO pg_temp.stewardship_outbox_message "
                    "VALUES (gen_random_uuid(),%s,%s,%s)",
                    [purpose, state, uuid4() if paused else None],
                )
            else:
                task_type, state = row
                cursor.execute(
                    "INSERT INTO pg_temp.stewardship_task_run "
                    "VALUES (gen_random_uuid(),%s,%s)",
                    [task_type, state],
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


@pytest.mark.parametrize("role", [ServiceRole.SCHEDULER, ServiceRole.WORKER])
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
