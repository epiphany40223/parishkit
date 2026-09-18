"""Timing proof, privacy and restart-deduplicated scheduler lag on real SQL."""

import json
from datetime import timedelta

import pytest
from django.db import connection

from parishkit.stewardship.audit.models import AuditContext, AuditEvent, OperationalLog
from parishkit.stewardship.campaigns.boundary_production import produce_boundaries
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.installer_health import MAX_AGE_SECONDS
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..test_boundary_context import IDENTIFIER, INVALID, context
from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_boundary_production_postgresql import scheduled  # noqa: F401
from .test_boundary_tasks_postgresql import run

pytestmark = pytest.mark.django_db(transaction=True)


def test_ordered_boundaries_record_distinct_exact_timing_proof(scheduled):  # noqa: F811
    instant = scheduled.active_configuration.ends_at + timedelta(seconds=123)
    with campaign_clock(instant), scheduler_session() as guard:
        _, close = produce_boundaries(guard)
    with campaign_clock(instant):
        assert run(close)
    records = AuditContext.objects.filter(schema="boundary").select_related("event")
    assert records.count() == 2
    for record in records:
        payload = record.context
        due = getattr(
            scheduled.active_configuration,
            "starts_at" if payload["kind"] == "start" else "ends_at",
        )
        assert payload["intended_unix_microseconds"] == int(due.timestamp() * 1_000_000)
        assert payload["actual_unix_microseconds"] == int(
            instant.timestamp() * 1_000_000
        )
        assert payload["lag_microseconds"] == int(
            (instant - due).total_seconds() * 1_000_000
        )
        assert (payload["before_state"], payload["after_state"]) == (
            ("scheduled", "active")
            if payload["kind"] == "start"
            else ("active", "closed")
        )
        assert str(record.event.subject_id) == payload["occurrence_id"]
        assert record.event.correlation_id == record.correlation_id
        assert record.event.event_type == "campaign_boundary_completed"
    assert (
        AuditEvent.objects.filter(event_type="campaign_boundary_completed").count() == 2
    )


@pytest.mark.parametrize("offset,expected", [(0, 0), (1, 1)])
def test_lag_warning_threshold_and_restart_deduplication(scheduled, offset, expected):  # noqa: F811
    instant = scheduled.active_configuration.starts_at + timedelta(
        seconds=MAX_AGE_SECONDS + offset
    )
    with campaign_clock(instant), task_login(ServiceRole.SCHEDULER, exact=True):
        for _ in range(2):
            with scheduler_session() as guard:
                (start,) = produce_boundaries(guard)
                assert produce_boundaries(guard) == (start,)
    warnings = OperationalLog.objects.filter(event="campaign_boundary_lag")
    assert warnings.count() == expected
    if expected:
        warning = warnings.get()
        assert warning.level == "WARNING"
        assert warning.context == {
            "task_id": str(start.task_root_id),
            "count": MAX_AGE_SECONDS + offset,
        }
        assert warning.correlation_id == start.occurrence_id


@pytest.mark.parametrize("key,value", [(None, None), *INVALID])
@pytest.mark.django_db(transaction=False)
def test_sql_boundary_context_privacy_matches_python(key, value):
    """Raw SQL cannot evade typed state/date/identifier checks in Python."""
    payload = {**context(), "occurrence_id": str(IDENTIFIER)}
    if key:
        payload[key] = value
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_safe_context_v1('boundary', %s::jsonb)",
            [json.dumps(payload)],
        )
        assert cursor.fetchone()[0] is (key is None)
