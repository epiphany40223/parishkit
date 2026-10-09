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
tasks queued, running or waiting to retry. Something waiting to retry counts
only once its retry is within ``RETRY_SOON`` (#868): a message the mail
provider throttled waits up to 12 hours between attempts, and ten of those
must not hold quick refreshes all day. Likewise a pending message whose
delivery Task a sending limit (the mailbox's or our own daily cap) put off
for longer: the message stays pending, but its Task waits to retry, so a
bulk send stopped at the daily cap does not hold refreshes until the cap
lifts. A reminder prepared ahead of its
due time (BG-12, #447) does not count until it is due, so it holds deltas
while it is being prepared, not for the rest of its lead window. A delta
promoted during preparation marks the population dirty, and preparation
waits for the rebuild; one promoted after preparation only makes the send
re-render. The minimum keeps a few stragglers from holding refreshes
back. Both reads are bounded by that
minimum, use the existing state indexes and take no lock. While Production
delivery is paused, preparation tasks wait for the pause to end, so only
messages count: a paused send keeps its deltas, as source refresh is meant
to continue through a pause.

Holding is bounded by the data-age alarm's own point (#510): the **overdue
full slot**, the first counted scheduled full due time after the newest
promoted full refresh started (``data_age``). The ``source_stale`` alarm
would sound ``source_stale_seconds`` after that due time; while a send is in
progress and scheduled refreshes are actually being held it allows
``SEND_ALLOWANCE`` beyond that, and the scheduler holds only until the
resume point, ``RESUME_LEAD`` before the allowance ends, so a held full
refresh can run and promote under send load before the alarm would sound.
Quick updates no longer move that point, so the bound works with quick
updates off, hourly or every 15 minutes. Sending itself never waits on
source age: Family preparation checks only that the population matches the
current source generation, which a held refresh leaves unchanged.

The relaxed alarm applies only while refreshes are actually being held: no
scheduled refresh of either kind was requested at or after the overdue
slot's due time and before the resume point, though scheduled refreshes ran
within the last day. A refresh requested in that span but not promoted (a
refresh failing or stuck) alarms at the margin as before. Requests at or
after the resume point are the scheduler's own catch-up, which the
allowance's last ``RESUME_LEAD`` exists to let finish.

Once the hold ends the catch-up full refresh keeps the allowance until it
promotes or fails (``catch_up_held``), recognized statelessly from the
scheduler's durable send-hold entry: a ``source_refresh_held`` operational
log entry with the task-free ``schedule`` schema, created at or after the
overdue slot's due time, with no scheduled full refresh requested between
that due time and the entry. A request in that span means the overdue slot
ran rather than being held: with full refreshes at 08:00 and 08:15, an
on-time 08:00 refresh that hangs is not excused by a send that then holds
the 08:15 slot. A refresh task's own held retry (lease contention, ``task``
schema) is never that evidence, and a held quick slot writes none.
"""

from datetime import timedelta

from django.db.models import DateTimeField, Exists, Func, OuterRef, Q
from django.db.models.functions import Now

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.jobs.outbox_models import OutboxMessage

from .data_age import HELD_EVENT, HELD_SCHEMA, current_overdue
from .refresh_models import SourceRefreshCommand

FAMILY_PURPOSES = ("initial", "reminder")
ACTIVE_MESSAGE_STATES = ("pending", "retry_wait", "submitting")
# The Family preparation Task type (jobs.family_mail_tasks.TASK_TYPE, pinned
# by a test); importing that module here would pull schedule planning into
# every source import.
PREPARATION_TASK_TYPE = "family_mail_prepare"
ACTIVE_PREPARATION_STATES = ("queued", "running", "retry_wait")
# A message or preparation waiting to retry counts as send work only when its
# retry is due within this long (#868). It is the cap of the ordinary retry
# schedule (jobs.family_mail_dispatch.retry_delay, pinned by a test), so a
# send working through ordinary retries still counts in full; waits for
# recipient throttling (15 minutes to 12 hours) or a mailbox limit are
# longer, and nothing competes for the work-order lock during them.
RETRY_SOON = timedelta(minutes=10)
# Fewer remaining messages and preparations than this is a send's tail, not
# a bulk send: deltas run again.
ACTIVE_MINIMUM = 10
# How much later than the lateness margin a held full refresh may be while a
# send holds scheduled refreshes. A launch-size send (about 1,100 messages at
# 10-20 a minute) takes one to two hours.
SEND_ALLOWANCE = timedelta(hours=2)
# How long before the allowance runs out the scheduler stops holding, so the
# held full refresh promotes before the alarm sounds. The spec keeps 30
# minutes only if a full refresh under send load promotes within 20. The
# measured inputs (October 2026, #440, #510): a full refresh takes a median
# 437 s idle, and send load stretched a delta from about 2.2 to about 5
# minutes (x2.3), which puts a full refresh under load near 17 minutes.
# That is an estimate; #653 measures a full refresh during a real send.
RESUME_LEAD = timedelta(minutes=30)
# Scheduled refreshes requested within this long mean the schedule is in use.
DELTA_CADENCE_WINDOW = timedelta(days=1)
# Scheduled refresh causes (a manual request is not the schedule running);
# the full ones are a scheduled full slot and the schedule-change catch-up.
SCHEDULED_FULL_CAUSES = ("nightly", "catch_up")
SCHEDULED_CAUSES = (*SCHEDULED_FULL_CAUSES, "delta")


def _waits_long(path=""):
    """A filter: the row at ``path`` waits more than ``RETRY_SOON`` to retry.

    Uses the database clock, as the retry times do.
    """
    return Q(
        **{f"{path}state": "retry_wait", f"{path}not_before__gt": Now() + RETRY_SOON}
    )


def working(rows):
    """Restrict active Task ``rows`` to work being done now or soon.

    Drops rows waiting longer than ``RETRY_SOON`` to retry; every other
    active state counts.
    """
    return rows.exclude(_waits_long())


def working_messages(rows):
    """Restrict active message ``rows`` to mail being sent now or soon.

    Drops a message waiting longer than ``RETRY_SOON`` to retry, and one
    whose delivery Task does: a sending limit (``_defer_held`` in
    jobs.family_mail_delivery_tasks, or the bulk sender's hold) puts off the
    Task and leaves the message pending. Message metadata, including
    ``task_id``, and the Task table are readable by every role that asks.
    """
    return rows.exclude(_waits_long()).exclude(_waits_long("task__"))


def family_send_active(minimum=ACTIVE_MINIMUM):
    """Whether at least ``minimum`` Family messages or preparations remain.

    Paused messages do not count: a paused send is not competing for the
    work-order lock, so refreshes need not wait for it. For the same reason
    preparation tasks do not count while Production delivery is paused:
    they stay queued, held, until the pause ends. Nor does a message whose
    occurrence is not yet due on the campaign clock (prepared ahead, BG-12),
    nor anything waiting longer than ``RETRY_SOON`` to retry, itself or
    through its delivery Task (``working``, ``working_messages``).
    """
    due = ScheduleOccurrence.objects.filter(
        pk=OuterRef("semantic_key"),
        due_at__lte=Func(
            function="stewardship_campaign_now_v1", output_field=DateTimeField()
        ),
    )
    messages = working_messages(
        OutboxMessage.objects.filter(
            Exists(due),
            purpose__in=FAMILY_PURPOSES,
            state__in=ACTIVE_MESSAGE_STATES,
            pause_hold__isnull=True,
        )
    )[:minimum].count()
    if messages >= minimum:
        return True
    if SystemConfiguration.objects.filter(
        mode="production", current_campaign__delivery_paused=True
    ).exists():
        return False
    preparations = working(
        TaskRun.objects.filter(
            task_type=PREPARATION_TASK_TYPE, state__in=ACTIVE_PREPARATION_STATES
        )
    )[: minimum - messages].count()
    return messages + preparations >= minimum


def _margin():
    """The lateness margin: how late a scheduled full refresh may be."""
    return timedelta(seconds=configured_policy().source_stale_seconds)


def resume_at(overdue):
    """When the scheduler stops holding for the full slot due at ``overdue``.

    ``RESUME_LEAD`` before the send's allowance runs out.
    """
    return overdue + _margin() + SEND_ALLOWANCE - RESUME_LEAD


def within_allowance(overdue, now):
    """Whether the full slot due at ``overdue`` is still within a send's allowance."""
    return now - overdue < _margin() + SEND_ALLOWANCE


def deltas_skipped(overdue, now):
    """Whether scheduled refreshes have been held since the slot due at ``overdue``.

    Stateless: scheduled refreshes (full or quick) were requested within the
    last day, but none at or after the overdue slot's due time and before
    the resume point. Measuring from the due time, not from when the newest
    full refresh was read, means quick updates that ran before it do not
    cancel the allowance, and held daytime full slots count as well as quick
    ones, so it works with quick updates off. A request at or after the
    resume point is the scheduler's own catch-up, not a failing refresh. The
    command table is small (about 100 rows a day) and this runs only for an
    out-of-date sample during a send.
    """
    scheduled = SourceRefreshCommand.objects.filter(cause__in=SCHEDULED_CAUSES)
    return (
        not scheduled.filter(
            created_at__gte=overdue, created_at__lt=resume_at(overdue)
        ).exists()
        and scheduled.filter(created_at__gt=now - DELTA_CADENCE_WINDOW).exists()
    )


def catch_up_held(overdue):
    """Whether the scheduler durably held a full slot since ``overdue`` fell due.

    The evidence for the catch-up's allowance: a ``source_refresh_held``
    entry with the ``schedule`` schema created at or after the overdue
    slot's due time, with no scheduled full refresh requested between that
    due time and the entry. A request in between means the overdue slot was
    not held but ran (and is late on its own account, say hung): a later
    slot's hold does not excuse it. No slot identity is derived, so a change
    of schedule, scope or time zone during the hold keeps the evidence.
    Filtering by level first uses the operational log's level/time index.

    A full or catch-up slot recorded as held in the slot decision record
    (#632) counts as well, by its due instant: due at or after the overdue
    slot's, with no scheduled full refresh requested between the two.
    """
    from .refresh_models import SourceSlotDecision

    def ran_before(field):
        """Scheduled full requests from ``overdue`` up to the outer ``field``."""
        return SourceRefreshCommand.objects.filter(
            cause__in=SCHEDULED_FULL_CAUSES,
            created_at__gte=overdue,
            created_at__lt=OuterRef(field),
        )

    return (
        OperationalLog.objects.filter(
            level="INFO",
            created_at__gte=overdue,
            event=HELD_EVENT,
            schema=HELD_SCHEMA,
        )
        .exclude(Exists(ran_before("created_at")))
        .exists()
        or SourceSlotDecision.objects.filter(
            decision="held",
            cause__in=SCHEDULED_FULL_CAUSES,
            due_at__gte=overdue,
        )
        .exclude(Exists(ran_before("due_at")))
        .exists()
    )


def allowance_applies(overdue, now, *, failed_since):
    """Whether an out-of-date full slot is a send hold rather than a fault.

    Within the send's allowance, measured from the overdue full slot, either
    a send is in progress and scheduled refreshes are actually being held,
    or the scheduler durably held a full slot since that slot fell due, so
    the refresh running now is the catch-up, which keeps the allowance until
    it promotes or fails. ``failed_since(overdue)`` says whether a source
    refresh failed critically since then: a failed catch-up alarms without
    the allowance. The catch-up rule does not require the send to still be
    in progress; it has usually ended by then.
    """
    if not within_allowance(overdue, now):
        return False
    if family_send_active() and deltas_skipped(overdue, now):
        return True
    return catch_up_held(overdue) and not failed_since(overdue)


def delta_held(now):
    """Whether the scheduler should hold a quick or daytime full slot due at ``now``.

    Only while a send is in progress and ``now`` is before the resume point
    measured from the overdue full slot; before any slot is overdue the data
    is current, so holding is safe. With no promoted full refresh yet there
    is nothing to protect. The scheduler applies the answer to quick slots
    and to full slots at a configured time other than the nightly one.
    """
    if not family_send_active():
        return False
    overdue, last_full = current_overdue(now)
    if last_full is None:
        return False
    return overdue is None or now < resume_at(overdue)
