"""Queued-task waiting reasons follow the real queue routing (#340).

The web cannot import the worker's handler registry, so queue_wait keeps its
own task-type-to-queue table. These tests pin that table to the handlers and
check the per-process queue sharing and the rendered wording; the database
tests read real task rows through the web login.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.jobs.queue_wait import (
    BLOCKER_NAMES,
    TASK_QUEUES,
    QueueWait,
    consumer_queues,
    waited_words,
)
from parishkit.stewardship.jobs.queues import WorkQueue

STARTED = datetime(2026, 9, 29, 22, 0, 3, tzinfo=UTC)
QUEUED = datetime(2026, 9, 29, 22, 0, 24, tzinfo=UTC)


def test_task_queues_match_the_handlers_that_run_them():
    """Every scheduled handler's queue is recorded here, and nothing else is."""
    from parishkit.stewardship.jobs.operational_collection import (
        TASK_TYPE as COLLECT,
    )
    from parishkit.stewardship.jobs.operational_collection import collection_handler
    from parishkit.stewardship.runtime_background import scheduler_handlers
    from parishkit.stewardship.source.setup_final_execution import (
        finalization_handler,
    )
    from parishkit.stewardship.source.setup_final_tasks import TASK_TYPE as FINALIZE

    handlers = scheduler_handlers()
    expected = {name: handler.queue for name, handler in handlers.items()}
    expected[FINALIZE] = finalization_handler(Mock(), scheduler=True).queue
    expected[COLLECT] = collection_handler(scheduler=True).queue
    assert expected == TASK_QUEUES
    # Only a task whose queue is known can be named as the one ahead.
    assert set(BLOCKER_NAMES) <= set(TASK_QUEUES)


def test_the_worker_and_status_pages_split_at_the_same_limit(tmp_path):
    """Status pages apply the worker's own rule to its login's real limit."""
    from parishkit.stewardship.jobs.queues import SOURCE_SPLIT_CONNECTIONS
    from parishkit.stewardship.runtime_process import split_source

    from .test_runtime_topology import configuration_at

    configuration = configuration_at(tmp_path)
    for overlap in (1, 2):
        budget = replace(configuration.runtime_budget, rollout_overlap=overlap)
        split = split_source(replace(configuration, runtime_budget=budget))
        assert split is (overlap * 3 >= SOURCE_SPLIT_CONNECTIONS)


def test_source_work_shares_the_export_process_only_without_the_split():
    """Since #336 a refresh has its own process unless the budget is too small."""
    general = consumer_queues(WorkQueue.GENERAL, split=True)
    assert WorkQueue.SOURCE not in general
    assert consumer_queues(WorkQueue.SOURCE, split=True) == {WorkQueue.SOURCE}
    assert WorkQueue.SOURCE in consumer_queues(WorkQueue.GENERAL, split=False)
    # The mail consumer is a separate container either way.
    assert WorkQueue.GENERAL not in consumer_queues(WorkQueue.MAIL, split=False)


@pytest.mark.parametrize(
    ("seconds", "words"),
    [(0, "0 seconds ago"), (1, "1 second ago"), (89, "89 seconds ago"),
     (90, "2 minutes ago"), (150, "3 minutes ago"), (300, "5 minutes ago")],
)  # fmt: skip
def test_waited_words_match_the_live_status_ticker(seconds, words):
    """The server text is what live-status-v1.js would show at that moment."""
    assert waited_words(timedelta(seconds=seconds)) == words


def _export(state, wait):
    """Render the export page with the fields its status region reads."""
    job = SimpleNamespace(
        pk=UUID(int=20),
        format="csv",
        requester_id=UUID(int=21),
        parameters={"population_scope": "current"},
        fact_set=SimpleNamespace(source_generation=3, submission_watermark=4),
        created_at=QUEUED,
        browser_timezone="UTC",
        report="participation",
    )
    return render_to_string(
        "stewardship/report-export.html",
        {
            "job": job,
            "status": {"state": state},
            "wait": wait,
            "mutable": True,
            "can_cancel": True,
            "report_url": "/participation",
        },
    )


def test_queued_export_behind_a_refresh_names_it_and_its_start():
    """The reason names the task ahead with its start as a local clock time."""
    wait = QueueWait(QUEUED, "3 minutes ago", "the ParishSoft update", STARTED)
    html = _export("queued", wait)
    assert "Waiting for the ParishSoft update to finish (started" in html
    assert (
        '<time datetime="2026-09-29T22:00:03+00:00" data-local-instant '
        "data-time-only>" in html
    )
    assert 'datetime="2026-09-29T22:00:24+00:00" data-live-since>3 minutes ago' in html
    assert "Preparing your file" not in html
    assert "data-live-pending" in html


def test_queued_export_with_nothing_named_ahead_gives_the_generic_reason():
    """Other queued-behind cases say so plainly, with the time waited."""
    html = _export("queued", QueueWait(QUEUED, "12 seconds ago"))
    assert "Waiting for other background work to finish." in html
    assert "12 seconds ago" in html
    assert "Preparing your file" not in html


@pytest.mark.parametrize("state", ["running", "queued"])
def test_claimed_or_unexplained_export_shows_the_normal_text(state):
    """A claimed task has no wait, so the page reads as it always did."""
    html = _export(state, None)
    assert "Preparing your file" in html
    assert "Waiting for" not in html


def test_background_task_and_exact_pages_share_the_reason():
    """Task details and exact exports explain a queued run the same way."""
    wait = QueueWait(QUEUED, "3 minutes ago", "the report totals update", STARTED)
    task = SimpleNamespace(
        id=UUID(int=1),
        name="Report export",
        type="report_export",
        state="queued",
        active=True,
        attempt=0,
        retry_sequence=0,
        initiator_id=None,
        created_at=QUEUED.isoformat(),
        heartbeat_at=None,
        lease_expires_at=None,
        progress=SimpleNamespace(phase="unspecified", total=0, current=0),
    )
    status = render_to_string(
        "stewardship/background-task-status.html", {"task": task, "wait": wait}
    )
    assert "Waiting for the report totals update to finish" in status
    assert "Still working" not in status
    plain = render_to_string("stewardship/background-task-status.html", {"task": task})
    assert "Still working" in plain
    exact = render_to_string(
        "stewardship/report-exact.html",
        {
            "job": SimpleNamespace(
                pk=UUID(int=40),
                format="csv",
                population_scope="current",
                submission_watermark=4,
                through_date=QUEUED.date(),
                timezone_configuration=SimpleNamespace(timezone="UTC"),
                browser_timezone="UTC",
                created_at=QUEUED,
            ),
            "source": {"generation": 1, "promoted_at": QUEUED},
            "status": {"state": "queued"},
            "wait": QueueWait(QUEUED, "5 seconds ago"),
            "report_url": "/participation",
        },
    )
    assert "Waiting for other background work to finish." in exact
    assert "Calculating the figures" not in exact
