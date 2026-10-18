"""Clear a halted mail sender: the Administrator's side (ADM-13 PR 4a, #530).

The service functions System health's **Clear the halt** (PR 4b) and the
``system mail-clear-preview`` and ``system mail-clear`` commands share
(admin-portal spec, "Clear a halted mail sender"):

- ``preview_clear`` reads the current halt, the waiting and unsettled
  emails, whether delivery is paused and the newest mailbox check, and asks
  for a new mailbox check when none is live and none passed recently
  (``request_check``). While the halt can be cleared it carries a signed
  binding naming the halt (``token``).
- ``clear_halt`` records the clear signal for the halt the binding names,
  with ``mail_halt_cleared``. The mail consumers act on it from their idle
  loop (``jobs.mail_halt``).

Every check here runs again in the clear signal's SQL guard (frozen
migration 0115), in the signal's own transaction under its advisory lock;
this module's checks only give the reader the reason in words first. No
recent sign-in is required (#1547): an enabled Administrator with a live
session may clear a halt, from the page or the command line.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

from .admin_reads import ReadModel, plain

# How long a mailbox check is live (waiting or running) before it counts as
# unanswered, and how long a passed one allows a clear: the guards' windows.
LIVE_CHECK = timedelta(minutes=2)
PASSED_CHECK = timedelta(minutes=5)
# A mail consumer whose status record is older than this is not running
# (service_status.NOT_RUNNING_SECONDS), so its Halted no longer counts.
REPORTED = timedelta(minutes=3)
# A halt is current only if it began no earlier than this before a halted
# consumer's status record started (the record starts when the process is
# admitted, before it sends; the slack covers the halt's dating back).
STARTED_SLACK = timedelta(minutes=1)
# The signed preview binding's salt and lifetime (the shared preview
# lifetime, as Accept this change once's).
CLEAR_SALT = "parishkit.stewardship.mail_halt_clear"
CLEAR_MAX_AGE = 15 * 60
# At most this many unsettled message ids are listed; the count is exact.
UNSETTLED_LISTED = 50
# The critical-problems banner's entries the halt raised: its banner events'
# CRITICAL entries from this long before the halt began (the outcome that
# caused it is stored a moment before the consumer halts) up to the
# preview, at most the banner's own 500 (admin-portal spec, "An action
# that resolves a problem clears its banner entries").
HALT_ENTRIES_BEFORE = timedelta(minutes=10)
HALT_ENTRIES_LIMIT = 500
# How long after a clear (or after a mail sender began reporting Halted) the
# senders may still take to lift, or the halt to be recorded: the consumers
# poll about every 30 seconds and a status record is up to 10 more behind.
# Past this a Halted sender is not explained by timing, and the page says to
# restart the mail sender (runbook "Mail-provider outage").
LIFT_WINDOW = timedelta(minutes=2)

# Why Clear the halt cannot be used now, in the order they are checked, and
# their words for the page and the command line.
REASONS = {
    "not_halted": "No mail sender is halted, or its halt is not recorded yet.",
    "checking": "The mailbox check is still running.",
    "check_failed": "The mailbox check did not pass.",
    "check_needed": "No mailbox check passed in the last five minutes.",
    "unsettled": (
        "Some emails may have arrived and are not settled yet; settle them on "
        "Sent mail first."
    ),
}
# What a confirmation answers when the halt shown can no longer be cleared.
HALT_CHANGED = "The mail halt changed since it was reviewed."


class ClearRefused(PermissionError):
    """The database refused a clear for a reason other than its own checks (a
    missing grant, another login): the reader is told plainly, and the System
    logs record why."""


@dataclass(frozen=True)
class Halt:
    """The current halt: its identity, kind and when it began."""

    halt_id: object
    kind: str
    began_at: datetime


@dataclass(frozen=True)
class MailboxCheckStatus:
    """The newest mailbox check, as the preview words it.

    ``status`` is ``waiting``, ``running``, ``passed``, ``refused``,
    ``unavailable`` or ``unanswered`` (live too long, see ``LIVE_CHECK``).
    """

    check_id: object
    status: str
    created_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None

    @property
    def live(self):
        """Whether the mail sender may still answer this check."""
        return self.status in {"waiting", "running"}

    def allows(self, halt, now):
        """Whether this check allows clearing ``halt`` at ``now``: it passed,
        was claimed after the halt began, and finished within PASSED_CHECK."""
        return (
            self.status == "passed"
            and self.claimed_at >= halt.began_at
            and self.finished_at > now - PASSED_CHECK
        )


def check_status(row, now):
    """``row`` (a ``MailboxCheck``, or None) as a ``MailboxCheckStatus``."""
    if row is None:
        return None
    status = row.state
    if status in {"waiting", "running"} and row.created_at <= now - LIVE_CHECK:
        status = "unanswered"
    return MailboxCheckStatus(
        check_id=row.pk,
        status=status,
        created_at=row.created_at,
        claimed_at=row.claimed_at,
        finished_at=row.finished_at,
    )


def current_halt(now):
    """The current halt, or None, in the caller's transaction.

    The newest recorded halt with no clear signal that began while a mail
    consumer now reporting Halted (within three minutes) was running, with
    ``STARTED_SLACK`` for the halt's dating back: the rule the clear
    signal's guard applies. Taking the newest *uncleared* halt means that if
    the two consumers ever hold different halts (one could not write or
    read the shared marker), clearing one leaves the other's current, so it
    can be cleared next without a restart. The start rule keeps a halt left
    from before a restart from being named once the new process reports; in
    the three minutes the old process's record still counts it may be, and a
    clear of it changes nothing, because each consumer lifts only its own.
    """
    from django.db.models import Exists, F, OuterRef

    from .jobs.mail_halt_models import MailHalt, MailHaltClear
    from .jobs.service_status_models import ServiceStatus

    halted = ServiceStatus.objects.filter(
        service="mail-dispatch",
        sender_state="halted",
        reported_at__gt=now - REPORTED,
        started_at__lte=OuterRef("began_at") + STARTED_SLACK,
    )
    newest = (
        MailHalt.objects.filter(Exists(halted))
        .exclude(Exists(MailHaltClear.objects.filter(halt_id=OuterRef("pk"))))
        .order_by(F("began_at").desc(), F("id").desc())
        .first()
    )
    if newest is None:
        return None
    return Halt(halt_id=newest.pk, kind=newest.kind, began_at=newest.began_at)


def _newest_check(now):
    """The newest mailbox check's status, or None."""
    from .jobs.mail_halt_models import MailboxCheck

    # The guard's order exactly: only this newest check counts, so a later
    # refused or unavailable check cancels an earlier pass.
    return check_status(
        MailboxCheck.objects.order_by("-created_at", "-id").first(), now
    )


def _unsettled():
    """How many messages in Sent mail are delivery_unknown, and the
    first ``UNSETTLED_LISTED`` of their ids, oldest first."""
    from .jobs.outbox_models import OutboxMessage

    found = OutboxMessage.objects.filter(state="delivery_unknown")
    return found.count(), tuple(
        found.order_by("created_at", "id").values_list("id", flat=True)[
            :UNSETTLED_LISTED
        ]
    )


def _waiting():
    """How many messages in Sent mail wait to be sent (pending or retry)."""
    from .jobs.outbox_models import OutboxMessage

    return OutboxMessage.objects.filter(state__in=("pending", "retry_wait")).count()


def _paused(store):
    """Whether an Administrator paused Production delivery (clearing a halt
    does not resume it)."""
    from .accounts.configuration_installation import coherent_configuration
    from .campaigns.models import Campaign

    configuration = coherent_configuration(store)
    if configuration.mode != "production" or configuration.current_campaign_id is None:
        return False
    return bool(
        Campaign.objects.filter(pk=configuration.current_campaign_id)
        .values_list("delivery_paused", flat=True)
        .first()
    )


def reason(halt, check, unsettled, now):
    """Why the halt cannot be cleared now (a ``REASONS`` key), or None."""
    if halt is None:
        return "not_halted"
    if check is not None and check.live:
        return "checking"
    if check is None or not check.allows(halt, now):
        return (
            "check_failed"
            if check is not None
            and check.status in {"refused", "unavailable", "unanswered"}
            and check.created_at >= halt.began_at
            else "check_needed"
        )
    if unsettled:
        return "unsettled"
    return None


@dataclass(frozen=True)
class ClearPreview:
    """What Clear the halt's preview states before the Administrator confirms.

    ``token`` is the signed binding the confirmation takes: it names the
    halt and the banner entries the preview saw (``entries``), and is made
    even while ``reason`` says why the halt cannot be cleared yet, so the
    page's Confirm can become usable in place once the mailbox check passes
    (the command line prints it only once it can be used). ``waiting``
    counts the emails waiting to be sent, ``unsettled`` the ones in
    delivery_unknown (``unsettled_ids`` lists the first of them), and
    ``paused`` says whether delivery is also paused.
    """

    halt: Halt
    check: MailboxCheckStatus | None
    waiting: int
    unsettled: int
    unsettled_ids: tuple
    paused: bool
    reason: str | None
    token: str | None
    entries: tuple = ()


@dataclass(frozen=True)
class ClearState:
    """Whether Clear the halt can be used now, for System health's page and
    its polled status: the current halt (None while none is recorded), the
    newest mailbox check, how many emails are unsettled and the reason the
    halt cannot be cleared (None when it can). While no halt is recorded,
    ``phase`` says why: ``clearing`` (a clear was just recorded and the
    senders have not lifted yet), ``unrecorded`` (the halt began a moment
    ago and is not recorded yet) or ``stuck`` (neither, for longer than
    ``LIFT_WINDOW``: the senders need a restart); it is ``recorded`` when
    ``halt`` is set."""

    halt: Halt | None
    check: MailboxCheckStatus | None
    unsettled: int
    reason: str | None
    phase: str = "recorded"


def clear_state(now):
    """The page's ``ClearState``, in the caller's read snapshot. Only read
    while a mail consumer reports Halted, so other reads add no query."""
    halt = current_halt(now)
    if halt is None:
        return ClearState(
            halt=None,
            check=None,
            unsettled=0,
            reason="not_halted",
            phase=_unrecorded_phase(now),
        )
    check = _newest_check(now)
    unsettled = _unsettled()[0]
    return ClearState(
        halt=halt,
        check=check,
        unsettled=unsettled,
        reason=reason(halt, check, unsettled, now),
    )


def _unrecorded_phase(now):
    """Why a Halted sender has no current halt: see ``ClearState.phase``.

    Two extra reads, made only while a sender reports Halted with no halt
    current: the newest clear signal's time, then the newest Halted report's
    ``sender_since`` (when the sender began reporting Halted).
    """
    from django.db.models import Max

    from .jobs.mail_halt_models import MailHaltClear
    from .jobs.service_status_models import ServiceStatus

    cleared = MailHaltClear.objects.aggregate(at=Max("created_at"))["at"]
    if cleared is not None and cleared > now - LIFT_WINDOW:
        return "clearing"
    since = ServiceStatus.objects.filter(
        service="mail-dispatch",
        sender_state="halted",
        reported_at__gt=now - REPORTED,
    ).aggregate(at=Max("sender_since"))["at"]
    if since is not None and since > now - LIFT_WINDOW:
        return "unrecorded"
    return "stuck"


def _halt_entries(halt, now):
    """The halt's critical-problems banner entries up to ``now`` (ids)."""
    from .audit.models import OperationalLog
    from .system_health import banner_events

    return tuple(
        OperationalLog.objects.filter(
            level="CRITICAL",
            event__in=banner_events("sender_halted"),
            created_at__gte=halt.began_at - HALT_ENTRIES_BEFORE,
            created_at__lte=now,
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[:HALT_ENTRIES_LIMIT]
    )


def _admit(caller, store):
    """An enabled Administrator who may view System health and configure,
    with a live session (activity recorded); no recent sign-in (#1547)."""
    from .system_health import _admit_requester

    return _admit_requester(caller, store)


def request_check(caller, store, actor=None):
    """Ask mail dispatch for a mailbox check; return the check's status.

    Refused (``StaleRecordError``) while no halt is current. When a check is
    already live, or one another Administrator asked for at the same moment
    wins the guard's one-at-a-time rule, that check is returned instead. No
    audit event: asking is part of opening the preview.
    """
    from django.db import IntegrityError, transaction

    from .accounts.sessions import database_now
    from .jobs.mail_halt_models import MailboxCheck
    from .storage import StaleRecordError

    actor = actor or _admit(caller, store)
    with transaction.atomic():
        now = database_now()
        if current_halt(now) is None:
            raise StaleRecordError(REASONS["not_halted"])
        newest = _newest_check(now)
        if newest is not None and newest.live:
            return newest
        try:
            with transaction.atomic():
                row = MailboxCheck.objects.create(
                    actor_id=actor.identity, session_id=caller.portal_session.pk
                )
        except IntegrityError:
            # Another check became live since the read above.
            return _newest_check(now)
        row.refresh_from_db()
        return check_status(row, database_now())


def _sign(actor, key, halt_id, entries=()):
    """The preview binding for ``actor``'s clear ``key`` of ``halt_id``, with
    the banner ``entries`` the preview saw."""
    from django.core import signing

    # Compressed: up to 500 entry ids ride in each hidden input and in the
    # command line's --token.
    return signing.dumps(
        {
            "key": str(key),
            "actor": str(actor.identity),
            "halt": str(halt_id),
            "entries": [str(entry) for entry in entries],
        },
        salt=CLEAR_SALT,
        compress=True,
    )


def _unsign(actor, token):
    """The binding's ``(key, halt_id, entries)``, for ``actor`` only.

    An expired binding is ``StaleRecordError`` (review it again); an altered
    one, or another Administrator's, is ``ValueError``.
    """
    from uuid import UUID

    from django.core import signing

    from .storage import StaleRecordError

    try:
        value = signing.loads(token, salt=CLEAR_SALT, max_age=CLEAR_MAX_AGE)
    except signing.SignatureExpired:
        raise StaleRecordError(HALT_CHANGED) from None
    except (signing.BadSignature, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    try:
        key, halt_id = UUID(value["key"]), UUID(value["halt"])
        entries = tuple(UUID(entry) for entry in value.get("entries", ()))
        if value["actor"] != str(actor.identity):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    return key, halt_id, entries


def read_preview(store, actor):
    """The preview as it stands now, without asking for a check.

    Refused (``StaleRecordError``) while no halt is current.
    """
    from uuid import uuid4

    from .accounts.sessions import database_now
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    with read_transaction():
        now = database_now()
        halt = current_halt(now)
        if halt is None:
            raise StaleRecordError(REASONS["not_halted"])
        check = _newest_check(now)
        unsettled, ids = _unsettled()
        waiting = _waiting()
        paused = _paused(store)
        entries = _halt_entries(halt, now)
    return ClearPreview(
        halt=halt,
        check=check,
        waiting=waiting,
        unsettled=unsettled,
        unsettled_ids=ids,
        paused=paused,
        reason=reason(halt, check, unsettled, now),
        token=_sign(actor, uuid4(), halt.halt_id, entries),
        entries=entries,
    )


def preview_clear(caller, store):
    """Clear the halt's preview, asking for a mailbox check when it needs one.

    A new check is asked for unless one is live or a passed one still
    allows a clear (``MailboxCheckStatus.allows``). Refused
    (``StaleRecordError``) while no halt is current.
    """
    from .accounts.sessions import database_now

    actor = _admit(caller, store)
    preview = read_preview(store, actor)
    check = preview.check
    if check is None or not (check.live or check.allows(preview.halt, database_now())):
        request_check(caller, store, actor)
        preview = read_preview(store, actor)
    return actor, preview


def check_again(caller, store):
    """**Check again**: ask for a new mailbox check (unless one is live) and
    return the preview with it. Refused (``StaleRecordError``) while no halt
    is current."""
    actor = _admit(caller, store)
    request_check(caller, store, actor)
    return read_preview(store, actor)


def _acknowledge(store, actor, row, entries):
    """Acknowledge the halt's banner entries the preview saw, for ``actor``,
    inside the clear's transaction, with the clear signal as subject.

    A courtesy to the banner, never a condition of the clear, as Accept this
    change once's (``system_health._acknowledge_refusal``): a configuration
    refusal (a restore under review) or a permission refusal is logged, and
    the clear stands; ``acknowledge_now`` runs in its own savepoint.
    """
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.critical_event_views import acknowledge_now
    from .observability import emit_failure

    if not entries:
        return
    service = SimpleNamespace(store=store)
    try:
        acknowledge_now(service, actor, list(entries), subject_id=row.pk)
    except ConfigError as error:
        emit_failure(error, level=logging.WARNING, name_class=True)
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "42501":
            raise
        emit_failure(error, level=logging.WARNING, name_class=True)


def clear_halt(caller, store, *, token, command=False):
    """Record the clear signal for the reviewed halt; return ``(row, created)``.

    ``token`` is the preview's binding: its key becomes the signal's
    identity, so a repeated confirmation returns the first signal
    (``created`` false). The halt must still be the current one and every
    check must still hold (``reason``); otherwise ``StaleRecordError``
    with the reason in words. The guard repeats each check under its lock.
    Records ``mail_halt_cleared`` with the signal as subject, and from the
    command line (``command``) ``admin_cmd_system_mail_clear`` too, in the
    signal's transaction, which also acknowledges the halt's banner entries
    the preview saw (``_acknowledge``).
    """
    from django.db import DatabaseError, IntegrityError, transaction

    from .accounts.sessions import database_now
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.mail_halt_models import MailHaltClear
    from .observability import emit_failure
    from .storage import StaleRecordError

    actor = _admit(caller, store)
    key, halt_id, entries = _unsign(actor, token)

    def first():
        """The signal already recorded with this key, or None."""
        previous = MailHaltClear.objects.filter(pk=key).first()
        if previous is not None and previous.actor_id != actor.identity:
            raise PermissionError("This halt was cleared by another Administrator.")
        return previous

    def refusal():
        """The current reason the halt cannot be cleared, in words."""
        now = database_now()
        halt = current_halt(now)
        if halt is None or halt.halt_id != halt_id:
            return HALT_CHANGED
        why = reason(halt, _newest_check(now), _unsettled()[0], now)
        return HALT_CHANGED if why is None else REASONS[why]

    try:
        with transaction.atomic():
            previous = first()
            if previous is not None:
                return previous, False
            now = database_now()
            halt = current_halt(now)
            if halt is None or halt.halt_id != halt_id:
                raise StaleRecordError(HALT_CHANGED)
            why = reason(halt, _newest_check(now), _unsettled()[0], now)
            if why is not None:
                raise StaleRecordError(REASONS[why])
            row = MailHaltClear.objects.create(
                id=key,
                halt_id=halt_id,
                actor_id=actor.identity,
                session_id=caller.portal_session.pk,
            )
            record_action(
                Action.MAIL_HALT_CLEARED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor.identity,
                subject_id=row.pk,
                context={"outcome": Outcome.SUCCEEDED},
            )
            if command:
                record_action(
                    Action.ADMIN_CMD_SYSTEM_MAIL_CLEAR,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
            _acknowledge(store, actor, row, entries)
            return row, True
    except IntegrityError as error:
        # A simultaneous second click with the same key lost the race and
        # returns the first signal; otherwise one of the guard's checks
        # (another clear won, the check expired, a message became unknown).
        previous = first()
        if previous is not None:
            return previous, False
        raise StaleRecordError(refusal()) from error
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "42501":
            raise
        # A permission refusal (a missing grant, the wrong login, a session
        # that ended) is not the reader's to fix here: log it and say so.
        emit_failure(error, level=logging.WARNING, name_class=True)
        raise ClearRefused(
            "The server refused this action; the System logs say why."
        ) from error


# --------------------------------------------------------------- commands


def _halt_document(halt):
    """The halt's identity, kind and start, as the documents print them."""
    return {
        "halt_id": halt.halt_id,
        "kind": halt.kind,
        "began_at": plain(halt.began_at),
    }


def _check_document(check):
    """The mailbox check's identity, status and times, or None."""
    if check is None:
        return None
    return {
        "check_id": check.check_id,
        "status": check.status,
        "created_at": plain(check.created_at),
        "finished_at": plain(check.finished_at),
    }


@dataclass(frozen=True)
class MailClearPreview(ReadModel):
    """``system mail-clear-preview``: Clear the halt's preview.

    ``halt`` is the current halt (``halt_id``, ``kind``, ``began_at``);
    ``mailbox_check`` the newest check (``status`` one of waiting, running,
    passed, refused, unavailable or unanswered); ``waiting`` and
    ``unsettled`` count emails, and ``unsettled_ids`` lists the first
    unsettled message ids; ``reason`` says why the halt cannot be cleared
    now (null when it can), and ``preview.token`` is the signed binding
    ``system mail-clear --token`` takes (null with a reason), valid for 15
    minutes and for this Administrator only.
    """

    halt: dict
    mailbox_check: dict | None
    waiting: int
    unsettled: int
    unsettled_ids: list
    delivery_paused: bool
    reason: str | None
    preview: dict


# How often the command reads the mailbox check again while it waits.
POLL_SECONDS = 2


def mail_clear_preview(caller, service, *, wait=True, sleep=None, clock=None):
    """``system mail-clear-preview``: the preview, after the mailbox check.

    Asks for a check as the page's preview does (``preview_clear``) and,
    with ``wait``, reads the preview again every ``POLL_SECONDS`` until the
    check is no longer live (at most ``LIVE_CHECK``, after which it counts
    as unanswered). Refused (``stale_version``) while no halt is current.
    """
    import time

    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    actor, preview = preview_clear(caller, service.store)
    deadline = clock() + LIVE_CHECK.total_seconds() + POLL_SECONDS
    while wait and preview.check is not None and preview.check.live:
        if clock() >= deadline:
            break
        sleep(POLL_SECONDS)
        preview = read_preview(service.store, actor)
    return MailClearPreview(
        halt=_halt_document(preview.halt),
        mailbox_check=_check_document(preview.check),
        waiting=preview.waiting,
        unsettled=preview.unsettled,
        unsettled_ids=list(preview.unsettled_ids),
        delivery_paused=preview.paused,
        reason=preview.reason,
        # Printed only once it can be used; the page holds it from the start.
        preview={"token": preview.token if preview.reason is None else None},
    )


def clear_prompt_lines(service, token, caller):
    """What ``system mail-clear`` asks the Administrator to confirm. Checks
    the token first, so a bad one is refused before the prompt."""
    from .accounts.sessions import authenticated_admin

    actor = authenticated_admin(caller, store=service.store, read_only=True)
    _, halt_id, _ = _unsign(actor, token)
    return (
        f"Clear the mail sender's halt {halt_id}.",
        "Both mail senders start again with one email as a probe. If the fault "
        "is still there, the probe meets it and sending halts again.",
        "Nothing is resent: failed emails stay failed until they are retried on "
        "Sent mail.",
    )


@dataclass(frozen=True)
class MailClear(ReadModel):
    """``system mail-clear``'s result: the clear signal it recorded (or the
    one an earlier run with the same token recorded).

    ``created`` is false for a repeat. The mail senders act on the signal
    within about 30 seconds; ``system health`` shows them running again.
    """

    clear_id: object
    halt_id: object
    created: bool
    created_at: str


def mail_clear(caller, service, *, token, context):
    """``system mail-clear``: Clear the halt, as the page's confirmation
    (``clear_halt``), with ``admin_cmd_system_mail_clear``.

    ``context["committed"]`` is set once the signal may have committed
    (exit 6 then): as soon as ``clear_halt`` returns, or on a database
    failure that may have been the commit itself.
    """
    from django.db import DatabaseError

    from .observability import _guard_refusal

    try:
        row, created = clear_halt(caller, service.store, token=token, command=True)
    except DatabaseError as error:
        if not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return MailClear(
        clear_id=row.pk,
        halt_id=row.halt_id,
        created=created,
        created_at=plain(row.created_at),
    )
