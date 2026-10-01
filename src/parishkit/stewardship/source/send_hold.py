"""Skip 15-minute delta refreshes while a bulk Family send is in progress (#440).

Under send load a delta refresh ran about five of every fifteen minutes and
halved the send rate: its promotion and Family population rebuild compete for
the global work-order lock with Family planning, preparation and delivery, and
a promotion marks the population dirty until it is rebuilt. So while an
initial invitation or reminder is being sent, the scheduler does not create
the delta slot's refresh. The scheduled full refresh and every manual refresh
still run.

A send is in progress while at least ``ACTIVE_MINIMUM`` pieces of its work
remain in durable state: Family messages pending (not paused), waiting to
retry or being submitted, plus Family preparation tasks queued, running or
waiting to retry. The minimum keeps a few stragglers (say, messages in a long
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
delta was requested since the current source was read, though deltas were in
use within the last day. A delta that was requested but has not promoted (a
refresh failing or stuck), or a frequency with no deltas (a full refresh
every quarter hour), alarms at the configured threshold as before.
"""

from datetime import timedelta

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
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
    they stay queued, held, until the pause ends.
    """
    messages = OutboxMessage.objects.filter(
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


def within_allowance(observed_at, now):
    """Whether source observed at ``observed_at`` is within a send's allowance."""
    stale = timedelta(seconds=configured_policy().source_stale_seconds)
    return now - observed_at < stale + SEND_ALLOWANCE


def deltas_skipped(observed_at, now):
    """Whether delta refreshes have been skipped since ``observed_at``.

    Stateless: deltas were requested within the last day, but none since the
    current source was read (a delta's command precedes its read). The
    command table is small (about 100 rows a day) and this runs only for a
    stale sample during a send.
    """
    deltas = SourceRefreshCommand.objects.filter(cause="delta")
    return (
        not deltas.filter(created_at__gt=observed_at).exists()
        and deltas.filter(created_at__gt=now - DELTA_CADENCE_WINDOW).exists()
    )


def delta_held(now):
    """Whether the scheduler should skip a delta slot due at ``now``.

    Only while a send is in progress and the current source is still
    ``RESUME_LEAD`` inside the allowance; with no promoted source yet there
    is nothing to protect.
    """
    if not family_send_active():
        return False
    started_at = (
        SourceCurrent.objects.filter(singleton=True)
        .values_list("snapshot__started_at", flat=True)
        .first()
    )
    return started_at is not None and within_allowance(started_at - RESUME_LEAD, now)
