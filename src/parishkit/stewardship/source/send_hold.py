"""Skip scheduled refreshes while a bulk Family send is in progress (#440, #465).

Under send load a delta refresh ran about five of every fifteen minutes and
halved the send rate: its promotion and Family population rebuild compete for
the global work-order lock with Family planning, preparation and delivery, and
a promotion marks the population dirty until it is rebuilt. So while an
initial invitation or reminder is being sent, the scheduler does not create
the delta slot's refresh, nor a scheduled full refresh at any configured
daytime time other than the nightly one (#465), which competes the same way.
The nightly full refresh, an hourly or quarter-hour full refresh and every
manual refresh still run.

A send is in progress while at least ``ACTIVE_MINIMUM`` pieces of its work
remain in durable state: Family messages pending (not paused), waiting to
retry or being submitted, whose occurrence is due, plus Family preparation
tasks queued, running or waiting to retry. A reminder prepared ahead of its
due time (BG-12, #447) does not count until it is due, so it holds deltas
while it is being prepared, not for the rest of its lead window. A delta
promoted during preparation marks the population dirty, and preparation
waits for the rebuild; one promoted after preparation only makes the send
re-render. The minimum keeps a few stragglers (say, messages in a long
retry wait) from holding refreshes back. Both reads are bounded by that
minimum, use the existing state indexes and take no lock. While Production
delivery is paused, preparation tasks wait for the pause to end, so only
messages count: a paused send keeps its deltas, as source refresh is meant
to continue through a pause.

Skipping is bounded by source age. The source-staleness alarm (``health``)
allows ``SEND_ALLOWANCE`` beyond its configured threshold while a send is in
progress, and the scheduler skips only while the current source is at least
``RESUME_LEAD`` inside that same total. A send still running past it gets its
deltas again, early enough for one to finish before the alarm would sound,
and past the allowance the alarm behaves as it always did. Sending itself
never waits on source age: Family preparation checks only that the population
matches the current source generation, which a skipped refresh leaves
unchanged.

The relaxed alarm applies only while deltas are actually being skipped: no
delta was requested between the current source's read and the resume point
(``resume_at``), though deltas were in use within the last day. A delta
requested in that span but not promoted (a refresh failing or stuck), or a
frequency with no deltas (a full refresh every quarter hour), alarms at the
configured threshold as before. Deltas requested at or after the resume
point are the scheduler's own catch-up, which the allowance's last
``RESUME_LEAD`` exists to let finish, so they keep the alarm relaxed.
"""

from datetime import timedelta

from django.db.models import DateTimeField, Exists, Func, OuterRef

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.outbox_models import OutboxMessage

from .refresh_models import SourceRefreshCommand
from .snapshot_models import SourceCurrent

FAMILY_PURPOSES = ("initial", "reminder")
ACTIVE_MESSAGE_STATES = ("pending", "retry_wait", "submitting")
# The Family preparation Task type (jobs.family_mail_tasks.TASK_TYPE, pinned
# by a test); importing that module here would pull schedule planning into
# every source import.
PREPARATION_TASK_TYPE = "family_mail_prepare"
ACTIVE_PREPARATION_STATES = ("queued", "running", "retry_wait")
# Fewer remaining messages and preparations than this is a send's tail, not
# a bulk send: deltas run again.
ACTIVE_MINIMUM = 10
# How much older than the staleness threshold the source may grow while a
# send skips its deltas. A launch-size send (about 1,100 messages at 10-20 a
# minute) takes one to two hours.
SEND_ALLOWANCE = timedelta(hours=2)
# How long before the allowance runs out the scheduler stops skipping, so a
# delta (about 5 minutes under send load) promotes before the alarm sounds.
RESUME_LEAD = timedelta(minutes=30)
# Deltas requested within this long mean the delta cadence is in use.
DELTA_CADENCE_WINDOW = timedelta(days=1)


def family_send_active(minimum=ACTIVE_MINIMUM):
    """Whether at least ``minimum`` Family messages or preparations remain.

    Paused messages do not count: a paused send is not competing for the
    work-order lock, so refreshes need not wait for it. For the same reason
    preparation tasks do not count while Production delivery is paused:
    they stay queued, held, until the pause ends. Nor does a message whose
    occurrence is not yet due on the campaign clock (prepared ahead, BG-12).
    """
    due = ScheduleOccurrence.objects.filter(
        pk=OuterRef("semantic_key"),
        due_at__lte=Func(
            function="stewardship_campaign_now_v1", output_field=DateTimeField()
        ),
    )
    messages = OutboxMessage.objects.filter(
        Exists(due),
        purpose__in=FAMILY_PURPOSES,
        state__in=ACTIVE_MESSAGE_STATES,
        pause_hold__isnull=True,
    )[:minimum].count()
    if messages >= minimum:
        return True
    if SystemConfiguration.objects.filter(
        mode="production", current_campaign__delivery_paused=True
    ).exists():
        return False
    preparations = TaskRun.objects.filter(
        task_type=PREPARATION_TASK_TYPE, state__in=ACTIVE_PREPARATION_STATES
    )[: minimum - messages].count()
    return messages + preparations >= minimum


def resume_at(observed_at):
    """When the scheduler stops skipping deltas for source read at ``observed_at``.

    ``RESUME_LEAD`` before the send's allowance runs out.
    """
    stale = timedelta(seconds=configured_policy().source_stale_seconds)
    return observed_at + stale + SEND_ALLOWANCE - RESUME_LEAD


def within_allowance(observed_at, now):
    """Whether source observed at ``observed_at`` is within a send's allowance."""
    stale = timedelta(seconds=configured_policy().source_stale_seconds)
    return now - observed_at < stale + SEND_ALLOWANCE


def deltas_skipped(observed_at, now):
    """Whether delta refreshes have been skipped since ``observed_at``.

    Stateless: deltas were requested within the last day, but none after the
    current source was read (a delta's command precedes its read) and before
    the resume point. A request at or after the resume point is the
    scheduler's own catch-up delta, not a sign of a failing refresh. The
    command table is small (about 100 rows a day) and this runs only for a
    stale sample during a send.
    """
    deltas = SourceRefreshCommand.objects.filter(cause="delta")
    return (
        not deltas.filter(
            created_at__gt=observed_at, created_at__lt=resume_at(observed_at)
        ).exists()
        and deltas.filter(created_at__gt=now - DELTA_CADENCE_WINDOW).exists()
    )


def delta_held(now):
    """Whether the scheduler should skip a delta or daytime full slot due at ``now``.

    Only while a send is in progress and the current source is still
    ``RESUME_LEAD`` inside the allowance; with no promoted source yet there
    is nothing to protect. The scheduler applies the answer to delta slots
    and to full slots at a configured time other than the nightly one.
    """
    if not family_send_active():
        return False
    started_at = (
        SourceCurrent.objects.filter(singleton=True)
        .values_list("snapshot__started_at", flat=True)
        .first()
    )
    return started_at is not None and now < resume_at(started_at)
