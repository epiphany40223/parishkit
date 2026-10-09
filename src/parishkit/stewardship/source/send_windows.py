"""Windows around Family emails in which scheduled refreshes are skipped (#632).

When a refresh schedule saved with its rules asks to **skip refreshes around
Family emails**, the scheduler skips a scheduled refresh (other than the
nightly one) whose due instant falls inside a Family email's window, and
records the skip (see the background-processing spec, "Skipped around Family
emails"). A window includes its start and excludes its end:

- a reminder's runs from its lead window's start (``PREPARE_AHEAD`` before
  the due time, while it is prepared) to the end of its send;
- an initial invitation's runs from its due time to the end of its send;
- the end of a send: while the email still has at least ``ACTIVE_MINIMUM``
  pieces of active Family send work, the window stays open, but never past
  its due time plus ``SEND_ALLOWANCE``; before its due time, and once that
  work is done, it is the due time plus the **estimated send length**
  (rounded up to 15 minutes, at least an hour and at most the allowance).

Windows exist only in Production mode, while Production delivery is not
paused, for the current campaign's scheduled Family emails (the current
revision of each initial invitation and reminder). The pure functions take
every input as an argument and are unit tested without a database; the
reader below them feeds them from durable records with no lock taken.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Exists, Max, OuterRef

from .send_hold import (
    ACTIVE_MESSAGE_STATES,
    ACTIVE_MINIMUM,
    ACTIVE_PREPARATION_STATES,
    PREPARATION_TASK_TYPE,
    SEND_ALLOWANCE,
    working,
    working_messages,
)

QUARTER_HOUR = timedelta(minutes=15)
# A reminder's lead window: campaigns.family_schedule_planning.PREPARE_AHEAD,
# which a test pins this to (importing that module here would pull Family
# planning into every source import).
PREPARE_AHEAD = timedelta(hours=2)
# The estimated send length's bounds, and its value before any send has run.
MINIMUM_ESTIMATE = timedelta(hours=1)
KINDS = ("initial", "reminder")
# How far back a slot may be decided (the scheduler looks at today and
# yesterday), so how far back a window may still matter.
LOOKBACK = timedelta(days=2)


@dataclass(frozen=True)
class Email:
    """One scheduled Family email: its kind, due time and whether it is sending.

    ``sending`` says the email still has at least ``ACTIVE_MINIMUM`` pieces
    of active Family send work.
    """

    kind: str
    due_at: datetime
    sending: bool = False


@dataclass(frozen=True)
class Window:
    """``[start, end)`` around a Family email, and what it is skipped for.

    ``cause`` is "reminder_preparing", "reminder_sending" or
    "initial_sending" (``refresh_models.WINDOW_CAUSES``).
    """

    start: datetime
    end: datetime
    cause: str


def estimate(length):
    """The estimated send length from a measured one (a timedelta, or None).

    Rounded up to the quarter hour, at least ``MINIMUM_ESTIMATE`` and at most
    ``SEND_ALLOWANCE``; ``MINIMUM_ESTIMATE`` when no send has run yet.
    """
    if length is None:
        return MINIMUM_ESTIMATE
    quarters = -(-length // QUARTER_HOUR)
    return min(max(QUARTER_HOUR * quarters, MINIMUM_ESTIMATE), SEND_ALLOWANCE)


def email_windows(email, *, now, estimates):
    """The windows of one email at ``now``.

    ``estimates`` maps each kind to its estimated send length. A send that
    has active work after its due time keeps its window open up to the cap;
    otherwise its end is the estimate.
    """
    cap = email.due_at + SEND_ALLOWANCE
    if email.sending and now >= email.due_at:
        end = cap
    else:
        end = min(email.due_at + estimates[email.kind], cap)
    windows = [Window(email.due_at, end, f"{email.kind}_sending")]
    if email.kind == "reminder":
        windows.insert(
            0, Window(email.due_at - PREPARE_AHEAD, email.due_at, "reminder_preparing")
        )
    return windows


def window_cause(windows, instant):
    """The cause of the window ``instant`` falls in, or None.

    The earliest-starting window wins when several overlap, so a reminder's
    preparation is named before the send of an earlier email that overlaps.
    """
    for window in sorted(windows, key=lambda item: (item.start, item.cause)):
        if window.start <= instant < window.end:
            return window.cause
    return None


def current_windows(now):
    """Every Family email window that may contain a slot due by ``now``.

    Empty outside Production mode, without a current campaign live in
    Production (``_live``), or while Production delivery is paused. Reads the
    current campaign's scheduled initial invitations and reminders due from
    ``LOOKBACK`` plus the send allowance ago until ``PREPARE_AHEAD`` ahead,
    each one's active send work (bounded by ``ACTIVE_MINIMUM``) and the
    estimated send length of each kind. A handful of short queries; the
    scheduler reads them before it takes the work-order lock.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition

    runtime = (
        SystemConfiguration.objects.select_related("current_campaign")
        .only(
            "mode",
            "current_campaign__state",
            "current_campaign__production_cycle",
            "current_campaign__delivery_paused",
        )
        .first()
    )
    if runtime is None or runtime.mode != "production":
        return []
    campaign = runtime.current_campaign
    if not _live(campaign):
        return []
    definitions = ScheduleDefinition.objects.filter(
        campaign_id=campaign.pk,
        kind__in=KINDS,
        removed_at__isnull=True,
        current_revision__due_at__gte=now - LOOKBACK - SEND_ALLOWANCE,
        current_revision__due_at__lte=now + PREPARE_AHEAD,
    ).values_list("pk", "kind", "current_revision__due_at")
    emails = [
        Email(kind, due, due <= now and _active(pk, due, campaign.production_cycle))
        for pk, kind, due in definitions
    ]
    if not emails:
        return []
    estimates = _estimates(campaign.pk, now)
    return [
        window
        for email in emails
        for window in email_windows(email, now=now, estimates=estimates)
    ]


def _live(campaign):
    """Whether the current campaign is live in Production and not paused.

    The caller has checked the deployment's mode is Production, which only
    a confirmed Production transition sets; the campaign must also be
    active (not draft, scheduled or closed) with delivery not paused.
    """
    return (
        campaign is not None
        and campaign.state == "active"
        and not campaign.delivery_paused
    )


def _occurrences(definition_id, due_at, production_cycle=None):
    """The Production occurrences of one email, one per Family.

    ``production_cycle`` limits them to the current Production cycle.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence

    rows = ScheduleOccurrence.objects.filter(
        definition_id=definition_id, due_at=due_at, mode="production"
    )
    if production_cycle is not None:
        rows = rows.filter(production_cycle=production_cycle)
    return rows.values("pk")


def _active(definition_id, due_at, production_cycle=None, minimum=None):
    """Whether one email still has at least ``minimum`` pieces of send work.

    The bulk-send hold's states (``send_hold.family_send_active``), counted
    for this email alone: its Family messages pending (not paused), waiting
    to retry or being submitted, plus its preparation tasks queued, running
    or waiting to retry; a retry, or a delivery put off by a sending limit,
    counts only once it is near (``working``, ``working_messages``).
    """
    from parishkit.stewardship.jobs.family_mail_models import FamilyMailPreparation
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    minimum = ACTIVE_MINIMUM if minimum is None else minimum
    occurrences = _occurrences(definition_id, due_at, production_cycle)
    messages = working_messages(
        OutboxMessage.objects.filter(
            semantic_key__in=occurrences,
            mode="production",
            purpose__in=KINDS,
            state__in=ACTIVE_MESSAGE_STATES,
            pause_hold__isnull=True,
        )
    )[:minimum].count()
    if messages >= minimum:
        return True
    preparing = FamilyMailPreparation.objects.filter(
        task_id=OuterRef("pk"), occurrence_id__in=occurrences
    )
    preparations = working(
        TaskRun.objects.filter(
            Exists(preparing),
            task_type=PREPARATION_TASK_TYPE,
            state__in=ACTIVE_PREPARATION_STATES,
        )
    )[: minimum - messages].count()
    return messages + preparations >= minimum


def _estimates(campaign_id, now):
    """Each kind's estimated send length from the campaign's own sends.

    The most recent completed Production send of the same kind, else of the
    other kind: from its due time until its last Family message reached a
    final state (``send_length``). A send is completed when it has messages
    and none is still active. Before any send has run, ``MINIMUM_ESTIMATE``.
    """
    measured = {kind: _last_length(campaign_id, kind, now) for kind in KINDS}
    return {
        kind: estimate(
            measured[kind]
            if measured[kind] is not None
            else measured[KINDS[1 - KINDS.index(kind)]]
        )
        for kind in KINDS
    }


def _last_length(campaign_id, kind, now, candidates=5):
    """The length of the newest completed send of ``kind``, or None.

    Looks at the ``candidates`` newest past emails of that kind only.
    """
    from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    past = ScheduleDefinition.objects.filter(
        campaign_id=campaign_id,
        kind=kind,
        current_revision__due_at__lte=now,
    ).order_by("-current_revision__due_at")[:candidates]
    for pk, due in past.values_list("pk", "current_revision__due_at"):
        messages = OutboxMessage.objects.filter(
            semantic_key__in=_occurrences(pk, due), mode="production", purpose=kind
        )
        if (
            not messages.exists()
            or messages.filter(state__in=ACTIVE_MESSAGE_STATES).exists()
        ):
            continue
        return send_length(due, last_final(messages, due))
    return None


def last_final(messages, due):
    """The newest final-state time among ``messages`` within the allowance.

    A message that reached its final state more than ``SEND_ALLOWANCE``
    after ``due`` (a late retry, or a release after a pause) is ignored, so
    it does not stretch the estimate; None when every message did.
    """
    return messages.filter(updated_at__lte=due + SEND_ALLOWANCE).aggregate(
        last=Max("updated_at")
    )["last"]


def send_length(due, last):
    """How long a completed send took: due time to its last final message.

    ``last`` is the newest final-state time of its messages no later than
    ``SEND_ALLOWANCE`` after the due time; None when every message finished
    later than that, which means the send took at least the allowance.
    Never negative.
    """
    if last is None:
        return SEND_ALLOWANCE
    return max(last - due, timedelta())
