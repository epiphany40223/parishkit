"""A bulk Family send's backlog under the real scheduler login (#634).

The bulk fixture's six Families stand in for a thousand: their invitation is
planned and prepared, and its delivery tasks are moved 20 minutes into the
past, as if the mail consumers had not reached them yet. The scan, its reads
of the send and the checkpoint trigger all run as the restricted scheduler.
The thresholds themselves are unit tested in ``test_due_work_send.py``.
"""

import pytest
from django.db import connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue
from parishkit.stewardship.jobs.due_work_health import DueWorkScan
from parishkit.stewardship.jobs.due_work_models import DueWorkHealth
from parishkit.stewardship.jobs.family_mail_dispatch import TASK_TYPE as DELIVERY
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import scan_once, scheduler_session
from parishkit.stewardship.observability import Event as LogEvent
from parishkit.stewardship.source import send_hold

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_family_mail_bulk_postgresql import (  # noqa: F401
    EXTRA,
    due,
    families,
    plan,
    prepare_all,
)
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
FAMILIES = EXTRA + 1


def backdate(table, column, minutes, where):
    """Move a fixture time into the past with the table's guards briefly off.

    Only this disposable database's fixture rows change; the scan, its reads
    and the checkpoint trigger that follow run with every guard installed.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        cursor.execute(
            f"UPDATE {table} SET {column}=clock_timestamp()-make_interval(mins=>%s) "
            f"WHERE {where}",
            [minutes],
        )
        cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


@pytest.fixture
def backlog(families):  # noqa: F811
    """The invitation prepared, its delivery tasks due 20 minutes ago."""
    harness, path = families
    with campaign_clock(due()):
        plan()
        prepare_all(harness)
    assert (
        OutboxMessage.objects.filter(purpose="initial", state="pending").count()
        == FAMILIES
    )
    backdate(
        "stewardship_task_run",
        "not_before",
        20,
        f"task_type='{DELIVERY}' AND state='queued'",
    )
    return harness, path


@pytest.fixture
def in_progress(monkeypatch):
    """Six remaining messages count as a send in progress, as ten would."""
    real = send_hold.family_send_active
    monkeypatch.setattr(send_hold, "family_send_active", lambda: real(minimum=1))


def scan(**kwargs):
    """One sweep (one page with ``limit``) by the real scheduler login."""
    handlers = {DELIVERY: Handler(WorkQueue.MAIL, lambda *args: True, lambda _: None)}
    with (
        campaign_clock(due()),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        result = scan_once(
            guard,
            handlers=handlers,
            publish=lambda _: None,
            health=DueWorkScan(),
            **kwargs,
        )
    if not kwargs:
        assert result.cursor is None
        return DueWorkHealth.objects.get()
    return result


def escalate():
    """Treat the current late window as 20 minutes old, then scan again.

    As ``test_due_work_health_postgresql.aged_checkpoint`` does, the last
    observation is also moved 40 seconds back, past the scan's write throttle.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_due_work_health "
            "DISABLE TRIGGER stewardship_due_work_health_guard"
        )
        cursor.execute(
            "UPDATE stewardship_due_work_health SET "
            "late_since=clock_timestamp()-interval '20 minutes', "
            "scan_started_at=clock_timestamp()-interval '41 seconds', "
            "observed_at=clock_timestamp()-interval '40 seconds'"
        )
        cursor.execute(
            "ALTER TABLE stewardship_due_work_health "
            "ENABLE TRIGGER stewardship_due_work_health_guard"
        )
    scan()
    return OperationalLog.objects.get(event=LogEvent.DUE_WORK_LAG)


def test_a_progressing_send_is_not_late(backlog, in_progress):
    """Tasks 20 minutes past due, but the send is progressing: no lag."""
    assert scan().signal == "clear"
    assert not OperationalLog.objects.filter(event=LogEvent.DUE_WORK_LAG).exists()


def test_a_stalled_send_raises_with_its_context(backlog, in_progress):
    """Nothing prepared or settled since it fell due: late, naming the send."""
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition

    # Prepared 25 minutes ago, ahead of the due time, and untouched since.
    backdate("stewardship_task_run", "created_at", 25, f"task_type='{DELIVERY}'")
    assert scan().signal == "late"
    failure = escalate()
    assert failure.level == "CRITICAL" and failure.schema == "due_work"
    context = failure.context
    definition = ScheduleDefinition.objects.get()
    assert context["definition_id"] == str(definition.pk)
    assert context["revision_id"] == str(definition.current_revision_id)
    assert (context["remaining_count"], context["done_count"]) == (FAMILIES, 0)
    # Preparation before the due time is not progress: stalled since then.
    assert 1200 <= context["stall_seconds"] == context["elapsed_seconds"] < 1300
    assert context["limit_seconds"] == 600
    assert "last_progress_unix_seconds" not in context
    assert context["task_type"] == DELIVERY


def test_without_a_send_in_progress_the_per_task_rule_applies(backlog):
    """Fewer than ten messages left is a send's tail: each task is judged alone."""
    assert scan().signal == "late"
    context = escalate().context
    assert 1200 <= context.pop("lag_seconds") < 1300
    assert context == {"task_type": DELIVERY, "count": FAMILIES, "limit_seconds": 90}


def test_a_send_prepared_ahead_progresses_by_its_settled_messages(
    backlog, in_progress, monkeypatch
):
    """The production path: prepared before its due time, progress from sending.

    Every delivery task was created 25 minutes ago, before the send fell due
    20 minutes ago, so preparation is no progress. Two messages are then
    really sent through the one-at-a-time mail path; their delivery tasks'
    last change (``max(updated_at)`` along each task chain) is the send's
    progress, and the remaining four tasks' 20-minute wait is not late.
    """
    from parishkit.stewardship.jobs import due_work_health, family_mail_delivery_tasks
    from parishkit.stewardship.jobs.models import TaskRun

    from .test_family_mail_bulk_postgresql import Provider, single

    harness, path = backlog
    backdate("stewardship_task_run", "created_at", 25, f"task_type='{DELIVERY}'")
    # Without any settled message the send has stalled since it fell due.
    assert scan().signal == "late"
    provider = Provider()
    monkeypatch.setattr(family_mail_delivery_tasks, "submit_family", provider)
    with campaign_clock(due()):
        for message in OutboxMessage.objects.order_by("id")[:2]:
            single(harness, path, message.task_id)
    assert OutboxMessage.objects.filter(state="delivered").count() == 2
    sent = TaskRun.objects.filter(
        root_id__in=OutboxMessage.objects.filter(state="delivered").values("task_id")
    )
    progress = max(sent.values_list("updated_at", flat=True))
    with campaign_clock(due()), transaction.atomic():
        messages = list(OutboxMessage.objects.values_list("id", flat=True))
        (send,) = set(due_work_health.family_sends(messages).values())
        evidence = due_work_health.send_evidence(*send)
    assert (evidence.remaining, evidence.done) == (FAMILIES - 2, 2)
    assert evidence.last_progress_at == progress
    assert scan().signal == "clear"


def test_a_failed_send_read_is_unknown_and_paging_advances(
    backlog, in_progress, monkeypatch
):
    """A broken progress read leaves the page unknown; the cursor still moves."""
    from parishkit.stewardship.jobs import due_work_health

    monkeypatch.setattr(due_work_health, "_SEND_EVIDENCE", "SELECT 1/0")
    assert scan().signal == "unknown"
    first = scan(limit=1)
    assert first.cursor is not None
    assert scan(limit=1, cursor=first.cursor).cursor != first.cursor
