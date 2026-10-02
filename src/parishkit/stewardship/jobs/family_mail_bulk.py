"""Bulk Family send (#430): prepare and send many Family messages per lock turn.

This is an optional, faster path for the large scheduled Family sends
(invitations and reminders). It is off by default and turned on by the
deployment setting ``bulk_family_send`` (environment variable
``PARISHKIT_STEWARDSHIP_BULK_FAMILY_SEND=1``) on the worker and mail-dispatch
services. Off, the consumers run exactly the one-task-per-hint path.

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
  its one SMTP session, outside any transaction, and one more lock transaction
  records every outcome and completes every task.

The rows written are the rows the one-at-a-time path writes, with the same
owners, so either path can finish or recover what the other started, and
turning the switch off mid-send is safe.

A batch ends at whichever comes first: its item limit or its lock-hold
budget, checked between items. The budget bounds how long one transaction
holds the lock (Family form requests, Admin pages, the scheduler and the
other consumer wait for it). Anything a batch cannot finish (an item a guard
or admission check refuses, a held or paused message, a changed selection,
any error) is rolled back to its savepoint, untouched, and is later handled
by the one-at-a-time path when the scheduler hints it.
"""

import logging
from dataclasses import dataclass, replace
from time import monotonic
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
# Default and bounds for B, the most messages one send batch marks
# "submitting" before sending them. A crash between that commit and the
# outcome commit leaves up to B messages delivery_unknown for an
# Administrator to settle, so the default is small. The lock-hold budget
# below usually ends a batch first (a few messages locally, one or two on
# the validation host), so B is a ceiling on that exposure, not a target.
SEND_BATCH = 20
SEND_BATCH_RANGE = range(1, 101)
# Most seconds one batch transaction keeps adding items while it holds the
# work-order lock (checked between items, so one item more can follow). The
# items themselves are the same guarded writes as before; this only bounds
# how long others wait for the lock behind one batch. It must stay well under
# the shortest wait any other lock taker allows itself: a running task's
# lease renewal gives up after 2 s (jobs/lifetime.renew_once, which stops
# that task), an in-flight mail check after 1 s (skipped and logged), and a
# Family login waits for the runtime row a preparation locks. One Family
# item takes about 0.1-0.2 s locally and roughly 3-4 times that on the
# validation host, so this keeps a batch hold near one second there, close
# to a single item's hold on the one-at-a-time path.
HOLD_SECONDS = 0.5
# How long one hint's consumer keeps taking further batches (the drain)
# before it returns to its queue, so other mail (receipts, digests) and
# other tasks are not kept waiting behind a long send.
DRAIN_SECONDS = 60
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
# No new message is launched later than this after the batch's claim; any
# message still unsent then is recorded as definitely unsent (retried later).
SEND_CUTOFF_SECONDS = 150
# Provider deadline of the first message in a send batch (the one-at-a-time
# path's PROVIDER_SECONDS) and the allowance added for each message ahead of
# it in the batch, capped so recovery after a crash is not delayed long.
FIRST_DEADLINE_SECONDS = 30
DEADLINE_STEP_SECONDS = 5
MAX_DEADLINE_SECONDS = 180
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


def _candidates(task_type, limit, exclude=(), purposes=None):
    """Due, claimable task ids of one type, oldest first, read without a lock.

    Like the hint pre-check in dispatch, this only chooses what to try: every
    item is claimed under the locks with the full admission check, and one
    found already claimed (by the other consumer, say) is skipped.
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
    return list(rows.order_by("not_before", "id").values_list("pk", flat=True)[:limit])


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


def _run_batch(ids, item, *, limit, seconds=None):
    """Run ``item`` for each id in one lock transaction, each in a savepoint.

    Stops after ``limit`` successes, once ``seconds`` of lock hold have
    passed (checked between items), or after REFUSALS_TO_STOP refusals in a
    row. Returns ``(done, tried, hold)``: what ``item`` returned for each
    success, every id tried, and how long the lock was held.
    """
    seconds = HOLD_SECONDS if seconds is None else seconds
    done, tried, refusals = [], [], 0
    with work_transaction():
        started = monotonic()
        for run_id in ids:
            if len(done) >= limit or refusals >= REFUSALS_TO_STOP:
                break
            if tried and monotonic() - started >= seconds:
                break
            tried.append(run_id)
            try:
                with transaction.atomic(), remembered_scopes():
                    done.append(item(run_id))
                refusals = 0
            except _Skip:
                pass
            except Exception as error:
                # Rolled back to the savepoint: the task is untouched and the
                # one-at-a-time path handles it when it is next hinted.
                refusals += 1
                DEBUG.debug("bulk item %s left for the single path: %r", run_id, error)
    return done, tried, monotonic() - started


def _drain(step, stop):
    """Repeat ``step`` (one batch) while it finds work, for DRAIN_SECONDS.

    ``step(exclude)`` returns how many items it finished and the ids it
    tried, which later batches of this drain skip.
    """
    started, tried, total = monotonic(), set(), 0
    while monotonic() - started < DRAIN_SECONDS:
        if stop is not None and stop.is_set():
            break
        finished, attempted = step(tried)
        tried.update(attempted)
        total += finished
        if not finished:
            break
    return total


# --- Preparation -------------------------------------------------------------


def preparation_bulk(handler, *, general, mac, public, public_origin, settings):
    """Build the general worker's bulk preparation entry point, or None.

    The returned callable takes a hint's run id: it prepares batches of due
    preparation tasks (the hinted one usually among them), and returns. The
    caller then runs the ordinary hint path, which finds the hinted task done
    or handles it singly.
    """
    if not settings.enabled:
        return None
    from .family_mail_preparation import prepare_occurrence
    from .family_mail_tasks import TASK_TYPE, disposition, owned_preparation

    def item(run_id):
        """Claim, prepare and complete one preparation task."""
        claim, correlation_id = _claim(run_id, handler, uuid4(), 60)
        task = lock_task_claim(claim)
        ticket = owned_preparation(_status(task))
        terminal = disposition(ticket)
        if terminal is None:
            terminal = prepare_occurrence(
                ticket,
                claim,
                general=general,
                mac=mac,
                public=public,
                public_origin=public_origin,
            )
        _transition(claim, correlation_id, handler, terminal)
        return run_id

    def step(exclude):
        """Prepare one batch; see _run_batch."""
        ids = _candidates(TASK_TYPE, PREPARE_BATCH * 2, exclude)
        if not ids:
            return 0, ()
        done, tried, hold = _run_batch(ids, item, limit=PREPARE_BATCH)
        DEBUG.debug(
            "bulk preparation: %d of %d tried in %.2f s of lock hold",
            len(done),
            len(tried),
            hold,
        )
        return len(done), tried

    def run(run_id, *, stop=None):
        """Drain due preparation in batches, then return to the hint path."""
        if connection.in_atomic_block:
            raise RuntimeError("Bulk preparation must own its transactions.")
        try:
            return _drain(step, stop)
        finally:
            connections.close_all()

    return run


# --- Sending -----------------------------------------------------------------


@dataclass
class _Sending:
    """One message of a send batch, from its "submitting" commit to its outcome."""

    claim: TaskClaim
    correlation_id: object
    message_id: object
    mail: object
    deadline: object
    attempt: int
    result: FamilyDeliveryResult | None = None
    stats: dict | None = None


def delivery_bulk(
    handler, *, private, public_origin, credential_path, circuit, session, settings
):
    """Build a mail consumer's bulk send entry point, or None.

    The returned callable takes a hint's run id and sends batches of due
    scheduled Family messages, then returns; the caller then runs the
    ordinary hint path, which handles anything left (the hinted task when it
    is a receipt, digest or test, or held, say).
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

    batch = settings.send_batch

    def begin_item(run_id, context):
        """Claim one message's task and commit its "submitting" state."""
        claim, correlation_id = _claim(run_id, handler, uuid4(), SEND_LEASE_SECONDS)
        _transition(
            claim,
            correlation_id,
            handler,
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
        position = len(context["items"])
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
        if prepared is None:
            # Selection changed or held; nothing of it is kept (savepoint).
            raise _Skip
        mail, deadline, _, attempt = prepared
        context["recipients"] += recipients
        context["items"].append(
            _Sending(claim, correlation_id, message.pk, mail, deadline, attempt)
        )
        return run_id

    def begin(exclude, candidate):
        """One lock transaction: up to B messages committed as "submitting".

        The transaction also stops at the lock-hold budget, so a batch is
        whichever is smaller. Its messages are then sent at once, before
        any further batch is begun: committing more batches first would
        hold no fewer lock turns (each is budget-bound anyway), but would
        widen what a crash leaves unknown and let early messages' provider
        deadlines run out before their turn to be sent.
        """
        ids = _candidates(TASK_TYPE, batch * 2, exclude, purposes=BULK_PURPOSES)
        if not ids:
            return None, ()
        context = {
            "items": [],
            "sent": circuit.daily_sends(),
            "recipients": 0,
            # Leave one largest message (100 recipients, the SQL cap) of
            # room, so the batch never decides at the limit itself.
            "allowed": DAILY_SEND_LIMIT - RESERVED_SENDS - 100,
        }
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
            _, tried, hold = _run_batch(
                ids, lambda run_id: begin_item(run_id, context), limit=batch
            )
            # The clock the provider deadlines were written on, read just
            # before commit, paired with this process's monotonic clock.
            context["db_now"], context["mono"] = database_now(), monotonic()
        DEBUG.debug(
            "bulk send: %d of %d tried submitting in %.2f s of lock hold",
            len(context["items"]),
            len(tried),
            hold,
        )
        return context, tried

    def send_all(context, candidate):
        """Send each committed message in turn, outside any transaction."""
        workspace = context["workspace"]
        base = workspace.settings
        claimed = context["mono"]
        cut = []
        for item in context["items"]:
            started = monotonic()
            remaining = (item.deadline - context["db_now"]).total_seconds() - (
                started - context["mono"]
            )
            item.result = FamilyDeliveryResult(
                FamilyDeliveryStatus.TRANSIENT,
                len(item.mail.recipients),
                health=ProviderHealth.UNOBSERVED,
            )
            item.stats = {"transport": "batched"}
            if started - claimed >= SEND_CUTOFF_SECONDS:
                cut.append(item)
                continue
            held = (
                "deadline"
                if remaining <= 1
                else "circuit"
                if circuit.blocks_new_send() or circuit.limit_remaining()
                else _held_now(item.message_id)
            )
            if held is not None:
                # Not launched: definitely unsent, so it is retried later.
                DEBUG.debug(
                    "bulk send: message %s not sent (%s)", item.message_id, held
                )
                continue
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
                    check=_lease_check(claimed),
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
        if cut:
            _record_cutoff(cut[0].claim.run_id, len(cut), monotonic() - claimed)

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

    def finish_item(item):
        """Record one outcome and settle its task, as the single path does."""
        result = _with_stats(item.result, item.stats or {})
        status = finish_submission(item.message_id, item.claim, result)
        if status.state.value == "retry_wait":
            if result.limit is not None or result.health is ProviderHealth.UNAVAILABLE:
                _transition(
                    item.claim,
                    item.correlation_id,
                    handler,
                    "progress",
                    progress=(0, 0),
                    phase=TaskPhase.RECONCILING,
                )
            _transition(
                item.claim,
                item.correlation_id,
                handler,
                "retryable_failure",
                retry_seconds=result_retry_seconds(result, item.attempt),
            )
        else:
            _transition(
                item.claim,
                item.correlation_id,
                handler,
                "complete"
                if status.state.value == "delivered"
                else "permanent_failure",
            )

    def finish(items):
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
                    started = monotonic()
                    while left and (not chunk or monotonic() - started < HOLD_SECONDS):
                        item = left.pop(0)
                        chunk.append(item)
                        try:
                            with transaction.atomic():
                                finish_item(item)
                        except Exception as error:
                            _unrecorded(error)
            except Exception:
                for item in chunk:
                    try:
                        with work_transaction():
                            finish_item(item)
                    except Exception as error:
                        _unrecorded(error)

    def step(exclude):
        """One send batch: submitting commit, sends, outcome commit."""
        if circuit.blocks_new_send() or circuit.limit_remaining():
            return 0, ()
        candidate = read_private(credential_path)
        # Settle retired helpers before anything commits "submitting":
        # failing to reap one is fatal, and must not strand sent-looking mail.
        session.reap()
        context, tried = begin(exclude, candidate)
        if context is None:
            return 0, ()
        items = context["items"]
        if not items:
            return 0, tried
        try:
            send_all(context, candidate)
        finally:
            # Whatever happened (even a fatal helper drain failure), record
            # what is known: unlaunched messages are definitely unsent.
            for item in items:
                if item.result is None:
                    item.result = FamilyDeliveryResult(
                        FamilyDeliveryStatus.TRANSIENT,
                        len(item.mail.recipients),
                        health=ProviderHealth.UNOBSERVED,
                    )
            finish(items)
        return len(items), tried

    def run(run_id, *, stop=None):
        """Drain due Family mail in batches, then return to the hint path."""
        if connection.in_atomic_block:
            raise RuntimeError("Bulk sending must own its transactions.")
        try:
            return _drain(step, stop)
        finally:
            connections.close_all()

    return run


def _unrecorded(error):
    """Log an outcome the bulk send could not record (recovery settles it)."""
    LOG.error(
        "A Family mail outcome could not be recorded; recovery will mark it "
        "delivery unknown.",
        exc_info=error,
    )


def _lease_check(claimed):
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
        if elapsed >= SEND_LEASE_SECONDS - LEASE_MARGIN_SECONDS:
            _record_cutoff(None, 1, elapsed)
            raise TimeoutError("Bulk send batch outlived its lease margin.")

    return check


def _held_now(message_id):
    """Why this message must not be launched now, or None (lock-free reads).

    A pause, a mode change, a close or the go-live gate is a transition under
    the work-order lock, so a batch committed before it may still hold
    "submitting" messages. Each later message is checked here just before
    its send and, if held, recorded as definitely unsent instead (retried
    once admitted again). A message already being sent finishes, as on the
    one-at-a-time path. The SQL guards refuse any new "submitting" write.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )
    from parishkit.stewardship.campaigns.models import Campaign

    from .outbox_models import OutboxMessage

    row = (
        OutboxMessage.objects.filter(pk=message_id)
        .values_list("mode", "campaign_id")
        .first()
    )
    if row is None:
        return "missing"
    mode, campaign_id = row
    runtime = SystemConfiguration.objects.values_list(
        "mode", "current_campaign_id"
    ).first()
    campaign = (
        Campaign.objects.filter(pk=campaign_id)
        .values_list("state", "delivery_paused")
        .first()
    )
    if runtime != (mode, campaign_id) or campaign is None:
        return "scope"
    state, paused = campaign
    if state in {"closed", "archived"}:
        return "closed"
    if mode == "production" and paused:
        return "paused"
    if CampaignCredentialState.objects.filter(
        campaign_id=campaign_id, go_live_gate=True
    ).exists():
        return "go_live"
    return None


def _record_cutoff(task_id, count, elapsed):
    """Durably log messages a batch stopped launching at its cutoff (#293)."""
    from parishkit.stewardship.audit.timeouts import record_timeout_within
    from parishkit.stewardship.observability import Event

    record_timeout_within(
        2,
        Event.TASK_TIMED_OUT,
        level="WARNING",
        what="lease",
        task_id=task_id,
        limit_seconds=SEND_CUTOFF_SECONDS,
        elapsed_seconds=elapsed,
        count=count,
    )


def with_bulk(handler, bulk):
    """Attach a bulk entry point to a compiled handler (None leaves it off)."""
    return handler if bulk is None else replace(handler, bulk=bulk)
