"""Retention that keeps stopping at its limits opens the incident (#833).

Runs stop for real: each runs ``compact_before_refresh`` with a zero time
budget, under its task's own correlation id, as a refresh does, so each
writes its INFO ``retention_budget`` entry. The old-backlog probes run
against real compacted corpora and superseded fact generations.
"""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.source import compaction, retention_health
from parishkit.stewardship.source.snapshot_models import SourceSnapshot

from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_fact_retention_postgresql import superseded
from .test_source_compaction_postgresql import _Execution, history  # noqa: F401
from .test_source_retention_health_postgresql import episode, observe

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def probe_is_a_refresh(monkeypatch):
    """The synthetic task type the builders use counts as a refresh."""
    monkeypatch.setattr(
        retention_health,
        "REFRESH_TASK_TYPES",
        (*retention_health.REFRESH_TASK_TYPES, "source_probe"),
    )


def stopping_run(monkeypatch):
    """One refresh's retention, stopped at once by its time budget."""
    task = running_source_task()
    run = TaskRun.objects.get(pk=task["task_id"]).correlation_id
    with monkeypatch.context() as context, correlation(run):
        context.setattr(compaction, "RETENTION_BUDGET_SECONDS", 0)
        compaction.compact_before_refresh(_Execution(task))


@contextmanager
def replica():
    """Write past the guards (test superuser only), to age a row."""
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
        try:
            yield cursor
        finally:
            cursor.execute("SET session_replication_role = DEFAULT")


def stalled():
    """retention_stalled, as the worker that observes it."""
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        return retention_health.retention_stalled()


def test_twelve_stopping_runs_with_old_work_open_the_incident(history, monkeypatch):  # noqa: F811
    """Eleven are not enough; the twelfth opens it; a clear backlog resolves it."""
    monkeypatch.setattr(retention_health, "_old_backlog", lambda: True)
    for _ in range(retention_health.STALLED_RUNS - 1):
        stopping_run(monkeypatch)
    assert not stalled()
    assert observe() is None
    stopping_run(monkeypatch)
    assert stalled()
    opened = observe()
    assert opened is not None and opened.signal_level == "WARNING"
    monkeypatch.setattr(retention_health, "_old_backlog", lambda: False)
    assert observe() is None and episode() is None


def test_stopping_runs_without_old_work_open_nothing(history, monkeypatch):  # noqa: F811
    """Stops alone are housekeeping."""
    monkeypatch.setattr(retention_health, "_old_backlog", lambda: False)
    for _ in range(retention_health.STALLED_RUNS):
        stopping_run(monkeypatch)
    assert not stalled() and observe() is None


def test_one_run_that_did_not_stop_breaks_the_streak(history, monkeypatch):  # noqa: F811
    """Old work with a recent run that got through is not a stall."""
    monkeypatch.setattr(retention_health, "_old_backlog", lambda: True)
    for _ in range(retention_health.STALLED_RUNS):
        stopping_run(monkeypatch)
    task = running_source_task()
    with correlation(TaskRun.objects.get(pk=task["task_id"]).correlation_id):
        compaction.compact_before_refresh(_Execution(task))
    assert not stalled()


def test_a_corpus_compacted_long_ago_but_not_reclaimed_is_old_work(history):  # noqa: F811
    """A compacted snapshot that still has membership rows two days on."""
    snapshot = SourceSnapshot.objects.filter(state="promoted").order_by("promoted_at")[
        0
    ]
    for days, expected in ((1, False), (3, True)):
        with replica() as cursor:
            cursor.execute(
                "UPDATE stewardship_source_snapshot SET compacted_at="
                "statement_timestamp()-make_interval(days=>%s) WHERE id=%s",
                [days, snapshot.pk],
            )
        with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
            assert retention_health._old_backlog() is expected


def test_a_generation_superseded_long_ago_is_old_work(tmp_path):
    """A disposable generation whose successor was published two days ago;
    a just-superseded one is not old."""
    _, _, _, new = superseded(tmp_path)
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        assert retention_health._old_backlog() is False
    with replica() as cursor:
        cursor.execute(
            "UPDATE stewardship_daily_fact_set SET created_at=created_at-%s "
            "WHERE id<>%s",
            [timedelta(days=4), new.pk],
        )
        cursor.execute(
            "UPDATE stewardship_daily_fact_set SET created_at=created_at-%s "
            "WHERE id=%s",
            [timedelta(days=3), new.pk],
        )
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        assert retention_health._old_backlog() is True


def test_the_refresh_task_type_is_the_real_one():
    """The constant names the task type that runs this retention."""
    from parishkit.stewardship.source.requests import TASK_TYPE

    assert TASK_TYPE in retention_health.REFRESH_TASK_TYPES


def test_a_stop_followed_by_a_later_success_does_not_count(history, monkeypatch):  # noqa: F811
    """Twelve runs whose newest outcome is a stop stall; if the twelfth then
    gets through on a retry under the same correlation, it does not."""
    monkeypatch.setattr(retention_health, "_old_backlog", lambda: True)
    monkeypatch.setattr(retention_health, "_made_progress", lambda runs: False)
    for _ in range(retention_health.STALLED_RUNS - 1):
        stopping_run(monkeypatch)
    task = running_source_task()
    run = TaskRun.objects.get(pk=task["task_id"]).correlation_id
    with monkeypatch.context() as context, correlation(run):
        context.setattr(compaction, "RETENTION_BUDGET_SECONDS", 0)
        compaction.compact_before_refresh(_Execution(task))
    assert stalled()
    retry = running_source_task()
    with correlation(run):
        compaction.compact_before_refresh(_Execution(retry))
    assert not stalled()


def test_runs_that_removed_something_made_progress(history):  # noqa: F811
    """Real compaction evidence with non-zero counts is progress; a run
    stopped before anything is not."""
    task = running_source_task()
    done = TaskRun.objects.get(pk=task["task_id"]).correlation_id
    with correlation(done):
        compaction.compact_before_refresh(_Execution(task))
    with task_login(ServiceRole.WORKER, exact=True), transaction.atomic():
        assert retention_health._made_progress([done])
        assert not retention_health._made_progress([uuid4()])


def test_a_steady_drain_is_not_a_stall(history, monkeypatch):  # noqa: F811
    """Twelve stopping runs with old work, one of which removed rows, are
    housekeeping: the backlog is shrinking."""
    monkeypatch.setattr(retention_health, "_old_backlog", lambda: True)
    for _ in range(retention_health.STALLED_RUNS):
        stopping_run(monkeypatch)
    assert stalled()
    monkeypatch.setattr(retention_health, "_made_progress", lambda runs: True)
    assert not stalled()
