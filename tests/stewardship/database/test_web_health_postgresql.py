"""The web health row, its CRITICAL entries, the incident and its recovery (#392 L1).

Real restricted logins, the real trigger and the real operational collector;
earlier observations are made by moving the row's times back with its guard
briefly disabled, never by sleeping.
"""

import json

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import web_health
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
)
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.web_health import (
    ProbeResult,
    WebHealthProducer,
    record_observation,
)
from parishkit.stewardship.jobs.web_health_models import WebHealth
from parishkit.stewardship.observability import Event, FailureKind

from .test_background_grants_postgresql import task_login
from .test_operational_collection_postgresql import consume, schedule

pytestmark = pytest.mark.django_db(transaction=True)

TIMEOUT = ProbeResult(
    FailureKind.WEB_PROBE_TIMEOUT, timed_out=True, limit=3, elapsed=3.2
)
DOWN = ProbeResult(FailureKind.WEB_BAD_RESPONSE, status=503)


def record(result):
    """Write one result as the owned scheduler does."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session():
        record_observation(result)


def shift(**intervals):
    """Move the row's times back (seconds), as if observed that long ago."""
    assignments = ",".join(
        f"{column}={column}-interval '{seconds} seconds'"
        for column, seconds in intervals.items()
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_web_health "
            "DISABLE TRIGGER stewardship_web_health_guard"
        )
        cursor.execute(f"UPDATE stewardship_web_health SET {assignments}")
        cursor.execute(
            "ALTER TABLE stewardship_web_health "
            "ENABLE TRIGGER stewardship_web_health_guard"
        )


def failed_minutes(count, result=DOWN):
    """Record ``count`` failed minutes in a row, a minute apart."""
    for index in range(count):
        if index:
            shift(observed_at=60, failing_since=60)
        record(result)


def entries():
    """The CRITICAL web_unhealthy entries, oldest first."""
    return list(
        OperationalLog.objects.filter(event=Event.WEB_UNHEALTHY).order_by("created_at")
    )


def test_three_failed_minutes_write_a_critical_entry_each_and_open_the_incident():
    """Two failed minutes are quiet; the third and each later one alert."""
    failed_minutes(2)
    row = WebHealth.objects.get()
    assert (row.healthy, row.failures, row.passing_since) == (False, 2, None)
    assert entries() == []
    shift(observed_at=60, failing_since=60)
    record(DOWN)
    (entry,) = entries()
    assert entry.level == "CRITICAL" and entry.schema == "failure"
    assert entry.context == {
        "failure": "web_unresponsive",
        "failure_kind": "web_bad_response",
        "status": 503,
        "count": 3,
    }
    shift(observed_at=60, failing_since=60)
    record(DOWN)
    assert [e.context["count"] for e in entries()] == [3, 4]
    (identifier,) = schedule()
    consume(identifier)
    incident = OperationalIncident.objects.get()
    assert incident.kind == "web_unhealthy" and incident.level == "CRITICAL"
    assert OperationalNotice.objects.get().phase == "opened"


def test_a_timed_out_minute_also_records_what_timed_out():
    """Each timeout keeps its limit and elapsed time (the timeout-logging rule)."""
    record(TIMEOUT)
    (timeout,) = OperationalLog.objects.filter(event=Event.TASK_TIMED_OUT)
    assert timeout.level == "WARNING" and timeout.schema == "timeout"
    assert timeout.context == {
        "what": "web_probe",
        "limit_seconds": 3,
        "elapsed_seconds": 3,
    }


def test_a_repeated_pass_within_30_seconds_counts_once():
    """A retried scheduler pass cannot turn one failed minute into three."""
    for _ in range(3):
        record(DOWN)
    assert WebHealth.objects.get().failures == 1 and entries() == []


def test_a_gap_starts_a_new_run():
    """A result long after the previous one is not a continuous failure."""
    failed_minutes(2)
    shift(observed_at=200, failing_since=200)
    record(DOWN)
    row = WebHealth.objects.get()
    assert row.failures == 1 and entries() == []
    shift(observed_at=60, failing_since=60)
    record(ProbeResult())
    row = WebHealth.objects.get()
    assert (row.healthy, row.failures, row.failing_since) == (True, 0, None)
    assert row.passing_since == row.observed_at


def test_a_clock_step_back_starts_a_new_run_instead_of_dropping_results():
    """A previous observation in the future (the clock stepped back) is not
    within 30 seconds: the result is kept, as the first of a new run."""
    failed_minutes(2)
    shift(observed_at=-100, failing_since=-100)
    record(DOWN)
    row = WebHealth.objects.get()
    assert row.failures == 1 and entries() == []


def test_a_refused_context_never_blocks_the_entry():
    """A context outside the allowlist is replaced, and the entry still written."""
    failed_minutes(2)
    shift(observed_at=60, failing_since=60)
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session(),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT set_config(%s,%s,true)",
            [web_health.CONTEXT_SETTING, json.dumps({"failure": "free text"})],
        )
        cursor.execute("UPDATE stewardship_web_health SET healthy=false")
    (entry,) = entries()
    assert entry.context == {
        "failure": "web_unresponsive",
        "failure_kind": "unexpected_failure",
        "count": 3,
    }


def test_only_the_owned_scheduler_session_writes_the_row():
    """The worker may read the row; nobody else may write or delete it."""
    record(DOWN)
    update = "UPDATE stewardship_web_health SET healthy=true"
    with task_login(ServiceRole.WORKER, exact=True):
        assert WebHealth.objects.get().failures == 1
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(update)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        # The scheduler login without its session lock is refused too.
        with pytest.raises(DatabaseError), transaction.atomic():
            record_observation(ProbeResult())
        with (
            scheduler_session(),
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM stewardship_web_health")
    # The shape constraint refuses a count that disagrees with health.
    with (
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "ALTER TABLE stewardship_web_health "
            "DISABLE TRIGGER stewardship_web_health_guard"
        )
        cursor.execute(update)


def age_entries(seconds):
    """Move the web_unhealthy entries back, as if written that long ago."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_operational_log "
            "DISABLE TRIGGER stewardship_operational_log_immutable_guard_v1"
        )
        cursor.execute(
            "UPDATE stewardship_operational_log "
            "SET created_at=created_at-make_interval(secs=>%s) "
            "WHERE event='web_unhealthy'",
            [seconds],
        )
        cursor.execute(
            "ALTER TABLE stewardship_operational_log "
            "ENABLE TRIGGER stewardship_operational_log_immutable_guard_v1"
        )


def open_incident():
    """Three failed minutes, taken in by the collector ten minutes ago."""
    failed_minutes(3)
    (identifier,) = schedule()
    consume(identifier)
    age_entries(600)
    return OperationalIncident.objects.get()


def observe():
    """Run the collector's check as the worker does."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        web_health.observe_web_health()


def test_five_minutes_of_passing_probes_resolve_the_incident():
    """Recovery needs the full window of passes; a failure restarts it."""
    incident = open_incident()
    shift(observed_at=60)
    record(ProbeResult())
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    # Four minutes of passes are not enough.
    shift(passing_since=240)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    # Five are. (The collector task for this minute already ran, and its
    # key is the minute, so the check is called as that task calls it.)
    shift(passing_since=61)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None
    recovered = OperationalLog.objects.get(event=Event.INCIDENT_RECOVERED)
    assert recovered.context["incident_kind"] == "web_unhealthy"
    assert {notice.phase for notice in OperationalNotice.objects.all()} == {
        "opened",
        "resolved",
    }
    # Nothing is open, so the collector is no longer scheduled for it.
    assert schedule() == ()


def test_a_stale_row_or_an_unreceipted_failure_keeps_the_incident_open():
    """Old evidence is not recovery, and an entry not yet taken in blocks it."""
    incident = open_incident()
    shift(observed_at=60)
    record(ProbeResult())
    shift(observed_at=200, passing_since=600)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    # A current, long pass, but a failure entry the collector has not taken in.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_operational_log "
            "(id,correlation_id,level,event,schema,context) VALUES "
            "(gen_random_uuid(),gen_random_uuid(),'CRITICAL','web_unhealthy',"
            '\'failure\',\'{"failure":"web_unresponsive",'
            '"failure_kind":"web_unreachable","count":3}\'::jsonb)'
        )
    shift(observed_at=-200)
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None


def test_the_producer_records_its_probe_through_the_real_trigger():
    """The scheduler producer's thread result reaches the row."""
    producer = WebHealthProducer(("web",), probe=lambda hosts: DOWN)
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        assert producer(guard) == ()
        producer.thread.join(5)
        assert producer(guard) == ()
    assert WebHealth.objects.get().failures == 1
