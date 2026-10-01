"""Live progress of the current or most recent Family email send (#413).

A *send* is one Family schedule definition (the invitation, or one reminder)
of the current campaign, in the current mode and Production cycle: every
Family's occurrence of that definition is one email of the send. The current
send is the one whose occurrences fell due most recently, so a reminder
replaces the invitation on the panel once its first email is due, while a
late-joining Family's invitation (due at the invitation's original time) does
not pull the panel back to the invitation.

Each Family counts once, by its newest occurrence (a deliverability recovery
replaces a failed invitation with a new occurrence of the same definition).
An occurrence's email is the outbox message it points to; before preparation
there is none yet, and the email still counts as remaining, except for a
reminder whose Family's invitation failed or is uncertain: planning holds
that reminder until the invitation is resolved, so it waits in a bucket of
its own instead of keeping the send from ever finishing.

The statements read only rows and columns the web login already reads
(``runtime_grants``). They take no lock: callers run them in
``read_transaction`` (one REPEATABLE READ, READ ONLY snapshot), never in
``work_transaction``, so a page polled during the send never waits on, or
delays, the global work-order lock the send's own workers take. Each is a
bounded scan of one campaign's Family occurrences through the existing
``definition_id`` foreign-key index plus a primary-key lookup per email; at
launch scale (about 1,100 Families) they cost a few milliseconds.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.db import connection
from django.db.models import Exists, OuterRef

FAMILY_KINDS = ("initial", "reminder")
# The current rate counts emails settled within this recent window.
RATE_WINDOW = timedelta(minutes=5)
# Too short a sample makes a rate (and a finish estimate) meaningless.
MINIMUM_SAMPLE = timedelta(seconds=30)
TENTH = Decimal("0.1")
# A Family send due within this much of now, with nothing scheduled for it
# yet, is about to start: an open page keeps checking for it.
UPCOMING = timedelta(hours=1)
# Skip and cancel reasons that mean the Family could not be emailed at all,
# as opposed to no longer needing the email (it responded, a later email
# replaced this one, or the campaign closed).
UNREACHABLE_REASONS = ("no_deliverable_recipient", "family_ineligible")

# Picks the send: the Family definition whose occurrence fell due last.
_CURRENT = (
    "SELECT o.definition_id, d.kind "
    "FROM stewardship_schedule_definition d "
    "JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id "
    "WHERE d.campaign_id=%(campaign)s AND d.kind IN ('initial','reminder') "
    "AND o.mode=%(mode)s AND o.production_cycle=%(cycle)s "
    "ORDER BY o.due_at DESC, o.created_at DESC, o.id DESC LIMIT 1"
)
# An occurrence's email, read by primary key. The LIMIT keeps it a per-row
# index lookup: as the outbox grows (it is never purged), a plain join may be
# planned as a scan of every message ever sent instead.
_EMAIL = (
    "SELECT m.id, m.state, m.created_at, m.updated_at "
    "FROM stewardship_outbox_message m WHERE m.id={}.outbox_id LIMIT 1"
)
# Each Family's newest occurrence of the send.
_LATEST = (
    "SELECT DISTINCT ON (o.target) o.target, o.state, o.reason, o.outbox_id "
    "FROM stewardship_schedule_occurrence o "
    "WHERE o.definition_id=%(definition)s AND o.mode=%(mode)s "
    "AND o.production_cycle=%(cycle)s "
    "ORDER BY o.target, o.recovery_generation DESC, o.created_at DESC, o.id DESC"
)
# Counts one send. The message state, when there is a message, decides the
# bucket, and otherwise the occurrence state does (not yet prepared, failed
# preparation, or not emailed, by its skip or cancel reason).
_COUNTS = (
    "WITH latest AS (" + _LATEST + "), emails AS ("
    "SELECT CASE "
    "WHEN m.id IS NULL THEN CASE "
    "WHEN l.state IN ('skipped','coalesced') AND l.reason=ANY(%(unreachable)s) "
    "THEN 'unreachable' "
    "WHEN l.state IN ('skipped','coalesced') THEN 'not_needed' "
    "WHEN l.state='failed' THEN 'failed' "
    "WHEN l.state='delivery_unknown' THEN 'uncertain' "
    "WHEN l.state='succeeded' THEN 'sent' "
    "ELSE 'remaining' END "
    "WHEN m.state='delivered' THEN 'sent' "
    "WHEN m.state='permanent_failure' THEN 'failed' "
    "WHEN m.state='delivery_unknown' THEN 'uncertain' "
    "WHEN m.state='cancelled' AND l.reason=ANY(%(unreachable)s) THEN 'unreachable' "
    "WHEN m.state='cancelled' THEN 'not_needed' "
    "ELSE 'remaining' END AS bucket, "
    "m.id IS NULL AS unprepared, m.created_at, m.updated_at "
    "FROM latest l LEFT JOIN LATERAL (" + _EMAIL.format("l") + ") m ON true"
    ") SELECT "
    "count(*) FILTER (WHERE bucket='sent'), "
    "count(*) FILTER (WHERE bucket='failed'), "
    "count(*) FILTER (WHERE bucket='failed' AND unprepared), "
    "count(*) FILTER (WHERE bucket='uncertain'), "
    "count(*) FILTER (WHERE bucket='remaining'), "
    "count(*) FILTER (WHERE bucket='unreachable'), "
    "count(*) FILTER (WHERE bucket='not_needed'), "
    "min(created_at), "
    "max(updated_at) FILTER (WHERE bucket IN ('sent','failed','uncertain')), "
    "count(*) FILTER (WHERE bucket IN ('sent','failed','uncertain') "
    "AND updated_at>=%(since)s) "
    "FROM emails"
)
# For a reminder only: how many of its not-yet-prepared emails wait behind
# the Family's newest invitation, because that invitation failed or is
# uncertain (schedule_recovery's initial_unfulfilled and
# delivery_unresolved). Planning holds those reminders until the invitation
# is resolved, so they would otherwise stay "remaining" forever. Only the
# unprepared reminders' Families' invitations are read.
_WAITING = (
    "WITH latest AS (" + _LATEST + "), unprepared AS ("
    "SELECT target FROM latest "
    "WHERE outbox_id IS NULL AND state IN ('pending','running')"
    "), invitation AS ("
    "SELECT DISTINCT ON (o.target) "
    "(o.state IN ('failed','delivery_unknown') "
    "OR m.state IN ('permanent_failure','delivery_unknown')) AS undelivered "
    "FROM stewardship_schedule_definition d "
    "JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id "
    "LEFT JOIN LATERAL (" + _EMAIL.format("o") + ") m ON true "
    "WHERE d.campaign_id=%(campaign)s AND d.kind='initial' "
    "AND o.mode=%(mode)s AND o.production_cycle=%(cycle)s "
    "AND o.target IN (SELECT target FROM unprepared) "
    "ORDER BY o.target, o.recovery_generation DESC, o.created_at DESC, o.id DESC"
    ") SELECT count(*) FILTER (WHERE undelivered) FROM invitation"
)


@dataclass(frozen=True)
class SendCounts:
    """What one read of a send observed, per Family; ``progress`` derives the rest.

    ``prepare_failed`` is the part of ``failed`` that failed before an email
    existed (so Outgoing mail cannot show it). ``waiting`` counts reminders
    held until the Family's failed or uncertain invitation is resolved;
    ``unreachable`` Families could not be emailed (no deliverable address, or
    no longer eligible); ``not_needed`` emails were dropped because the
    Family responded, a later email replaced them or the campaign closed.
    ``recent`` counts emails settled (sent, failed or uncertain) since
    ``since``. ``started_at`` is when the send's first email was prepared,
    and ``last_settled_at`` when its most recent email settled.
    """

    kind: str
    sent: int
    failed: int
    prepare_failed: int
    uncertain: int
    remaining: int
    waiting: int
    unreachable: int
    not_needed: int
    started_at: datetime | None
    last_settled_at: datetime | None
    recent: int
    since: datetime
    now: datetime

    @property
    def outbox_failed(self):
        """Failed emails that exist, so Outgoing mail can show them."""
        return self.failed - self.prepare_failed


def read_send(campaign_id, mode, cycle, now):
    """Count the current Family send, or return None when there has been none.

    ``cycle`` is the campaign's Production cycle in Production and 0 in
    Testing, so a campaign returned to Testing and confirmed again shows only
    its current cycle's sends. The caller owns the (read-only) transaction.
    """
    if not isinstance(campaign_id, UUID):
        raise TypeError("A send belongs to one campaign.")
    since = now - RATE_WINDOW
    values = {
        "campaign": campaign_id,
        "mode": mode,
        "cycle": cycle,
        "since": since,
        "unreachable": list(UNREACHABLE_REASONS),
    }
    with connection.cursor() as cursor:
        cursor.execute(_CURRENT, values)
        current = cursor.fetchone()
        if current is None:
            return None
        values["definition"] = current[0]
        cursor.execute(_COUNTS, values)
        sent, failed, prepare_failed, uncertain, remaining, *rest = cursor.fetchone()
        waiting = 0
        if current[1] == "reminder":
            cursor.execute(_WAITING, values)
            waiting = cursor.fetchone()[0]
    return SendCounts(
        current[1],
        sent,
        failed,
        prepare_failed,
        uncertain,
        remaining - waiting,
        waiting,
        *rest,
        since,
        now,
    )


def upcoming(campaign, mode, cycle, now):
    """Whether a Production Family send is about to start (or just started).

    True while the campaign's activation catch-up (which schedules the
    invitations after go-live) is unfinished, or while an invitation or
    reminder is due within an hour either side of now and nothing has been
    scheduled for it yet. An open page then keeps checking, so it switches
    to the send by itself. Testing sends are not followed this way.
    """
    from parishkit.stewardship.campaigns.runtime_models import (
        ActivationCatchUpDemand,
    )
    from parishkit.stewardship.campaigns.schedule_models import (
        ScheduleDefinition,
        ScheduleOccurrence,
    )

    if mode != "production" or campaign.state in {"closed", "archived"}:
        return False
    if ActivationCatchUpDemand.objects.filter(
        campaign_id=campaign.pk, completed_at__isnull=True
    ).exists():
        return True
    scheduled = ScheduleOccurrence.objects.filter(
        definition_id=OuterRef("pk"), mode=mode, production_cycle=cycle
    )
    return (
        ScheduleDefinition.objects.filter(
            campaign_id=campaign.pk,
            kind__in=FAMILY_KINDS,
            removed_at__isnull=True,
            current_revision__due_at__gt=now - UPCOMING,
            current_revision__due_at__lte=now + UPCOMING,
        )
        .exclude(Exists(scheduled))
        .exists()
    )


@dataclass(frozen=True)
class SendProgress:
    """A send's counts with its progress, rate and timing, ready to show.

    ``total`` is the emails this send is sending: sent, failed, uncertain
    and remaining. Reminders waiting for their invitation, Families that
    could not be emailed and emails no longer needed are shown beside it,
    not in it, so the send can finish. ``done`` counts those settled (sent,
    failed or uncertain). ``rate`` is emails settled per minute, to one
    decimal place: over the recent window while the send runs, over the
    whole send once it has finished, and None until there is enough to
    measure. ``finish_at`` and ``minutes_left`` estimate when the remaining
    emails will have settled at that rate; there is no estimate while
    delivery is paused or while nothing is settling. ``stalled`` is a
    running send with nothing settled in the recent window.
    """

    counts: SendCounts
    total: int
    done: int
    percent: int
    active: bool
    paused: bool
    stalled: bool
    rate: Decimal | None
    finish_at: datetime | None
    minutes_left: int | None
    finished_at: datetime | None


def _per_minute(count, elapsed):
    """``count`` per minute over ``elapsed``, or None for too short a sample."""
    if elapsed < MINIMUM_SAMPLE:
        return None
    return (Decimal(count) * 60 / Decimal(elapsed.total_seconds())).quantize(
        TENTH, ROUND_HALF_UP
    )


def progress(counts, *, paused=False):
    """Derive progress, rate and finish estimate from one read.

    ``paused`` is whether live delivery is paused now (the campaign's pause
    control, not whether messages have been held yet: a worker holds each
    message only when it reaches it). Pure, so the arithmetic (empty,
    running, stalled, paused and finished sends) is unit tested without a
    database. The percentage rounds down, so a send shows 100% only when
    nothing remains.
    """
    done = counts.sent + counts.failed + counts.uncertain
    total = done + counts.remaining
    active = counts.remaining > 0
    percent = 100 if total == 0 else done * 100 // total
    rate = finish_at = minutes_left = finished_at = None
    if active:
        # A send younger than the window is measured from its own start.
        start = max(counts.since, counts.started_at or counts.now)
        rate = _per_minute(counts.recent, counts.now - start)
        if rate and not paused:
            minutes_left = math.ceil(counts.remaining / rate)
            finish_at = counts.now + timedelta(minutes=minutes_left)
    elif counts.last_settled_at is not None:
        finished_at = counts.last_settled_at
        if counts.started_at is not None:
            rate = _per_minute(done, finished_at - counts.started_at)
    return SendProgress(
        counts=counts,
        total=total,
        done=done,
        percent=percent,
        active=active,
        paused=active and paused,
        stalled=active and rate is not None and rate == 0,
        rate=rate,
        finish_at=finish_at,
        minutes_left=minutes_left,
        finished_at=finished_at,
    )
