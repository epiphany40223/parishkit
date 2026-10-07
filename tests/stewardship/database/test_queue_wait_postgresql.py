"""Queued export and task pages say why they wait, from real task rows (#340).

Rows are synthetic task runs moved through the real SQL task transitions; the
pages are read through the web login's own grants, and no audit row is
written by a status read. Whether the worker runs source work on its own
process comes from the worker login's real connection limit in pg_roles.
"""

from contextlib import contextmanager

import pytest
from django.db import connection
from psycopg import sql

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun, TaskRunEvent
from parishkit.stewardship.jobs.queue_wait import (
    SOURCE_SPLIT_CONNECTIONS,
    queue_wait,
    source_split,
)
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.runtime_grants import login_name

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_jobs_postgresql import scenario  # noqa: F401
from .test_export_views_postgresql import create, http_scenario  # noqa: F401
from .test_ministry_exports_postgresql import create as create_ministry_export
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import setup as ministry_setup
from .test_report_workspace_postgresql import read
from .test_taskrun_postgresql import act, expire, new

pytestmark = pytest.mark.django_db(transaction=True)


def running(task_type, **kwargs):
    """A claimed task of ``task_type`` with a live lease."""
    return act(new(task_type=task_type), "claim", **kwargs)


def wait_for(status, *, split=None, **kwargs):
    """The wait a status page would show for this run's chain.

    ``split`` sets the worker login's connection limit first: False gives it
    too few connections for a second process, True exactly enough.
    """
    if split is not None:
        with worker_limit(SOURCE_SPLIT_CONNECTIONS - (not split)):
            return wait_for(status, **kwargs)
    return queue_wait(TaskRun.objects.filter(root_id=status.root_id), **kwargs)


@contextmanager
def worker_limit(limit):
    """Give the worker login ``limit`` connections, as database-grants would.

    Worker startup refuses to run unless its login's limit matches its
    budget, so this limit is what a running worker splits its queues by.
    """
    name = sql.Identifier(login_name(ServiceRole.WORKER))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", [login_name(ServiceRole.WORKER)]
        )
        verb = "ALTER" if cursor.fetchone() else "CREATE"
        cursor.execute(
            sql.SQL("{} ROLE {} LOGIN CONNECTION LIMIT {}").format(
                sql.SQL(verb), name, sql.Literal(limit)
            )
        )
    try:
        yield
    finally:
        if verb == "CREATE":
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP ROLE {}").format(name))


def test_queued_export_behind_a_refresh_on_one_process_names_the_refresh():
    """A small budget keeps a refresh on the export's process: say so."""
    refresh = running("source_refresh")
    export = new(task_type="report_export")
    wait = wait_for(export, split=False)
    claimed = TaskRunEvent.objects.get(run_id=refresh.run_id, action="claim")
    assert wait.blocker == "the ParishSoft update"
    assert wait.blocker_started == claimed.created_at
    assert wait.since == TaskRun.objects.get(pk=export.run_id).created_at
    assert wait.waited.endswith("seconds ago")


def test_a_split_refresh_is_never_blamed_for_a_queued_export():
    """Since #336 a refresh runs on its own process, so it delays nothing."""
    running("source_refresh")
    wait = wait_for(new(task_type="report_export"), split=True)
    assert wait is not None and wait.blocker is None


def test_split_follows_the_worker_login_not_the_web_configuration():
    """The running worker's own limit decides, read afresh on every poll.

    A web process started before a budget change must not keep blaming a
    refresh that the recreated worker now runs on its own process (the skew
    in #358's review). Nothing about the web's configuration is consulted.
    """
    running("source_refresh")
    export = new(task_type="report_export")
    with worker_limit(SOURCE_SPLIT_CONNECTIONS - 1):
        assert source_split() is False
        assert wait_for(export).blocker == "the ParishSoft update"
        # The worker is reprovisioned and restarted with a larger budget.
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(
                    sql.Identifier(login_name(ServiceRole.WORKER)),
                    sql.Literal(SOURCE_SPLIT_CONNECTIONS),
                )
            )
        assert source_split() is True
        assert wait_for(export).blocker is None


@pytest.mark.parametrize("limit", [None, -1])
def test_an_unproven_worker_split_never_blames_a_refresh(limit):
    """No worker login, or an unlimited one, proves nothing: stay generic."""
    running("source_refresh")
    export = new(task_type="report_export")
    if limit is None:
        assert source_split() is True
        assert wait_for(export).blocker is None
    else:
        with worker_limit(limit):
            assert source_split() is True
            assert wait_for(export).blocker is None


def test_readers_without_background_work_get_only_the_generic_reason():
    """``named=False`` never names the task ahead, only how long it waited."""
    running("report_facts")
    wait = wait_for(new(task_type="report_export"), named=False)
    assert wait.blocker is None and wait.waited.endswith("ago")


def test_running_general_work_on_the_same_process_is_named():
    """Work on the export's own process is named whatever the budget."""
    running("report_facts")
    for split in (True, False):
        wait = wait_for(new(task_type="report_export"), split=split)
        assert wait.blocker == "the report totals update"


def test_queued_export_with_nothing_named_ahead_gives_the_generic_reason():
    """A lapsed lease or unnamed work: the generic reason, with the time waited."""
    expire(running("report_facts", lease_seconds=1))
    running("storage_probe")
    wait = wait_for(new(task_type="report_export"))
    assert wait is not None and wait.blocker is None
    assert wait.waited.endswith("ago")


def test_claimed_export_has_no_wait():
    """Once a worker claims the run, the page goes back to its normal text."""
    running("source_refresh")
    export = running("report_export")
    assert wait_for(export, split=False) is None


def test_export_page_explains_its_wait_through_the_web_login(
    http_scenario,  # noqa: F811
):
    """The real page shows each case, reads only through web grants, audits nothing."""
    _, browser = http_scenario
    request = create(http_scenario)
    path = f"/admin/reports/exports/{request.pk}/"
    before = AuditEvent.objects.count()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = read(browser, path)[1]
    assert b"Waiting for other background work to finish." in body
    assert b"Preparing your file" not in body
    assert b"data-live-since" in body
    running("source_refresh")
    with (
        worker_limit(SOURCE_SPLIT_CONNECTIONS - 1),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        body = read(browser, path)[1]
    assert b"Waiting for the ParishSoft update to finish (started" in body
    with (
        worker_limit(SOURCE_SPLIT_CONNECTIONS),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        body = read(browser, path)[1]
    assert b"Waiting for other background work to finish." in body
    act(_status(request.task), "claim")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = read(browser, path)[1]
    assert b"Preparing your file" in body
    assert b"Waiting for" not in body
    # Only the synthetic refresh and the export claim audit; no page read does.
    written = AuditEvent.objects.order_by("created_at").values_list(
        "event_type", flat=True
    )[before:]
    assert sorted(written) == ["task_claim", "task_claim", "task_created"]


def test_task_status_fragment_explains_a_queued_run(auth_service, google):  # noqa: F811
    """Background task pages carry the same reason in their polled fragment."""
    running("report_facts")
    task = new(task_type="report_export")
    browser, signed = signed_in()
    assert signed.status_code == 302
    path = f"/admin/system/background/{task.run_id}/status/"
    with task_login(ServiceRole.WEB):
        body = browser.get(path).content
    assert b"Waiting for the report totals update to finish" in body
    act(task, "claim")
    with task_login(ServiceRole.WEB):
        body = browser.get(path).content
    assert b"to finish" not in body and b'data-live-state="running"' in body


def test_ministry_leader_sees_only_the_generic_reason(response_service, google):
    """A leader may open their own export but not background work (#358)."""
    harness = ministry_setup(response_service)
    browser, actor, _, _ = leader(harness, google)
    export = create_ministry_export(harness, actor)
    running("report_facts")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, f"/admin/reports/exports/{export.pk}/")
    assert response.status_code == 200
    assert b"Waiting for other background work to finish." in body
    assert b"report totals" not in body
