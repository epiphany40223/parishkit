"""Scheduler-owned refresh slots are atomic, replayable and scope-bound."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import (
    SchedulerOwnershipLost,
    scheduler_session,
)
from parishkit.stewardship.source import production
from parishkit.stewardship.source.cadence import due_slots
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.outcomes import scope_fingerprint
from parishkit.stewardship.source.production import (
    SourceProducer,
    produce_refreshes,
    sweep_superseded_refreshes,
)
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshCommand,
    SourceRefreshRequest,
    SourceRefreshTick,
)

from .campaign_builders import add_draft, change, restored_runtime
from .test_source_attempts_postgresql import configured
from .test_source_requests_postgresql import command

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def test_two_due_slots_share_one_full_request_and_survive_restart(tmp_path):
    """Repeated scans keep their exact commands even when no insertion hint survived."""
    configured(tmp_path)
    with scheduler_session() as guard:
        raw = connection.connection
        first = produce_refreshes(guard)
        assert produce_refreshes(guard) == first
        assert connection.connection is raw
        guard.check()
    with scheduler_session() as guard:
        assert produce_refreshes(guard) == first
    assert len(first) == SourceRefreshTick.objects.count() == 2
    assert SourceRefreshCommand.objects.count() == 2
    assert SourceRefreshRequest.objects.get().kind == "full"
    assert TaskRun.objects.get(task_type="source_refresh").state == "queued"
    assert first[0].request_id == first[1].request_id
    assert {tick.command.cause for tick in SourceRefreshTick.objects.all()} == {
        "nightly",
        "delta",
    }
    with transaction.atomic():
        assert all(
            tick.due_at <= database_now() for tick in SourceRefreshTick.objects.all()
        )


@pytest.mark.parametrize("zone", ["EET", "US/Eastern", "America/Indianapolis"])
def test_nightly_alias_uses_same_local_day_as_python_resolver(
    tmp_path, monkeypatch, zone
):
    """A DST midnight slot must not become the previous date under a fixed offset."""
    _, store, version, actor = configured(tmp_path)
    sections = version.document()["sections"]
    integration = sections["integrations"][0]
    assert (
        change(
            store,
            version,
            actor,
            [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": sections["parish"][0]["id"],
                    "values": {"timezone": zone},
                },
                {
                    "operation": "update",
                    "section": "integrations",
                    "id": integration["id"],
                    "values": {
                        "settings": integration["values"]["settings"]
                        | {"nightly_time": "00:30"}
                    },
                },
            ],
        ).state
        == "applied"
    )
    now = datetime(2026, 4, 15, 12, tzinfo=UTC)
    monkeypatch.setattr(production, "database_now", lambda: now)
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2
    tick = SourceRefreshTick.objects.select_related("command__request").get(
        command__cause="nightly"
    )
    request = tick.command.request
    expected = due_slots(
        now=now,
        timezone=zone,
        nightly_time="00:30",
        scope_fingerprint=scope_fingerprint(
            request.organization_id, request.window_digest
        ),
    )[0]
    assert tick.due_at == expected.due_at
    assert tick.slot_key == expected.slot_key


def test_absent_configuration_and_restore_are_holds_not_consumed_slots(tmp_path):
    """Read scheduling cannot manufacture its own setup or maintenance exemption."""
    with scheduler_session() as guard:
        assert produce_refreshes(guard) == ()
    configured(tmp_path)
    with transaction.atomic():
        now = database_now()
    with restored_runtime(now), scheduler_session() as guard:
        assert produce_refreshes(guard) == ()
    assert not SourceRefreshTick.objects.exists()
    with scheduler_session() as guard:
        assert len(produce_refreshes(guard)) == 2


def test_tick_insert_failure_rolls_back_all_commands_and_tasks(tmp_path, monkeypatch):
    """A slot cannot be consumed independently of the actual durable refresh request."""
    configured(tmp_path)

    def fail(**options):
        """Fail durable cadence evidence after the first Task/command was prepared."""
        raise RuntimeError("Synthetic tick persistence failure")

    monkeypatch.setattr(production.SourceRefreshTick.objects, "create", fail)
    with scheduler_session() as guard, pytest.raises(RuntimeError, match="tick"):
        produce_refreshes(guard)
    assert not SourceRefreshTick.objects.exists()
    assert not SourceRefreshCommand.objects.exists()
    assert not TaskRun.objects.filter(task_type="source_refresh").exists()


def test_changed_campaign_creates_new_scope_without_rebinding_old_ticks(tmp_path):
    """Old work retains its original meaning for supersession or completion owners."""
    _, store, version, actor = configured(tmp_path)
    with scheduler_session() as guard:
        first = produce_refreshes(guard)
    add_draft(store, version, actor)
    with scheduler_session() as guard:
        second = produce_refreshes(guard)
    assert first[0].request_id != second[0].request_id
    assert SourceRefreshTick.objects.count() == 4
    assert SourceRefreshRequest.objects.count() == 2


def test_actual_scheduler_lifetime_is_required(tmp_path):
    """Neither a caller flag nor a released guard may authorize production."""
    configured(tmp_path)
    with pytest.raises(TypeError):
        produce_refreshes(True)
    with scheduler_session() as guard:
        pass
    with pytest.raises(SchedulerOwnershipLost):
        produce_refreshes(guard)
    assert not SourceRefreshTick.objects.exists()


@pytest.mark.parametrize("field,value", [("slot_key", "a" * 64), ("timezone", "UTC")])
def test_raw_tick_cannot_forge_schedule_inputs(tmp_path, field, value):
    """Raw writers must satisfy the same current-source/cadence identity contract."""
    configured(tmp_path)
    with scheduler_session() as guard:
        produce_refreshes(guard)
        tick = SourceRefreshTick.objects.select_related("command").first()
        with pytest.raises(IntegrityError, match="Refresh tick"), work_transaction():
            receipt = command(cause=tick.command.cause, actor_id=None)
            values = {
                "command_id": receipt.command_id,
                "configuration_id": tick.configuration_id,
                "due_at": tick.due_at,
                "timezone": tick.timezone,
                "nightly_time": tick.nightly_time,
                "slot_key": tick.slot_key,
            }
            values[field] = value
            SourceRefreshTick.objects.create(**values)


def test_raw_tick_requires_scheduler_and_no_future_due_time(tmp_path):
    """A known slot is not a grant to write future or unowned scheduled evidence."""
    configured(tmp_path)
    with scheduler_session() as guard:
        produce_refreshes(guard)
        tick = SourceRefreshTick.objects.select_related("command__request").get(
            command__cause="delta"
        )
        request = tick.command.request
        future = due_slots(
            now=tick.due_at + timedelta(days=1),
            timezone=tick.timezone,
            nightly_time=tick.nightly_time,
            scope_fingerprint=scope_fingerprint(
                request.organization_id, request.window_digest
            ),
        )[1]
        with (
            pytest.raises(IntegrityError, match="not due under its applied cadence"),
            work_transaction(),
        ):
            receipt = command(cause="delta", actor_id=None)
            SourceRefreshTick.objects.create(
                command_id=receipt.command_id,
                configuration_id=tick.configuration_id,
                due_at=future.due_at,
                timezone=tick.timezone,
                nightly_time=tick.nightly_time,
                slot_key=future.slot_key,
            )
    with (
        pytest.raises(IntegrityError, match="requires scheduler and work ownership"),
        work_transaction(),
    ):
        receipt = command(cause="delta", actor_id=None)
        SourceRefreshTick.objects.create(
            command_id=receipt.command_id,
            configuration_id=tick.configuration_id,
            due_at=tick.due_at,
            timezone=tick.timezone,
            nightly_time=tick.nightly_time,
            slot_key=tick.slot_key,
        )


def test_tick_is_immutable_and_cannot_be_deleted(tmp_path):
    """Tick history cannot be edited or deleted through raw SQL."""
    configured(tmp_path)
    with scheduler_session() as guard:
        produce_refreshes(guard)
    tick = SourceRefreshTick.objects.first()
    for sql in (
        "UPDATE stewardship_source_refresh_tick SET correlation_id=%s WHERE id=%s",
        "DELETE FROM stewardship_source_refresh_tick WHERE id=%s AND %s IS NOT NULL",
    ):
        values = [uuid4(), tick.pk] if sql.startswith("UPDATE") else [tick.pk, "x"]
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute(sql, values)
    assert SourceRefreshTick.objects.count() == 2


def test_supersession_scan_is_bounded_and_advances_past_current_work(tmp_path):
    """One held/current row cannot pin the source-specific scheduler sweep."""
    _, store, version, actor = configured(tmp_path)
    old_delta = command(cause="delta", actor_id=None)
    old_full = command()
    add_draft(store, version, actor)
    current = command()
    worker_id = uuid4()
    with scheduler_session() as guard:
        count, position = sweep_superseded_refreshes(
            guard, worker_id=worker_id, limit=1
        )
        assert count == 1 and position is not None
        count, position = sweep_superseded_refreshes(
            guard, worker_id=worker_id, cursor=position, limit=1
        )
        assert count == 1 and position is not None
        count, position = sweep_superseded_refreshes(
            guard, worker_id=worker_id, cursor=position, limit=1
        )
        assert count == 0 and position is not None
        assert sweep_superseded_refreshes(
            guard, worker_id=worker_id, cursor=position, limit=1
        ) == (0, None)
    for receipt in (old_delta, old_full):
        assert TaskRun.objects.get(pk=receipt.task_root_id).state == "cancelled"
    assert TaskRun.objects.get(pk=current.task_root_id).state == "queued"


def test_compiled_producer_retires_stale_work_before_current_slots(tmp_path):
    """The process-loop producer composes real cleanup and durable slot creation."""
    _, store, version, actor = configured(tmp_path)
    old = command()
    add_draft(store, version, actor)
    producer = SourceProducer(uuid4())
    with scheduler_session() as guard:
        receipts = producer(guard)
    assert len(receipts) == 2 and producer.cursor is None
    assert TaskRun.objects.get(pk=old.task_root_id).state == "cancelled"
    assert all(receipt.request_id != old.request_id for receipt in receipts)
