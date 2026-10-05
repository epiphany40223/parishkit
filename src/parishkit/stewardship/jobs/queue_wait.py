"""Why a queued task has not started yet, in words an Administrator can use.

Each worker consumer process executes one message at a time, so a queued task
waits while another task holds the process that consumes its queue (#340).
Since #336 the worker container normally runs ParishSoft source work on its
own process (see ``runtime_process.split_source``): a refresh then never
delays an export, and this module must not say it does. Only a worker login
whose connection limit is too small for two processes keeps one process on
every worker queue. That limit is read from ``pg_roles`` on every call rather
than from the web's own configuration, so a web process started before a
budget change cannot disagree with the worker that is actually running.

The answer comes from rows the web login already reads (task runs, their
claim events and ``pg_roles``); nothing here reads the broker, writes a row
or records an audit event, so status pages can poll it freely.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from parishkit.stewardship.deployment import ServiceRole

from .models import TaskRun, TaskRunEvent
from .ownership import database_now
from .queues import ROLE_QUEUES, SOURCE_QUEUES, SOURCE_SPLIT_CONNECTIONS, WorkQueue

# The queue each task type's handler names, as the web cannot import the
# worker's registry. tests/stewardship/test_queue_wait.py pins this against
# the handlers themselves so the two cannot drift; a type missing here is
# never named as the task ahead and only gets the generic reason.
TASK_QUEUES = {
    "activation_catchup": WorkQueue.GENERAL,
    "automation_maintenance": WorkQueue.GENERAL,
    "branding_cleanup": WorkQueue.GENERAL,
    "campaign_boundary": WorkQueue.GENERAL,
    "daily_digest_finalize": WorkQueue.GENERAL,
    "daily_digest_prepare": WorkQueue.GENERAL,
    "family_mail_prepare": WorkQueue.GENERAL,
    "family_mail_test": WorkQueue.GENERAL,
    "operational_collect": WorkQueue.GENERAL,
    "production_cleanup": WorkQueue.GENERAL,
    "production_token_cleanup": WorkQueue.GENERAL,
    "production_tokens": WorkQueue.GENERAL,
    "report_exact_export": WorkQueue.GENERAL,
    "report_export": WorkQueue.GENERAL,
    "report_export_cleanup": WorkQueue.GENERAL,
    "report_fact_verification": WorkQueue.GENERAL,
    "report_facts": WorkQueue.GENERAL,
    "weekly_digest_finalize": WorkQueue.GENERAL,
    "weekly_digest_prepare": WorkQueue.GENERAL,
    "setup_mail_test": WorkQueue.MAIL,
    "setup_finalize": WorkQueue.SOURCE,
    "setup_source_cleanup": WorkQueue.SOURCE,
    "setup_source_load": WorkQueue.SOURCE,
    "source_refresh": WorkQueue.SOURCE,
}

# Running tasks worth naming, phrased to follow "Waiting for". Any other
# running task ahead gets the generic "other background work" reason.
BLOCKER_NAMES = {
    "source_refresh": _("the ParishSoft update"),
    "setup_source_load": _("the initial ParishSoft data load"),
    "setup_finalize": _("the final initial-setup step"),
    "setup_source_cleanup": _("the initial setup cleanup"),
    "report_export": _("another report export"),
    "report_exact_export": _("another report export"),
    "report_facts": _("the report totals update"),
    "report_fact_verification": _("the report totals check"),
    "daily_digest_prepare": _("the daily report email"),
    "daily_digest_finalize": _("the daily report email"),
    "weekly_digest_prepare": _("the weekly report email"),
    "weekly_digest_finalize": _("the weekly report email"),
    "family_mail_prepare": _("Family email preparation"),
}


@dataclass(frozen=True)
class QueueWait:
    """A queued task's waiting time and, when known, the running task ahead."""

    since: datetime
    waited: str
    blocker: str | None = None
    blocker_started: datetime | None = None


def consumer_queues(queue, *, split):
    """The queues one consumer process takes together with ``queue``.

    With ``split`` the worker's source queue has its own process and the
    worker's other queues share the main process; without it, one process
    takes every worker queue. Other roles run one process for their queues.
    """
    for role, queues in ROLE_QUEUES.items():
        if queue in queues:
            if role is not ServiceRole.WORKER or not split:
                return queues
            return SOURCE_QUEUES if queue in SOURCE_QUEUES else queues - SOURCE_QUEUES
    return frozenset()


def source_split():
    """Whether the running worker has source work on its own process.

    The worker splits when its login's connection limit is at least
    ``SOURCE_SPLIT_CONNECTIONS`` (``runtime_process.split_source``), and both
    ``database-grants`` and worker startup refuse a limit that differs from
    the worker's budget, so a running worker agrees with ``pg_roles``. Only a
    limit proven too small shares the source queue with general work; a
    missing login or an unlimited (-1) one proves nothing, so a refresh is
    then never named as the task ahead.
    """
    from parishkit.stewardship.runtime_grants import login_name

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolconnlimit FROM pg_roles WHERE rolname=%s",
            [login_name(ServiceRole.WORKER)],
        )
        row = cursor.fetchone()
    return row is None or not 0 <= row[0] < SOURCE_SPLIT_CONNECTIONS


def waited_words(elapsed):
    """How long ago, in the same words live-status-v1.js ticks forward.

    Whole units only, rounded down: 90 seconds is "1 minute ago", not 2, and
    ten hours is "10 hours ago", not "600 minutes ago".
    """
    seconds = max(0, int(elapsed.total_seconds()))
    if seconds < 60:
        return ngettext("%(count)d second ago", "%(count)d seconds ago", seconds) % {
            "count": seconds
        }
    if seconds < 3600:
        minutes = seconds // 60
        return ngettext("%(count)d minute ago", "%(count)d minutes ago", minutes) % {
            "count": minutes
        }
    hours = seconds // 3600
    return ngettext("%(count)d hour ago", "%(count)d hours ago", hours) % {
        "count": hours
    }


def queue_wait(runs, *, named=True):
    """Explain the newest run in ``runs`` while it is queued; else None.

    ``runs`` is a TaskRun queryset naming one retry chain; the newest retry
    is the one a status page shows. See ``explain`` for ``named``.
    """
    with transaction.atomic():
        return explain(runs.order_by("-retry_sequence").first(), named=named)


def explain(run, *, named=True, split=None):
    """Explain ``run`` while it is queued; else None.

    ``run`` has a TaskRun's ``pk``, ``task_type``, ``state``, ``created_at``
    and ``not_before`` (a model row, or a page's already-read metadata). A
    claimed or finished run returns None, so its page shows its normal text
    again. The run waits since it was created or became due, whichever is
    later. A running task with a live lease on the same consumer process is
    named when this module knows both task types' queues and has a name for
    the running one, and only for a reader who may see background work
    (``named``); otherwise the reason is generic. One short transaction keeps
    the database clock (``database_now``) and the reads together. ``split``
    overrides the worker's real process split for tests.
    """
    if run is None or run.state != "queued":
        return None
    with transaction.atomic():
        now = database_now()
        since = max(run.created_at, min(run.not_before, now))
        waited = waited_words(now - since)
        queue = TASK_QUEUES.get(run.task_type)
        if queue is None or not named:
            return QueueWait(since, waited)
        shared = consumer_queues(
            queue, split=source_split() if split is None else split
        )
        ahead = (
            TaskRun.objects.filter(
                state="running",
                lease_expires_at__gt=now,
                task_type__in=[
                    name
                    for name, routed in TASK_QUEUES.items()
                    if routed in shared and name in BLOCKER_NAMES
                ],
            )
            .exclude(pk=run.pk)
            .order_by("created_at", "id")
            .first()
        )
        if ahead is None:
            return QueueWait(since, waited)
        # The current attempt's claim event says when it started running; the
        # run's own creation time is only a fallback (it may have queued long).
        started = (
            TaskRunEvent.objects.filter(
                run=ahead, action="claim", attempt=ahead.attempt
            )
            .values_list("created_at", flat=True)
            .first()
        )
        return QueueWait(
            since,
            waited,
            blocker=BLOCKER_NAMES[ahead.task_type],
            blocker_started=started or ahead.created_at,
        )
