"""Live progress of the Family email send in progress, if any (#413).

A *send* is one revision of one Family schedule definition (the invitation,
or one reminder) of the current campaign, in the current mode and Production
cycle: every Family's occurrence of that revision is one email of the send,
and every Family planning still owes that revision is one more. Moving or
editing a schedule makes a new revision and so a new send; the earlier
revision's emails stay listed on Outgoing mail but are not part of it.

The panel is a moment-in-time view. A send is *in progress* when it still has
work to do: an email not yet settled (an occurrence not yet prepared, or a
message pending, waiting to retry or being submitted), or a Family planning
still owes it (its revision is the schedule's current one and is due, and
planning can still create it), or a total that cannot be known yet. Settled
emails (sent, failed or uncertain), Families not emailed and reminders held
behind a failed or uncertain invitation are not work to do, so a send whose
remaining emails were cancelled, or which finished, is not in progress. The
panel shows the in-progress send that fell due most recently; with none, it
summarises the send whose occurrences fell due most recently.

Each Family counts once, by its newest occurrence of the send (a
deliverability recovery replaces a failed invitation with a new occurrence of
the same revision). An occurrence's email is the outbox message it points
to; before preparation there is none yet, and the email still counts as
remaining, except for a reminder whose Family's invitation failed or is
uncertain: planning holds that reminder until the invitation is resolved, so
it waits in a bucket of its own instead of keeping the send in progress.

The scheduler plans Families a few at a time, so early in a send most
Families have no occurrence yet. Those planning still owes are counted from
the Family population with the planner's own rules (``plan_family`` and
``plan_recovery``, see ``_OWED``). They are remaining, so the total is the
whole send from its first email. While the population is being refreshed,
who will be emailed is not known, and neither is the total. While planning
is held (a campaign change or restore review, or a campaign not open for
sending), the owed Families still count, and the send is marked held.

The statements read only rows and columns the web login already reads
(``runtime_grants``). They take no lock: callers run them in
``read_transaction`` (one REPEATABLE READ, READ ONLY snapshot), never in
``work_transaction``, so a page polled during the send never waits on, or
delays, the global work-order lock the send's own workers take. Each is a
bounded scan of one campaign's Family occurrences through the existing
``definition_id`` foreign-key index plus a primary-key lookup per email, or
one anti-join over the campaign's Family rows; at launch scale (about 1,100
Families) they cost a few milliseconds.
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

# The send whose occurrences fell due most recently: its definition, kind,
# revision and due time.
_LATEST_SEND = (
    "SELECT o.definition_id, d.kind, o.revision_id, o.due_at "
    "FROM stewardship_schedule_definition d "
    "JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id "
    "WHERE d.campaign_id=%(campaign)s AND d.kind IN ('initial','reminder') "
    "AND o.mode=%(mode)s AND o.production_cycle=%(cycle)s "
    "ORDER BY o.due_at DESC, o.created_at DESC, o.id DESC LIMIT 1"
)
# Every due send: each Family schedule's current revision that is due on the
# campaign clock, whether or not planning has started it. A send that is not
# the most recently due one can still be in progress: an invitation still
# sending when a reminder falls due (the reminder coalesces into it), or a
# schedule moved to an earlier time that is already due. At most one per
# Family schedule, and there are at most 100 of those (a campaign normally
# has an invitation and a few reminders).
_DUE_SENDS = (
    "SELECT d.id, d.kind, r.id, r.due_at "
    "FROM stewardship_schedule_definition d "
    "JOIN stewardship_schedule_revision r ON r.id=d.current_revision_id "
    "WHERE d.campaign_id=%(campaign)s AND d.kind IN ('initial','reminder') "
    "AND r.due_at<=public.stewardship_campaign_now_v1() "
    "ORDER BY r.due_at DESC, d.id LIMIT 100"
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
    "WHERE o.definition_id=%(definition)s AND o.revision_id=%(revision)s "
    "AND o.mode=%(mode)s AND o.production_cycle=%(cycle)s "
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
    "count(*) FILTER (WHERE bucket='remaining' AND unprepared), "
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
# What decides whether planning still owes Families this send, read on the
# campaign clock as planning reads it (``_planning_scope``):
# - planning still creates the send's emails: the send is in the current mode
#   and Production cycle, its revision is the schedule's current one and is
#   due, the campaign has not closed, and a Testing send has an active
#   rehearsal (without one, Testing planning and sending are both held, so
#   nothing more is owed);
# - planning is held for now: a restore review, a campaign change in
#   progress (an unreleased work gate), a campaign not yet started or not in
#   a sending state, or no current credentials;
# - the population is being refreshed (Family rows changed and not yet
#   re-counted), so who is owed is not known;
# - the active rehearsal, whose Testing responses suppress a Family's email.
_SCOPE = (
    "SELECT %(mode)s=s.mode "
    "AND (%(mode)s<>'production' OR %(cycle)s=c.production_cycle) "
    "AND d.current_revision_id IS NOT NULL AND r.id=%(revision)s "
    "AND r.due_at<=public.stewardship_campaign_now_v1() "
    "AND public.stewardship_campaign_now_v1()<cc.ends_at "
    "AND (%(mode)s<>'testing' OR e.id IS NOT NULL), "
    "s.restore_review_required OR k.campaign_id IS NULL "
    "OR public.stewardship_campaign_now_v1()<cc.starts_at "
    "OR (%(mode)s='testing' AND c.state<>'draft') "
    "OR (%(mode)s='production' AND c.state NOT IN ('scheduled','active','closed')) "
    "OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate g "
    "WHERE g.campaign_id=c.id AND g.state<>'released'), "
    "coalesce(k.population_dirty,false), e.id "
    "FROM stewardship_campaign c "
    "CROSS JOIN (SELECT mode, restore_review_required "
    "FROM stewardship_system_configuration LIMIT 1) s "
    "JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id "
    "JOIN stewardship_schedule_definition d "
    "ON d.id=%(definition)s AND d.campaign_id=c.id "
    "LEFT JOIN stewardship_schedule_revision r ON r.id=d.current_revision_id "
    "LEFT JOIN LATERAL (SELECT campaign_id, population_dirty, rehearsal_epoch_id "
    "FROM stewardship_campaign_credentials "
    "WHERE campaign_id=c.id AND NOT go_live_gate ORDER BY id LIMIT 1) k ON true "
    "LEFT JOIN stewardship_rehearsal_epoch e ON e.id=k.rehearsal_epoch_id "
    "AND e.campaign_id=c.id AND e.state='active' "
    "WHERE c.id=%(campaign)s"
)
# The Families planning still owes the send, by the rules of ``plan_family``
# and ``plan_recovery`` (described in the background-processing spec's
# "Family invitations and reminders"): an active, email-eligible Family with a
# deliverable address that has not responded (its effective live submission
# in Production, a submission in the active rehearsal in Testing), with no
# occurrence of the send's revision, no fulfillment of the schedule and no
# restore hold on it, and, for a reminder, whose invitation was delivered.
# An undeliverable Family is skipped once planned, and an ineligible one is
# never planned, so neither is owed. Each NOT EXISTS is an anti-join on the
# Family's target, read through the definition's own index, so the cost
# grows with the campaign's Families, not with other schedules' history.
_TARGET = "'family:'||f.id::text"
_OWED = (
    "SELECT count(*) FROM stewardship_family_campaign f "
    "WHERE f.campaign_id=%(campaign)s AND f.active AND f.email_eligible "
    "AND f.email_deliverable AND {responded} "
    "AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_occurrence o "
    "WHERE o.definition_id=%(definition)s AND o.revision_id=%(revision)s "
    "AND o.mode=%(mode)s AND o.production_cycle=%(cycle)s "
    "AND o.target=" + _TARGET + ") "
    "AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment c "
    "WHERE c.definition_id=%(definition)s AND c.mode=%(mode)s "
    "AND c.target=" + _TARGET + ") "
    "AND NOT EXISTS (SELECT 1 FROM stewardship_restore_delivery_hold h "
    "WHERE h.definition_id=%(definition)s AND h.mode=%(mode)s "
    "AND h.target=" + _TARGET + " AND h.state IN ('unreviewed','assumed_delivered'))"
    "{reminder}"
)
_RESPONDED = {
    "production": "f.effective_submission_id IS NULL",
    "testing": "NOT EXISTS (SELECT 1 FROM stewardship_submission s "
    "WHERE s.family_id=f.id AND s.mode='test' AND s.rehearsal_epoch_id=%(epoch)s)",
}
# A reminder is sent only after the Family's invitation was delivered (or a
# restore review assumed so); otherwise planning sends the invitation
# instead, or holds the reminder behind a failed or uncertain invitation.
# Like planning, only a selected (not removed) invitation schedule counts.
_INVITED = (
    " AND (EXISTS (SELECT 1 FROM stewardship_schedule_definition i "
    "JOIN stewardship_schedule_fulfillment c ON c.definition_id=i.id "
    "WHERE i.campaign_id=%(campaign)s AND i.kind='initial' "
    "AND i.current_revision_id IS NOT NULL AND c.mode=%(mode)s "
    "AND c.target=" + _TARGET + " AND c.slot='once' AND c.disposition='delivered') "
    "OR EXISTS (SELECT 1 FROM stewardship_schedule_definition i "
    "JOIN stewardship_restore_delivery_hold h ON h.definition_id=i.id "
    "WHERE i.campaign_id=%(campaign)s AND i.kind='initial' "
    "AND i.current_revision_id IS NOT NULL AND h.mode=%(mode)s "
    "AND h.target=" + _TARGET + " AND h.slot='once' "
    "AND h.state='assumed_delivered'))"
)


def owed_statement(mode, kind):
    """The ``_OWED`` count for one mode and send kind."""
    return _OWED.format(
        responded=_RESPONDED[mode], reminder=_INVITED if kind == "reminder" else ""
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

    ``remaining`` includes ``unplanned``, the Families planning still owes
    the send; ``unprepared`` is the part of ``remaining`` with no email
    prepared yet (those Families included). ``unplanned`` is None when it
    cannot be known (see the module docstring): ``remaining`` then counts
    only the planned Families, and the send's total is unknown. ``held``
    is whether planning is held for now.
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
    unprepared: int = 0
    unplanned: int | None = 0
    held: bool = False

    @property
    def outbox_failed(self):
        """Failed emails that exist, so Outgoing mail can show them."""
        return self.failed - self.prepare_failed

    @property
    def in_progress(self):
        """Whether the send still has work to do (see the module docstring)."""
        return self.remaining > 0 or self.unplanned is None

    @property
    def not_sent(self):
        """Families the send did not email: unreachable or no longer needed."""
        return self.unreachable + self.not_needed


def read_send(campaign_id, mode, cycle, now):
    """Read the send in progress, else the most recent send, else None.

    The send in progress that fell due most recently is returned. Every due
    send (each schedule's current revision, started or not) is considered as
    well as the send whose occurrences fell due most recently. So a moved
    schedule's new due time, or a new send's first minute, shows as soon as
    Families are owed, and an invitation still sending is not hidden by a
    reminder that fell due after it. With nothing in
    progress, the most recent send with occurrences is returned (its
    ``in_progress`` is False), for the panel's one-line summary.

    ``cycle`` is the campaign's Production cycle in Production and 0 in
    Testing, so a campaign returned to Testing and confirmed again shows only
    its current cycle's sends. The caller owns the (read-only) transaction.
    """
    if not isinstance(campaign_id, UUID):
        raise TypeError("A send belongs to one campaign.")
    values = {
        "campaign": campaign_id,
        "mode": mode,
        "cycle": cycle,
        "since": now - RATE_WINDOW,
        "unreachable": list(UNREACHABLE_REASONS),
    }
    with connection.cursor() as cursor:
        cursor.execute(_LATEST_SEND, values)
        latest = cursor.fetchone()
        cursor.execute(_DUE_SENDS, values)
        # The latest send is usually also a due current revision: count it once.
        sends = [
            send
            for send in cursor.fetchall()
            if latest is None or (send[0], send[2]) != (latest[0], latest[2])
        ]
        if latest is not None:
            sends.append(latest)
        # Newest due first; on a tie the send already under way goes first.
        sends.sort(key=lambda send: (send[3], send is latest), reverse=True)
        last = None
        for definition, kind, revision, _ in sends:
            counts = _count(
                cursor,
                values | {"definition": definition, "revision": revision},
                kind,
                now,
            )
            if counts.in_progress:
                return counts
            if latest is not None and (definition, revision) == (latest[0], latest[2]):
                last = counts
    return last


def _count(cursor, values, kind, now):
    """Count one send: its occurrences, held reminders and owed Families."""
    cursor.execute(_COUNTS, values)
    (
        sent,
        failed,
        prepare_failed,
        uncertain,
        remaining,
        unprepared,
        unreachable,
        not_needed,
        started_at,
        last_settled_at,
        recent,
    ) = cursor.fetchone()
    waiting = 0
    if kind == "reminder" and unprepared:
        cursor.execute(_WAITING, values)
        waiting = cursor.fetchone()[0]
    unplanned, held = _owed(cursor, values, kind)
    return SendCounts(
        kind=kind,
        sent=sent,
        failed=failed,
        prepare_failed=prepare_failed,
        uncertain=uncertain,
        remaining=remaining - waiting + (unplanned or 0),
        waiting=waiting,
        unreachable=unreachable,
        not_needed=not_needed,
        started_at=started_at,
        last_settled_at=last_settled_at,
        recent=recent,
        since=values["since"],
        now=now,
        unprepared=unprepared - waiting + (unplanned or 0),
        unplanned=unplanned,
        held=held,
    )


def _owed(cursor, values, kind):
    """(Families planning still owes the send or None if unknowable, held).

    Zero when planning creates no more of the send's emails (see
    ``_SCOPE``), so an old revision, a closed campaign, a schedule not yet
    due and a Testing send without an active rehearsal all owe nothing.
    None while the population is being refreshed.
    """
    cursor.execute(_SCOPE, values)
    row = cursor.fetchone()
    if row is None or not row[0]:
        return 0, False
    _, held, dirty, epoch = row
    if dirty:
        return None, held
    cursor.execute(owed_statement(values["mode"], kind), values | {"epoch": epoch})
    return cursor.fetchone()[0], held


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

    ``total`` counts the Families planning has not reached yet, so it is the
    whole send from its first email. When they cannot be counted
    (``SendCounts.unplanned`` is None), ``total`` and ``percent`` are None,
    rather than a share of the emails planned so far, and there is no finish
    estimate; the send stays active while its total is unknown.
    """

    counts: SendCounts
    total: int | None
    done: int
    percent: int | None
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
    known = counts.unplanned is not None
    total = done + counts.remaining if known else None
    active = counts.in_progress
    percent = None if not known else 100 if total == 0 else done * 100 // total
    rate = finish_at = minutes_left = finished_at = None
    if active:
        # A send younger than the window is measured from its own start.
        start = max(counts.since, counts.started_at or counts.now)
        rate = _per_minute(counts.recent, counts.now - start)
        # Without the total there is nothing to estimate the finish from.
        if rate and known and not paused:
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
