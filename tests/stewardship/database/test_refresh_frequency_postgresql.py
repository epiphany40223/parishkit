"""Full ParishSoft refresh frequency and the last-full-refresh status line."""

from datetime import UTC, datetime

import pytest
from django.db import IntegrityError

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source import production
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.cadence import due_slots
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.outcomes import scope_fingerprint
from parishkit.stewardship.source.production import produce_refreshes
from parishkit.stewardship.source.refresh_models import SourceRefreshTick
from parishkit.stewardship.source.refresh_status import full_refresh_status
from parishkit.stewardship.source.snapshots import promote_snapshot

from .campaign_builders import change
from .test_source_attempts_postgresql import configured, setup, stage
from .test_source_requests_postgresql import claim, command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2026, 9, 11, 17, 23, 42, tzinfo=UTC)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def with_schedule(tmp_path, **settings):
    """Apply refresh schedule settings through an ordinary configuration change.

    Returns ``configured``'s credential, store, base version and actor.
    """
    credential, store, version, actor = configured(tmp_path)
    integration = version.document()["sections"]["integrations"][0]
    settings = integration["values"]["settings"] | settings
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": integration["id"],
            "values": {"settings": settings},
        }
    ]
    applied = change(store, version, actor, patch)
    assert applied.state == "applied"
    return credential, store, version, actor


def with_frequency(tmp_path, frequency, nightly_time="02:00"):
    """Apply a full-refresh frequency through an ordinary configuration change."""
    return with_schedule(tmp_path, full_refresh=frequency, nightly_time=nightly_time)


def forged_tick(tick, **slot):
    """A raw tick row for ``tick``'s configuration, with ``slot``'s due and key."""
    return {
        "configuration_id": tick.configuration_id,
        "due_at": slot["due_at"],
        "timezone": tick.timezone,
        "nightly_time": slot.get("nightly_time") or tick.nightly_time,
        "slot_key": slot["slot_key"],
    }


def scope_of(tick):
    """The scope fingerprint the tick's request was scheduled under."""
    request = tick.command.request
    return scope_fingerprint(request.organization_id, request.window_digest)


@pytest.mark.parametrize(
    "frequency,due,count",
    [
        ("hourly", datetime(2026, 9, 11, 17, tzinfo=UTC), 2),
        ("quarter_hour", datetime(2026, 9, 11, 17, 15, tzinfo=UTC), 1),
    ],
)
def test_frequent_full_refresh_ticks_pass_the_sql_cadence_guard(
    tmp_path, monkeypatch, frequency, due, count
):
    """The scheduler's hourly or quarter-hour slot is what SQL accepts."""
    with_frequency(tmp_path, frequency)
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == count
    tick = SourceRefreshTick.objects.get(command__cause="nightly")
    assert tick.due_at == due


def test_sql_refuses_a_full_tick_off_its_frequency_boundary(tmp_path, monkeypatch):
    """Under an hourly frequency, a daily-time slot is not a valid full tick."""
    with_frequency(tmp_path, "hourly", nightly_time="00:30")
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    with scheduler_session() as guard:
        produce_refreshes(guard)
        tick = SourceRefreshTick.objects.select_related("command").get(
            command__cause="nightly"
        )
        with (
            pytest.raises(IntegrityError, match="applied frequency"),
            work_transaction(),
        ):
            receipt = command(cause="nightly", actor_id=None)
            SourceRefreshTick.objects.create(
                command_id=receipt.command_id,
                configuration_id=tick.configuration_id,
                due_at=tick.due_at.replace(minute=30),
                timezone=tick.timezone,
                nightly_time=tick.nightly_time,
                slot_key=tick.slot_key,
            )


def test_full_refresh_status_reports_success_then_a_later_failure(tmp_path):
    """The status line names the last success and a failure only if newer."""
    assert full_refresh_status().succeeded_at is None
    credential, execution, source_claim, *_ = setup(tmp_path)
    assert full_refresh_status().running
    attempt = begin_refresh_attempt(execution, source_claim, credential)
    snapshot = stage(attempt, execution, source_claim)
    with execution.effect():
        promote_snapshot(snapshot.pk, source_claim, admit=permit, reconcile=permit)
    release_source(source_claim)
    execution.transition("complete")
    status = full_refresh_status()
    assert status.succeeded_at is not None
    assert status.failed_at is None and not status.running
    # A later full refresh that fails is reported after the success.
    failing = claim(command())
    failing.transition("permanent_failure")
    status = full_refresh_status()
    assert status.failed_at is not None and status.failed_at > status.succeeded_at
    # The banner links straight to the failed run's task page.
    assert status.failed_task_id == failing.claim.run_id


def test_daytime_full_refresh_tick_records_its_listed_time(tmp_path, monkeypatch):
    """At 13:23 parish time the 12:00 refresh is due, and SQL accepts its time (#465).

    The full tick names the listed time it fell due at; the delta tick keeps
    the nightly time, as before.
    """
    with_schedule(tmp_path, full_refresh_times=["02:00", "12:00", "20:00"])
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2
    tick = SourceRefreshTick.objects.get(command__cause="nightly")
    assert tick.nightly_time == "12:00"
    assert tick.due_at == datetime(2026, 9, 11, 16, tzinfo=UTC)
    assert SourceRefreshTick.objects.get(command__cause="delta").nightly_time == "02:00"


def test_sql_refuses_a_full_tick_at_an_unlisted_time(tmp_path, monkeypatch):
    """A resolvable local time outside the applied list is not a full tick."""
    with_schedule(tmp_path, full_refresh_times=["02:00", "12:00"])
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    with scheduler_session() as guard:
        produce_refreshes(guard)
        tick = SourceRefreshTick.objects.select_related("command__request").get(
            command__cause="nightly"
        )
        forged = due_slots(
            now=NOW,
            timezone=tick.timezone,
            nightly_time="02:00",
            scope_fingerprint=scope_of(tick),
            full_refresh_times=["02:00", "09:00"],
        )[0]
        assert forged.nightly_time == "09:00"
        with (
            pytest.raises(IntegrityError, match="not due under its applied cadence"),
            work_transaction(),
        ):
            receipt = command(cause="nightly", actor_id=None)
            SourceRefreshTick.objects.create(
                command_id=receipt.command_id,
                **forged_tick(
                    tick,
                    due_at=forged.due_at,
                    slot_key=forged.slot_key,
                    nightly_time="09:00",
                ),
            )


def test_sql_refuses_delta_ticks_the_applied_delta_cadence_does_not_schedule(
    tmp_path, monkeypatch
):
    """With deltas off no delta tick is valid; with hourly deltas no quarter hour is."""
    with_schedule(tmp_path, delta_refresh="off")
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 1
        tick = SourceRefreshTick.objects.select_related("command__request").get(
            command__cause="nightly"
        )
        quarter = due_slots(
            now=NOW,
            timezone=tick.timezone,
            nightly_time="02:00",
            scope_fingerprint=scope_of(tick),
        )[1]
        with (
            pytest.raises(IntegrityError, match="applied delta cadence"),
            work_transaction(),
        ):
            receipt = command(cause="delta", actor_id=None)
            SourceRefreshTick.objects.create(
                command_id=receipt.command_id,
                **forged_tick(tick, due_at=quarter.due_at, slot_key=quarter.slot_key),
            )


def test_hourly_deltas_pass_the_sql_guard_and_quarter_hours_do_not(
    tmp_path, monkeypatch
):
    """An hourly delta cadence schedules the hour and SQL refuses the quarter hour."""
    with_schedule(tmp_path, delta_refresh="hourly")
    monkeypatch.setattr(production, "database_now", lambda: NOW)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2
        tick = SourceRefreshTick.objects.select_related("command__request").get(
            command__cause="delta"
        )
        assert tick.due_at == datetime(2026, 9, 11, 17, tzinfo=UTC)
        quarter = due_slots(
            now=NOW,
            timezone=tick.timezone,
            nightly_time="02:00",
            scope_fingerprint=scope_of(tick),
        )[1]
        with (
            pytest.raises(IntegrityError, match="applied delta cadence"),
            work_transaction(),
        ):
            receipt = command(cause="delta", actor_id=None)
            SourceRefreshTick.objects.create(
                command_id=receipt.command_id,
                **forged_tick(tick, due_at=quarter.due_at, slot_key=quarter.slot_key),
            )
