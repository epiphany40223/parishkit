"""Complete-generation publication and raw-SQL guards on a disposable database."""

from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.reports.facts import (
    FactUnavailable,
    begin_fact_set,
    publish_fact_set,
    read_fact_set,
    stage_fact_days,
)
from parishkit.stewardship.reports.models import (
    CampaignDailyFact,
    CampaignDailyFactSet,
    CampaignFactPointer,
)
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

from .fact_builders import fact_fixture, staged_facts
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def test_exact_generation_deduplicates_and_publishes_whole_graph(tmp_path):
    """Exact retries and lazy reads expose all expected rows from one generation."""
    inputs, owner, source = fact_fixture(tmp_path)
    record, days = staged_facts(inputs, owner, source)
    assert begin_fact_set(inputs, owner, admit=permit).pk == record.pk
    assert stage_fact_days(record.pk, owner, days=days, admit=permit) == 0
    assert SourceSnapshotPin.objects.get(parent_kind="facts").parent_id == record.pk
    assert not CampaignFactPointer.objects.exists()
    with (
        pytest.raises(FactUnavailable, match="not ready"),
        read_fact_set(record.pk, admit=permit),
    ):
        pytest.fail("Building facts must remain hidden")
    ready = publish_fact_set(record.pk, owner, admit=permit, interactive=True)
    assert ready.state == "ready"
    with read_fact_set(record.pk, admit=permit) as selected:
        assert list(
            selected.days.order_by("local_date").values_list(
                "cumulative_responses", flat=True
            )
        ) == [1, 2]
    assert CampaignFactPointer.objects.get().fact_set_id == record.pk


def test_incomplete_publish_rolls_back_header_and_pointer(tmp_path):
    """SQL, not a UI convention, rejects a missing day before changing readiness."""
    inputs, owner, _ = fact_fixture(tmp_path)
    record = begin_fact_set(inputs, owner, admit=permit)
    with pytest.raises(IntegrityError, match="incomplete"):
        publish_fact_set(record.pk, owner, admit=permit, interactive=True)
    assert CampaignDailyFactSet.objects.get(pk=record.pk).state == "building"
    assert not CampaignFactPointer.objects.exists()


def test_staging_rejects_conflicting_repeat_and_out_of_bounds_day(tmp_path):
    """Chunk replay is idempotent only for the same values and valid date range."""
    inputs, owner, source = fact_fixture(tmp_path)
    record, days = staged_facts(inputs, owner, source)
    with pytest.raises(FactUnavailable, match="differs"):
        stage_fact_days(
            record.pk, owner, days=[{**days[0], "first_responses": 0}], admit=permit
        )
    with pytest.raises(IntegrityError, match="outside"):
        stage_fact_days(
            record.pk,
            owner,
            days=[{**days[0], "local_date": record.first_date - timedelta(days=1)}],
            admit=permit,
        )
    assert record.days.count() == 2


def test_raw_writes_cannot_change_published_rows_or_ready_inputs(tmp_path):
    """Bypassing immutable ORM methods still cannot corrupt a retained graph."""
    inputs, owner, source = fact_fixture(tmp_path)
    record, _ = staged_facts(inputs, owner, source)
    publish_fact_set(record.pk, owner, admit=permit, interactive=True)
    statements = (
        "UPDATE stewardship_daily_fact SET first_responses=0",
        "DELETE FROM stewardship_daily_fact",
        "UPDATE stewardship_daily_fact_set "
        "SET submission_watermark=2,version=version+1",
        "DELETE FROM stewardship_daily_fact_set",
    )
    for sql in statements:
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(sql)
    assert CampaignDailyFact.objects.count() == 2


def test_empty_pre_campaign_graph_and_midnight_rollover_are_distinct(tmp_path):
    """Date-bound inputs can be empty; unchanged watermarks cannot hide a new day."""
    inputs, owner, _ = fact_fixture(tmp_path)
    empty = begin_fact_set(
        replace(inputs, through_date=date(2026, 9, 30)), owner, admit=permit
    )
    assert empty.expected_count == 0
    publish_fact_set(empty.pk, owner, admit=permit)
    later = begin_fact_set(inputs, owner, admit=permit)
    assert later.pk != empty.pk and later.expected_count == 2


def test_recheck_admission_and_exact_task_fence(tmp_path):
    """Internal claims do not authorize a workflow or let another worker take over."""
    inputs, owner, source = fact_fixture(tmp_path)
    record, days = staged_facts(inputs, owner, source)
    with pytest.raises(PermissionError):
        publish_fact_set(record.pk, owner, admit=lambda *args: False)
    with pytest.raises(TaskOwnershipLost):
        stage_fact_days(
            record.pk, replace(owner, worker_id=uuid4()), days=days, admit=permit
        )
    with pytest.raises(TaskOwnershipLost):
        TaskClaim(owner.run_id, True, owner.worker_id)
    assert CampaignDailyFactSet.objects.get(pk=record.pk).state == "building"


@pytest.mark.parametrize(
    "change",
    [
        "expected_count=1",
        "first_date='2026-09-30'",
        "last_date='2026-10-03'",
        "source_generation=2",
        "submission_watermark=2",
        "population_scope='historical'",
        "task_fence=99",
        "state='failed',failure_code='private error value'",
    ],
)
def test_sql_rejects_rewriting_frozen_build_inputs(tmp_path, change):
    """An internal writer cannot silently rebind an allocated exact calculation."""
    inputs, owner, _ = fact_fixture(tmp_path)
    record = begin_fact_set(inputs, owner, admit=permit)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            f"UPDATE stewardship_daily_fact_set SET {change},version=version+1 "
            "WHERE id=%s",
            (record.pk,),
        )


def test_sql_liveness_requires_exact_task_fence_and_worker(tmp_path):
    """SQL arguments cannot resolve to same-named TaskRun columns or tautologies."""
    _, owner, _ = fact_fixture(tmp_path)
    with connection.cursor() as cursor:
        for task, fence, worker, expected in (
            (owner.run_id, owner.fence, owner.worker_id, True),
            (owner.run_id, owner.fence + 1, owner.worker_id, False),
            (owner.run_id, owner.fence, uuid4(), False),
            (uuid4(), owner.fence, owner.worker_id, False),
        ):
            cursor.execute(
                "SELECT stewardship_fact_live(%s,%s,%s)", [task, fence, worker]
            )
            assert cursor.fetchone()[0] is expected


def test_completeness_checks_cumulative_math_not_just_number_of_days(tmp_path):
    """Every expected date is insufficient evidence when cumulative counts disagree."""
    inputs, owner, source = fact_fixture(tmp_path)
    record = begin_fact_set(inputs, owner, admit=permit)
    # Each point obeys individual bounds, but their cumulative series is wrong.
    days = [
        dict(
            local_date=date(2026, 10, index),
            first_responses=0,
            cumulative_responses=1,
            cohort_denominator=10,
            source_generation=source.generation,
            source_as_of=source.promoted_at,
            population_available=True,
            pledge_available=False,
            pledge_total=None,
        )
        for index in (1, 2)
    ]
    stage_fact_days(record.pk, owner, days=days, admit=permit)
    with pytest.raises(IntegrityError, match="incomplete or inconsistent"):
        publish_fact_set(record.pk, owner, admit=permit)


def test_historical_generation_can_record_a_pre_source_unavailable_day(tmp_path):
    """Unavailable population is explicit and never invented from later source rows."""
    inputs, owner, _ = fact_fixture(tmp_path)
    record = begin_fact_set(
        replace(inputs, population_scope="historical"), owner, admit=permit
    )
    days = [
        dict(
            local_date=date(2026, 10, index),
            first_responses=0,
            cumulative_responses=0,
            cohort_denominator=0,
            source_generation=None,
            source_as_of=None,
            population_available=False,
            pledge_available=False,
            pledge_total=None,
        )
        for index in (1, 2)
    ]
    stage_fact_days(record.pk, owner, days=days, admit=permit)
    assert publish_fact_set(record.pk, owner, admit=permit).state == "ready"
    assert not SourceSnapshotPin.objects.filter(parent_id=record.pk).exists()


def test_fact_migrations_reverse_and_reapply_all_guards():
    """Offline schema rollback/reapply restores all state and protection functions."""
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("stewardship_reports", None)])
        assert (
            "stewardship_daily_fact_set" not in connection.introspection.table_names()
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regprocedure('stewardship_fact_disposable(uuid)')")
        assert cursor.fetchone()[0] is not None
