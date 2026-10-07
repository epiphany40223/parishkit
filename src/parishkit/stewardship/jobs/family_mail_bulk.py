"""Bulk Family send (#430): prepare and send many Family messages per lock turn.

This is an optional, faster path for the large scheduled Family sends
(invitations and reminders). It is off by default and turned on by the
deployment setting ``bulk_family_send`` on the scheduler, worker and
mail-dispatch services. Off, the consumers run exactly the one-task-per-hint
path.

Why: during a send every step of every message took its own turn on the
deployment-wide work-order lock (736220,1): a claim, an effect, a submission,
an outcome and a completion, each in its own transaction on a fresh database
connection. The lock, not SMTP, set the send rate. The work done under the
lock is mostly the SQL guard checks on the rows written; rendering is a few
milliseconds. So this path keeps every write, guard and admission check the
one-at-a-time path makes, and only changes how they are grouped:

- Preparation: one lock transaction claims, prepares and completes several
  ``family_mail_prepare`` tasks, each in its own savepoint.
- Sending: one lock transaction claims several ``outbox_delivery`` tasks and
  commits each message's "submitting" state (the durable record, made before
  anything reaches Gmail). The consumer then sends them one after another over
  its one SMTP session, outside any transaction, and records every outcome and
  completes every task before it begins the next batch.

The rows written are the rows the one-at-a-time path writes, with the same
owners, so either path can finish or recover what the other started, and
turning the switch off mid-send is safe.

A batch ends at whichever comes first: its item limit or its lock-hold
budget. The budget bounds how long one transaction holds the lock (Family
form requests, Admin pages, the scheduler and the other consumer wait for
it). Anything a batch cannot finish (an item a guard or admission check
refuses, a held or paused message, a changed selection, any error) is rolled
back to its savepoint, untouched, and is later handled by the one-at-a-time
path when the scheduler hints it.
"""

import json
import logging
from dataclasses import dataclass, replace
from random import randint
from time import monotonic, sleep
from uuid import uuid4

from django.db import connection, connections, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
    elapsed_ms,
)

from .admission import remembered_scopes
from .dispatch import _CLAIMABLE
from .models import TaskRun
from .ownership import TaskClaim, database_now, lock_task_claim
from .phases import TaskPhase
from .storage import _locked, _status, change_run

LOG = logging.getLogger(__name__)
DEBUG = logging.getLogger("parishkit.stewardship.debug")

# Most preparation tasks one lock transaction prepares.
PREPARE_BATCH = 100
# How many more items than its last batch committed a preparation drain
# builds outside the lock before its next batch (BG-12). A batch's hold
# fits only a few items, so building far ahead would mostly build items the
# batch never reaches; those carry over to the next batch and are rechecked
# there, but cost time while a Family's state may still change.
BUILD_MARGIN = 2
# Default and bounds for B, the most messages one send batch marks
# "submitting" before sending them. A crash between that commit and the
# outcome commit leaves up to B messages delivery_unknown for an
# Administrator to settle, so the default is small. The lock-hold budget
# below usually ends a batch first (a few messages locally, one or two on
# the validation host), so B is a ceiling on that exposure, not a target.
SEND_BATCH = 20
SEND_BATCH_RANGE = range(1, 101)
# Most seconds one batch transaction holds the work-order lock. Before each
# item after the first, the batch stops if the time held so far plus the
# average item so far would reach this (see _Pace), so a hold overruns the
# budget only by how much one item is slower than the average. The items are
# the same guarded writes as before; this only bounds how long others wait
# for the lock behind one batch. It must stay well under the shortest wait
# any other lock taker allows itself: a running task's lease renewal gives
# up after 2 s (jobs/lifetime.renew_once, which stops that task), so even a
# waiter queued behind two full batches (one per consumer process) waits
# about 1.5 s; an in-flight mail check gives up after 1 s (skipped and
# logged, never stopping its task), and a Family login waits for the
# runtime row a preparation locks. One Family item takes about 0.1-0.25 s
# locally and roughly 3-4 times that on the validation host, where a batch
# is then one or two items (still one transaction instead of three to five).
HOLD_SECONDS = 0.75
# How long one hint's consumer keeps taking further batches (the drain)
# before it returns to its queue. Checked before each batch; the process
# heartbeat is beaten between batches and between sends, so the container's
# liveness probe (90 s) never sees a drain as silence. Kept short so other
# work in the same queue is not kept waiting behind a long send.
DRAIN_SECONDS = 30
# How long a drain waits, polling without the lock, for new due work once it
# finds none (the preparer is usually seconds ahead of the senders), before
# it returns to its queue. Without it a consumer idles until the scheduler's
# next loop hints it again, often tens of seconds during a send.
IDLE_SECONDS = 5
IDLE_POLL_SECONDS = 0.5
# Consecutive item refusals after which a batch stops early: a campaign-wide
# hold refuses every item, and each refusal costs lock time.
REFUSALS_TO_STOP = 3
# Lease on each claimed send task. It must outlast the whole batch: the
# "submitting" commit, every send, and the outcome commit.
SEND_LEASE_SECONDS = 240
# A helper still sending this close to the lease's end is stopped (its
# message is then unknown, never resent), so the outcome commit that follows
# still holds a live claim.
LEASE_MARGIN_SECONDS = 30
# No new message is launched later than this after the batch's commit; any
# message still unsent then is recorded as definitely unsent (retried later).
SEND_CUTOFF_SECONDS = 150
# A message is launched only with at least this much of its provider
# deadline left; the helper then gets min(30, what is left), like the
# one-at-a-time path's PROVIDER_SECONDS. With less, a slow Gmail could stop
# the helper after DATA began, an unknown outcome that is never retried, so
# the message is released as definitely unsent instead.
MIN_LAUNCH_SECONDS = 25
# Provider deadline of the first message in a send batch (the one-at-a-time
# path's PROVIDER_SECONDS) and the allowance added for each message ahead of
# it: one whole helper budget each, so even if every earlier send used its
# full 30 s, this one still has its full budget at launch. Capped at the
# launch cutoff plus one helper budget; positions past the cap launch only
# if earlier sends were quick, and are released unsent otherwise.
FIRST_DEADLINE_SECONDS = 30
DEADLINE_STEP_SECONDS = 30
MAX_DEADLINE_SECONDS = SEND_CUTOFF_SECONDS + 30
# How soon a message held before launch (pause, scope, deadline) is retried.
HOLD_RETRY_SECONDS = 30
# Scheduled Family mail only; receipts, digests and tests keep their path.
BULK_PURPOSES = ("initial", "reminder")


class _Skip(Exception):
    """An item the batch leaves, untouched, for the one-at-a-time path."""


@dataclass(frozen=True)
class BulkSettings:
    """Whether the bulk path is on, and its send batch size B."""

    enabled: bool = False
    send_batch: int = SEND_BATCH

    def __post_init__(self):
        """Reject a malformed switch or batch size before any task runs."""
        if type(self.enabled) is not bool or (
            type(self.send_batch) is not int or self.send_batch not in SEND_BATCH_RANGE
        ):
            raise ValueError("Bulk Family send settings are invalid.")


def _candidates(task_type, limit, exclude=(), purposes=None, first=None):
    """Due, claimable task ids of one type, oldest first, read without a lock.

    Like the hint pre-check in dispatch, this only chooses what to try: every
    item is claimed under the locks with the full admission check, and one
    found already claimed (by the other consumer, say) is skipped. ``first``
    (the hinted task) is tried before the others.
    """
    rows = TaskRun.objects.filter(_CLAIMABLE, task_type=task_type).exclude(
        pk__in=list(exclude)
    )
    if purposes is not None:
        from .outbox_models import OutboxMessage

        rows = rows.filter(
            domain_request_id__in=OutboxMessage.objects.filter(
                purpose__in=purposes, state__in=("pending", "retry_wait")
            ).values("pk")
        )
    ids = list(rows.order_by("not_before", "id").values_list("pk", flat=True)[:limit])
    if first is not None and first not in exclude:
        ids = [first] + [value for value in ids if value != first]
    return ids


def _claim(run_id, handler, worker_id, lease_seconds):
    """Claim one task as claim_hint does, inside the caller's transaction."""
    original = TaskRun.objects.filter(pk=run_id).first()
    if original is None:
        raise _Skip
    with _locked(original.correlation_id, root_id=original.root_id):
        row = TaskRun.objects.select_for_update().get(pk=run_id)
        if row.state not in {"queued", "retry_wait"} or row.not_before > database_now():
            raise _Skip
        status = change_run(
            run_id=row.pk,
            action="claim",
            expected_version=row.version,
            actor_id=worker_id,
            correlation_id=row.correlation_id,
            admit=handler.admit,
            lease_seconds=lease_seconds,
        )
    claim = TaskClaim(status.run_id, status.fence, worker_id)
    # The effect admission Execution.effect() makes before any domain write.
    if handler.admit("effect", _status(lock_task_claim(claim))) is not True:
        raise PermissionError("This task effect is not admitted.")
    return claim, row.correlation_id


def _transition(claim, correlation_id, handler, action, **options):
    """Apply one execution transition as Execution.transition does, in place."""
    row = lock_task_claim(claim)
    status = change_run(
        run_id=row.pk,
        expected_version=row.version,
        action=action,
        actor_id=claim.worker_id,
        correlation_id=correlation_id,
        fence=claim.fence,
        admit=handler.admit,
        **options,
    )
    if handler.after_transition is not None:
        handler.after_transition(action, status)
    return status


class _Pace:
    """The hold-budget rule for one batch transaction (review M3).

    ``fits()`` before each item after the first: whether the hold so far
    plus the average item so far stays under the budget. The average, not
    the slowest, item is the estimate: the first item of a transaction also
    pays one-time costs (the lock, cold reads), and judging every later item
    by it would end nearly every batch after one item.
    """

    def __init__(self, seconds=None):
        """Start timing a transaction that has just taken the lock."""
        self.seconds = HOLD_SECONDS if seconds is None else seconds
        self.started, self.count, self.spent = monotonic(), 0, 0.0
        # Each item's seconds, for the rehearsal's timing line (BG-12).
        self.durations = []

    def fits(self):
        """Whether another average item would still fit in the budget."""
        if not self.count:
            return True
        average = self.spent / self.count
        return monotonic() - self.started + average < self.seconds

    def ran(self, begun):
        """Count one item that started at ``begun`` (monotonic)."""
        took = monotonic() - begun
        self.count += 1
        self.spent += took
        self.durations.append(took)

    def held(self):
        """Seconds held so far."""
        return monotonic() - self.started


def _run_batch(ids, item, *, limit, seconds=None):
    """Run ``item(run_id, position)`` for each id in one lock transaction.

    Each item runs in its own savepoint; ``position`` is how many items
    have succeeded before it. A success counts (and its return value is
    kept) only once its savepoint has been released. Stops after ``limit``
    successes, after REFUSALS_TO_STOP refusals in a row, or before an item
    that would likely take the hold past ``seconds`` (see _Pace). Returns
    ``(done, tried, pace)``: what ``item`` returned for each success, every
    id tried, and the batch's _Pace (how long the lock was held, and each
    item's time).
    """
    done, tried, refusals = [], [], 0
    with work_transaction():
        pace = _Pace(seconds)
        for run_id in ids:
            if len(done) >= limit or refusals >= REFUSALS_TO_STOP:
                break
            if not pace.fits():
                break
            tried.append(run_id)
            begun = monotonic()
            try:
                with transaction.atomic(), remembered_scopes():
                    value = item(run_id, len(done))
            except _Skip:
                pass
            except Exception as error:
                # Rolled back to the savepoint: the task is untouched and the
                # one-at-a-time path handles it when it is next hinted.
                refusals += 1
                DEBUG.debug("bulk item %s left for the single path: %r", run_id, error)
            else:
                done.append(value)
                refusals = 0
            pace.ran(begun)
    return done, tried, pace


def _timing(kind, pace, *, items, tried, work=(), built=(), prebuilt=0, rebuilt=0):
    """Debug-log one lock transaction's timings for the local rehearsal (BG-12).

    One line per batch transaction, ``bulk timing: {...}``, with a JSON
    object that the rehearsal report (``local/rehearsal_report.py``) parses:

    - ``kind``: ``prepare``, ``commit`` (a send batch's "submitting"
      commit) or ``outcome`` (one outcome chunk);
    - ``items`` and ``tried``: the items finished and tried (an outcome
      chunk counts every outcome it tried to record, including one that
      could not be recorded and is left to recovery);
    - ``hold_ms``: the lock hold, from the batch's first item through the
      commit, since this is called just after it (a send batch's
      configuration reads before its first item are left out);
    - ``item_ms``: each item's time under the lock;
    - ``work_ms``: the part of each item that renders, decrypts or seals,
      the work BG-12 moves outside the lock;
    - ``build_ms``: each build made outside the lock before this batch
      (BG-12), whether used or dropped, so the rehearsal can see what the
      lock no longer holds;
    - ``prebuilt`` and ``rebuilt``: items written from a build made outside
      the lock, and items whose build was stale (its fingerprint changed)
      and was redone under the lock (BG-12). An item with no build, such
      as a Testing item or one whose build was dropped, is neither.

    Observation only: nothing is built unless DEBUG is enabled, any failure
    here is swallowed, so it can never change what a batch does or delay a
    send, and the line holds no identifiers, addresses or content.
    """
    try:
        if not DEBUG.isEnabledFor(logging.DEBUG):
            return
        DEBUG.debug(
            "bulk timing: %s",
            json.dumps(
                {
                    "kind": kind,
                    "items": items,
                    "tried": tried,
                    "hold_ms": round(pace.held() * 1000),
                    "item_ms": [round(value * 1000) for value in pace.durations],
                    "work_ms": [round(value * 1000) for value in work],
                    "build_ms": [round(value * 1000) for value in built],
                    "prebuilt": prebuilt,
                    "rebuilt": rebuilt,
                },
                sort_keys=True,
            ),
        )
    except Exception:  # noqa: S110 - observation must never affect delivery
        pass


def _beat(pulse):
    """Publish this process's liveness heartbeat, if it has one."""
    if pulse is not None:
        pulse()


def _drain(step, stop, pulse):
    """Repeat ``step`` (one batch) while it finds work, for DRAIN_SECONDS.

    ``step(exclude)`` returns how many items it finished and the ids it
    tried, which later batches of this drain skip. A step that finishes
    nothing (no due work, or its items were just taken by the other
    consumer) starts an idle wait: the drain polls again every
    IDLE_POLL_SECONDS and ends once IDLE_SECONDS pass with nothing finished,
    or at DRAIN_SECONDS. The heartbeat is beaten after every batch and poll,
    as the lease renewal thread does during one task.
    """
    started, tried, total, idle = monotonic(), set(), 0, None
    while monotonic() - started < DRAIN_SECONDS:
        if stop is not None and stop.is_set():
            break
        finished, attempted = step(tried)
        _beat(pulse)
        tried.update(attempted)
        total += finished
        if finished:
            idle = None
            continue
        idle = monotonic() if idle is None else idle
        if monotonic() - idle >= IDLE_SECONDS:
            break
        if stop is not None:
            stop.wait(IDLE_POLL_SECONDS)
        else:
            sleep(IDLE_POLL_SECONDS)
    return total


# --- Preparation -------------------------------------------------------------


def preparation_bulk(handler, *, general, mac, public, public_origin, settings):
    """Build the general worker's bulk preparation entry point, or None.

    The returned callable takes a hint's run id: it prepares batches of due
    preparation tasks (the hinted one first), and returns. The caller then
    runs the ordinary hint path, which finds the hinted task done or handles
    it singly. The caller passes its registered handler (``owner``), whose
    admission includes the runtime's authority checks; ``handler`` is only
    the fallback for direct callers.
    """
    if not settings.enabled:
        return None
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .family_mail_builds import build_preparation
    from .family_mail_preparation import prepare_occurrence
    from .family_mail_tasks import TASK_TYPE, disposition, owned_preparation

    def run(run_id, *, stop=None, owner=None, pulse=None):
        """Drain due preparation in batches, then return to the hint path.

        Before each batch the drain builds its next few Production items
        outside the lock (``build_ahead``); the batch then writes each
        prebuilt item whose fingerprint still matches, and prepares a stale
        or dropped one under the lock as before. Builds a batch does not reach
        carry over to the next one and are rechecked there; all are dropped
        when the drain ends.
        """
        if connection.in_atomic_block:
            raise RuntimeError("Bulk preparation must own its transactions.")
        current = owner or handler
        first = [run_id]
        # Each item's preparation time in the current batch (_timing).
        work = []
        # Builds made outside the lock and not yet used, by task id, and
        # every task this drain has tried to build (built or dropped), so a
        # dropped build is not retried within the drain.
        builds, attempted = {}, set()
        # Items the last batch committed: the next lookahead's base.
        committed = [0]
        # Each build's time outside the lock before the current batch.
        built = []

        def build_ahead(ids):
            """Build up to the last batch's count plus BUILD_MARGIN items.

            In ``ids`` order, which is the order the batch tries them.
            Serial, on this process's own connection, outside any
            transaction. Returns how many of ``ids`` the next batch may
            try: those built (now or carried over), those whose build was
            dropped (prepared under the lock, as before), and at least the
            first. The batch stops there rather than preparing items it has
            not tried to build under the lock; the next lookahead, sized by
            what this batch commits, reaches them. In Testing nothing is
            built (its preparation writes credentials, #555), so the batch
            may try every item, as before.
            """
            # A carried build whose item is no longer a candidate (taken by
            # another path, say) is dropped, so it cannot count toward the
            # lookahead and shrink the batch.
            for task_id in builds.keys() - set(ids):
                builds.pop(task_id)
            if not SystemConfiguration.objects.filter(mode="production").exists():
                return len(ids)
            want = committed[0] + BUILD_MARGIN
            for count, task_id in enumerate(ids):
                if task_id in builds or task_id in attempted:
                    continue
                if len(builds) >= want or (stop is not None and stop.is_set()):
                    return max(count, 1)
                attempted.add(task_id)
                begun = monotonic()
                build = build_preparation(
                    task_id, general=general, public=public, public_origin=public_origin
                )
                built.append(monotonic() - begun)
                if build is not None:
                    builds[task_id] = build
            return len(ids)

        def item(task_id, position):
            """Claim, prepare and complete one preparation task.

            Returns the task id and whether its build was written (True),
            redone under the lock (False) or absent (None).
            """
            prebuilt = builds.pop(task_id, None)
            used = []
            claim, correlation_id = _claim(task_id, current, uuid4(), 60)
            task = lock_task_claim(claim)
            ticket = owned_preparation(_status(task))
            terminal = disposition(ticket)
            if terminal is None:
                begun = monotonic()
                try:
                    terminal = prepare_occurrence(
                        ticket,
                        claim,
                        general=general,
                        mac=mac,
                        public=public,
                        public_origin=public_origin,
                        prebuilt=prebuilt,
                        report=used.append,
                    )
                finally:
                    work.append(monotonic() - begun)
            _transition(claim, correlation_id, current, terminal)
            return task_id, (used[0] if used else None)

        def step(exclude):
            """Build ahead, then prepare one batch; see _run_batch."""
            ids = _candidates(
                TASK_TYPE,
                PREPARE_BATCH * 2,
                exclude,
                first=first.pop() if first else None,
            )
            if not ids:
                return 0, ()
            built.clear()
            ids = ids[: build_ahead(ids)]
            work.clear()
            done, tried, pace = _run_batch(ids, item, limit=PREPARE_BATCH)
            for task_id in tried:
                # Used, refused or rolled back: a build is never reused for
                # an item a batch has tried (a refusal leaves the item to
                # the one-at-a-time path).
                builds.pop(task_id, None)
            committed[0] = len(done)
            _timing(
                "prepare",
                pace,
                items=len(done),
                tried=len(tried),
                work=work,
                built=built,
                prebuilt=sum(used is True for _, used in done),
                rebuilt=sum(used is False for _, used in done),
            )
            return len(done), tried

        try:
            return _drain(step, stop, pulse)
        finally:
            # Builds hold no plaintext, but none outlives its drain.
            builds.clear()
            connections.close_all()

    return run


# --- Sending -----------------------------------------------------------------


@dataclass
class _Sending:
    """One message of a send batch, from its "submitting" commit to its outcome.

    ``held`` names why the message was not launched (it is then recorded as
    definitely unsent, without spending its attempt budget), and
    ``retry_seconds`` how soon it may be tried again.
    """

    claim: TaskClaim
    correlation_id: object
    message_id: object
    mail: object
    deadline: object
    attempt: int
    recipients: int
    result: FamilyDeliveryResult | None = None
    stats: dict | None = None
    held: str | None = None
    retry_seconds: int = HOLD_RETRY_SECONDS


def delivery_bulk(
    handler, *, private, public_origin, credential_path, circuit, session, settings
):
    """Build a mail consumer's bulk send entry point, or None.

    The returned callable takes a hint's run id. Only a hint for scheduled
    Family mail (an initial invitation or reminder) starts a drain, with the
    hinted message first; any other hint (a receipt, digest, test or alert)
    returns at once, so it never waits behind a send. The caller then runs
    the ordinary hint path, which handles anything left. The caller passes
    its registered handler (``owner``), as for preparation.
    """
    if not settings.enabled or session is None:
        return None
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
    )
    from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.sender_name import configured_sender_name

    from . import family_mail_delivery_tasks as single
    from .family_mail_delivery_tasks import (
        LIMIT_RETRY_SECONDS,
        MAILBOX_LIMITS,
        OUTAGE_RECOVERY_SECONDS,
        _with_stats,
    )
    from .family_mail_dispatch import (
        DAILY_SEND_LIMIT,
        RESERVED_SENDS,
        TASK_TYPE,
        begin_submission,
        bound_dispatch,
        disposition,
        finish_submission,
        result_retry_seconds,
    )
    from .outbox_models import OutboxMessage

    batch = settings.send_batch

    def begin_item(run_id, position, context, current):
        """Claim one message's task and commit its "submitting" state."""
        claim, correlation_id = _claim(run_id, current, uuid4(), SEND_LEASE_SECONDS)
        _transition(
            claim,
            correlation_id,
            current,
            "progress",
            progress=(0, 0),
            phase=TaskPhase.PREPARING,
        )
        message = bound_dispatch(_status(lock_task_claim(claim)))
        if (
            message.purpose not in BULK_PURPOSES
            or message.state not in {"pending", "retry_wait"}
            or disposition(message, check_recipient=True) is not None
        ):
            # Paused, cancelled, superseded or not bulk mail: the single
            # path records the hold or cancellation as it always has.
            raise _Skip
        recipients = len(message.render.routed_recipients)
        if context["sent"] + context["recipients"] + recipients > context["allowed"]:
            # Near the daily limit the single path decides message by message.
            raise _Skip
        begun = monotonic()
        try:
            prepared = begin_submission(
                message.pk,
                claim,
                private=private,
                public_origin=public_origin,
                configuration_id=context["configuration_id"],
                nested=True,
                provider_seconds=min(
                    MAX_DEADLINE_SECONDS,
                    FIRST_DEADLINE_SECONDS + DEADLINE_STEP_SECONDS * position,
                ),
            )
        finally:
            context["work"].append(monotonic() - begun)
        if prepared is None:
            # Selection changed or held; nothing of it is kept (savepoint).
            raise _Skip
        mail, deadline, _, attempt = prepared
        # Returned, not recorded: the caller keeps it only once this item's
        # savepoint has been released.
        return _Sending(
            claim, correlation_id, message.pk, mail, deadline, attempt, recipients
        )

    def begin(exclude, candidate, current, first):
        """One lock transaction: up to B messages committed as "submitting".

        The transaction also stops at the lock-hold budget, so a batch is
        whichever is smaller. Its messages are then sent at once, before
        any further batch is begun: committing more batches first would
        hold no fewer lock turns (each is budget-bound anyway), but would
        widen what a crash leaves unknown and let early messages' provider
        deadlines run out before their turn to be sent.
        """
        ids = _candidates(
            TASK_TYPE, batch * 2, exclude, purposes=BULK_PURPOSES, first=first
        )
        if not ids:
            return None, ()
        context = {
            "sent": circuit.daily_sends(),
            "recipients": 0,
            # Leave one largest message (100 recipients, the SQL cap) of
            # room, so the batch never decides at the limit itself.
            "allowed": DAILY_SEND_LIMIT - RESERVED_SENDS - 100,
            # Each item's begin_submission time (_timing).
            "work": [],
        }

        def item(run_id, position):
            """Begin one message, counting its recipients only on success."""
            sending = begin_item(run_id, position, context, current)
            context["recipients"] += sending.recipients
            return sending

        with work_transaction():
            configuration_id = SystemConfiguration.objects.get().active_configuration_id
            workspace = AppliedIntegration.objects.get(
                configuration_id=configuration_id, kind="google_workspace"
            )
            if file_fingerprint(candidate) != workspace.credential_fingerprint:
                # Mid key switch (or a mismatch): leave every message to the
                # single path, which holds or fails it as it always has.
                return None, ()
            context.update(
                configuration_id=configuration_id,
                sender_name=configured_sender_name(configuration_id),
                workspace=workspace,
            )
            items, tried, pace = _run_batch(ids, item, limit=batch)
            # The clock the provider deadlines were written on, read just
            # before commit, paired with this process's monotonic clock.
            context["db_now"], context["mono"] = database_now(), monotonic()
        context["items"] = items
        _timing(
            "commit", pace, items=len(items), tried=len(tried), work=context["work"]
        )
        return context, tried

    def send_all(context, candidate, pulse):
        """Send each committed message in turn, outside any transaction."""
        workspace = context["workspace"]
        base = workspace.settings
        claimed = context["mono"]
        cut, short = [], []
        for item in context["items"]:
            _beat(pulse)
            started = monotonic()
            remaining = (item.deadline - context["db_now"]).total_seconds() - (
                started - context["mono"]
            )
            # Until launched, a message is definitely unsent: a hold, never a
            # failed attempt (see finish_item). "stopped" stays only if this
            # loop ends before deciding (an error in the checks below).
            item.result, item.held = _unsent(item), "stopped"
            item.stats = {"transport": "batched"}
            if started - claimed >= SEND_CUTOFF_SECONDS:
                item.held = "cutoff"
                cut.append(item)
                continue
            if remaining < MIN_LAUNCH_SECONDS:
                item.held = "deadline"
                short.append(started - claimed)
                continue
            wait = circuit.limit_remaining()
            if circuit.blocks_new_send() or wait:
                item.held = "circuit"
                # Like the single path's limit and outage holds (_defer_held):
                # spread out, so held messages do not all probe together.
                item.retry_seconds = max(60, int(wait)) + randint(0, 300)
                continue
            reason = _held_now(item.message_id)
            if reason is not None:
                item.held = reason
                continue
            item.held = None
            settings_ = base | {
                "sender": item.mail.sender,
                "reply_to": item.mail.reply_to,
                "sender_name": context["sender_name"],
            }
            # From here on the message may reach Gmail: until the helper
            # answers, its outcome is unknown, never "unsent".
            item.result = FamilyDeliveryResult(
                FamilyDeliveryStatus.UNKNOWN,
                len(item.mail.recipients),
                health=ProviderHealth.UNAVAILABLE,
            )
            calling = monotonic()
            try:
                # The single path's module attribute: one transport (and any
                # test double of it) serves both paths.
                item.result = single.submit_family(
                    candidate,
                    settings_,
                    item.mail,
                    session=session,
                    seconds=min(30, remaining),
                    check=_lease_check(claimed, item.claim.run_id),
                )
            except Exception:
                item.result = FamilyDeliveryResult(
                    FamilyDeliveryStatus.UNKNOWN,
                    len(item.mail.recipients),
                    health=ProviderHealth.UNAVAILABLE,
                )
            finally:
                item.stats.update(submit_ms=elapsed_ms(calling), **session.take_stats())
            _observe(item.result)
        held = [item for item in context["items"] if item.held]
        if held:
            DEBUG.debug(
                "bulk send: %d messages released unsent: %s",
                len(held),
                ", ".join(sorted({item.held for item in held})),
            )
        if cut:
            _record_timeout(
                "lease",
                cut[0].claim.run_id,
                SEND_CUTOFF_SECONDS,
                monotonic() - claimed,
                len(cut),
            )
        if short:
            # Messages whose own provider deadline (set by batch position)
            # left less than MIN_LAUNCH_SECONDS for the helper when their
            # turn came; elapsed is the longest wait since the batch commit.
            _record_timeout(
                "mail_helper",
                context["items"][0].claim.run_id,
                MIN_LAUNCH_SECONDS,
                max(short),
                len(short),
                helper="family_delivery_worker",
            )

    def _observe(result):
        """React to one outcome as the single path does (circuit and limits)."""
        if circuit.observe(result.health):
            if circuit.stopped:
                LOG.critical(
                    "Family mail provider refused this configuration; further "
                    "sending is stopped until the mail worker restarts."
                )
            else:
                LOG.log(
                    logging.WARNING if circuit.repeated else logging.CRITICAL,
                    "Family mail provider is unavailable; sending pauses for %d "
                    "minutes, then resumes automatically.",
                    OUTAGE_RECOVERY_SECONDS // 60,
                )
        if result.limit in MAILBOX_LIMITS and circuit.hold(
            LIMIT_RETRY_SECONDS[result.limit]
        ):
            LOG.critical(
                "Google Workspace refused mail at its %s sending limit; sending "
                "pauses and resumes automatically.",
                result.limit,
            )

    def finish_item(item, current):
        """Record one outcome and settle its task, as the single path does.

        A message held before launch is recorded as definitely unsent with
        ``hold=True``: it does not spend the attempt budget, and its task
        waits in the RECONCILING phase, which the single path's attempt
        count leaves out, exactly like its admission holds. Unsent mail
        whose mode, epoch or revision has changed is still recorded as a
        definitive failure, as finish_submission always has.
        """
        result = _with_stats(item.result, item.stats or {})
        held = item.held is not None
        status = finish_submission(item.message_id, item.claim, result, hold=held)
        if status.state.value == "retry_wait":
            if (
                held
                or result.limit is not None
                or (result.health is ProviderHealth.UNAVAILABLE)
            ):
                _transition(
                    item.claim,
                    item.correlation_id,
                    current,
                    "progress",
                    progress=(0, 0),
                    phase=TaskPhase.RECONCILING,
                )
            _transition(
                item.claim,
                item.correlation_id,
                current,
                "retryable_failure",
                retry_seconds=item.retry_seconds
                if held
                else result_retry_seconds(result, item.attempt),
            )
        else:
            _transition(
                item.claim,
                item.correlation_id,
                current,
                "complete"
                if status.state.value == "delivered"
                else "permanent_failure",
            )

    def finish(items, current):
        """Record every outcome, in lock-budget chunks, one savepoint each.

        An outcome that cannot be recorded leaves its message "submitting"
        under a live claim; once the lease and provider deadline pass,
        recovery records it delivery_unknown, as after a crash. If a chunk's
        transaction itself fails, each of its outcomes is tried in its own.
        """
        left = list(items)
        while left:
            chunk = []
            try:
                with work_transaction():
                    pace = _Pace()
                    while left and pace.fits():
                        item = left.pop(0)
                        chunk.append(item)
                        begun = monotonic()
                        try:
                            with transaction.atomic():
                                finish_item(item, current)
                        except Exception as error:
                            _unrecorded(error)
                        pace.ran(begun)
            except Exception:
                for item in chunk:
                    try:
                        with work_transaction():
                            finish_item(item, current)
                    except Exception as error:
                        _unrecorded(error)
            else:
                _timing("outcome", pace, items=len(chunk), tried=len(chunk))

    def hinted_bulk(run_id):
        """Whether a hint is for scheduled Family mail (the only drain trigger)."""
        message = (
            TaskRun.objects.filter(pk=run_id, task_type=TASK_TYPE)
            .values_list("domain_request_id", flat=True)
            .first()
        )
        return (
            message is not None
            and OutboxMessage.objects.filter(
                pk=message, purpose__in=BULK_PURPOSES
            ).exists()
        )

    def run(run_id, *, stop=None, owner=None, pulse=None):
        """Drain due Family mail in batches, then return to the hint path."""
        if connection.in_atomic_block:
            raise RuntimeError("Bulk sending must own its transactions.")
        current = owner or handler
        first = [run_id]

        def step(exclude):
            """One send batch: submitting commit, sends, outcome commit."""
            if circuit.blocks_new_send() or circuit.limit_remaining():
                return 0, ()
            candidate = read_private(credential_path)
            # Settle retired helpers before anything commits "submitting":
            # failing to reap one is fatal, and must not strand sent mail.
            session.reap()
            context, tried = begin(
                exclude, candidate, current, first.pop() if first else None
            )
            if context is None:
                return 0, ()
            items = context["items"]
            if not items:
                return 0, tried
            try:
                send_all(context, candidate, pulse)
            finally:
                # Whatever happened (even a fatal helper drain failure),
                # record what is known: unlaunched messages are unsent.
                for item in items:
                    if item.result is None:
                        item.result, item.held = _unsent(item), "stopped"
                finish(items, current)
            return len(items), tried

        try:
            if not hinted_bulk(run_id):
                return 0
            return _drain(step, stop, pulse)
        finally:
            connections.close_all()

    return run


def _unsent(item):
    """A definitely unsent result for a message that was never launched."""
    return FamilyDeliveryResult(
        FamilyDeliveryStatus.TRANSIENT,
        len(item.mail.recipients),
        health=ProviderHealth.UNOBSERVED,
    )


def _unrecorded(error):
    """Log an outcome the bulk send could not record (recovery settles it)."""
    LOG.error(
        "A Family mail outcome could not be recorded; recovery will mark it "
        "delivery unknown.",
        exc_info=error,
    )


def _lease_check(claimed, task_id):
    """The helper's in-flight check for a batch message.

    The one-at-a-time path re-verifies its claim in SQL about once a second
    while the helper runs. A batch's claims are held with a lease that
    outlasts the whole batch (SEND_LEASE_SECONDS against the
    SEND_CUTOFF_SECONDS launch cutoff plus the helper's 30 s bound), so
    ownership cannot expire mid-send; this check only enforces that bound.
    """

    def check():
        """Stop the helper if the batch has run past its lease margin."""
        elapsed = monotonic() - claimed
        limit = SEND_LEASE_SECONDS - LEASE_MARGIN_SECONDS
        if elapsed >= limit:
            _record_timeout("lease", task_id, limit, elapsed, None)
            raise TimeoutError("Bulk send batch outlived its lease margin.")

    return check


# One statement for _held_now: the checks family_mail_dispatch.disposition
# and family_schedule_planning._planning_scope make under the lock, in the
# same order, read here without it. One round trip per message keeps this
# cheap beside the send (ten ORM queries cost noticeable CPU per message).
HELD_NOW_SQL = """
SELECT CASE
  WHEN m.id IS NULL THEN 'missing'
  WHEN r.id IS NULL OR c.id IS NULL OR o.id IS NULL OR k.campaign_id IS NULL
    OR r.current_campaign_id IS DISTINCT FROM m.campaign_id OR r.mode <> m.mode
    OR r.restore_review_required THEN 'scope'
  WHEN o.revision_id IS DISTINCT FROM d.current_revision_id
    OR o.state IN ('skipped','coalesced')
    OR (m.mode='production' AND o.production_cycle <> c.production_cycle)
    OR (m.mode='testing' AND (c.state <> 'draft'
        OR k.rehearsal_epoch_id IS DISTINCT FROM m.rehearsal_epoch_id)) THEN 'scope'
  WHEN c.state IN ('closed','archived')
    OR stewardship_campaign_now_v1() >= p.ends_at THEN 'closed'
  WHEN m.mode='production' AND (c.delivery_paused
    OR c.state NOT IN ('scheduled','active')) THEN 'paused'
  WHEN k.go_live_gate THEN 'go_live'
  WHEN k.population_dirty OR s.snapshot_id IS NULL
    OR k.source_snapshot_id IS DISTINCT FROM s.snapshot_id
    OR k.source_generation IS DISTINCT FROM s.generation THEN 'source'
  WHEN EXISTS (SELECT 1 FROM stewardship_campaign_work_gate g
        WHERE g.campaign_id=m.campaign_id AND g.state<>'released')
    OR (m.mode='production' AND EXISTS (SELECT 1 FROM stewardship_activation_catchup a
        WHERE a.campaign_id=m.campaign_id AND a.completed_at IS NULL))
    OR (m.purpose='reminder' AND EXISTS (
        SELECT 1 FROM stewardship_restore_delivery_hold h
        JOIN stewardship_schedule_definition i ON i.id=h.definition_id
        WHERE i.campaign_id=m.campaign_id AND i.kind='initial' AND h.mode=m.mode
          AND h.target=o.target AND h.slot='once' AND h.state='unreviewed'))
    -- This message's own email is held after a restore (#537): undecided or
    -- assumed sent. The single path then waits or cancels it.
    OR EXISTS (SELECT 1 FROM stewardship_restore_delivery_hold own
        WHERE own.definition_id=o.definition_id AND own.mode=m.mode
          AND own.target=o.target AND own.slot=o.slot
          AND own.state IN ('unreviewed','assumed_delivered'))
    THEN 'hold'
END
FROM (SELECT 1) AS one
LEFT JOIN stewardship_outbox_message m ON m.id=%s
LEFT JOIN stewardship_system_configuration r ON true
LEFT JOIN stewardship_campaign c ON c.id=m.campaign_id
LEFT JOIN stewardship_campaign_configuration p ON p.id=c.active_configuration_id
LEFT JOIN stewardship_schedule_occurrence o ON o.id=m.semantic_key
LEFT JOIN stewardship_schedule_definition d ON d.id=o.definition_id
LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=m.campaign_id
LEFT JOIN stewardship_source_current s ON s.singleton
"""


def _held_now(message_id):
    """Why this message must not be launched now, or None (one lock-free read).

    A pause, a mode change, a close, the go-live gate or a schedule change is
    a transition under the work-order lock, so a batch committed before it
    may still hold "submitting" messages. Each later message is checked here
    just before its send against what family_mail_dispatch.disposition and
    the planning scope check under the lock (HELD_NOW_SQL), and, if held,
    recorded as definitely unsent instead (retried, or cancelled by the
    single path, once it is looked at again). A message already being sent
    finishes, as on the one-at-a-time path. The SQL guards refuse any new
    "submitting" write.
    """
    with connection.cursor() as cursor:
        cursor.execute(HELD_NOW_SQL, [message_id])
        return cursor.fetchone()[0]


def _record_timeout(what, task_id, limit, elapsed, count, *, helper=None):
    """Durably log work the bulk send stopped at a time limit (#293)."""
    from parishkit.stewardship.audit.timeouts import record_timeout_within
    from parishkit.stewardship.observability import Event

    record_timeout_within(
        2,
        Event.TASK_TIMED_OUT,
        level="WARNING",
        what=what,
        helper=helper,
        task_id=task_id,
        limit_seconds=limit,
        elapsed_seconds=elapsed,
        count=count,
    )


def with_bulk(handler, bulk):
    """Attach a bulk entry point to a compiled handler (None leaves it off)."""
    return handler if bulk is None else replace(handler, bulk=bulk)
