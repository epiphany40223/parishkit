"""Maintained MAIL-only Family consumer; no network work inside a transaction."""

import itertools
import logging
import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from random import randint
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from django.db import connection, transaction
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.logging import log_extra
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.integration_selection import switching
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
    elapsed_ms,
)
from parishkit.stewardship.family_delivery_process import (
    FamilyMailSession,
    submit_digest,
    submit_family,
    submit_weekly,
)
from parishkit.stewardship.observability import emit_failure
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.sender_name import configured_sender_name
from parishkit.stewardship.storage import StorageInvariantError

from .connection_reuse import drop_unusable, hint_kept, release
from .dispatch import Handler, RecoveryPlan
from .family_mail_dispatch import (
    CAPPED_RETRY_SECONDS,
    DAILY_SEND_LIMIT,
    LIMIT_RETRY_SECONDS,
    MAILBOX_LIMITS,
    MAX_ATTEMPTS,
    RESERVED_SENDS,
    FamilyDeliveryHeld,
    begin_submission,
    bound_dispatch,
    cancel_unsent,
    disposition,
    failure_identity,
    finish_submission,
    over_daily_limit,
    result_retry_seconds,
    retry_delay,
    sends_in_last_day,
)
from .family_mail_dispatch_recovery import (
    cancel_abandoned_family_test,
    definitely_unsent,
    record_abandoned_submission,
)
from .models import TaskRun, TaskRunEvent
from .ownership import database_now, lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .storage import _status

LOG = logging.getLogger(__name__)
# How soon an abandoned, still-unsent delivery may be claimed again.
RECOVERY_RETRY_SECONDS = 30
# How long Family mail pauses after a shared outage before one probe is sent.
OUTAGE_RECOVERY_SECONDS = 600
# How often a mail consumer re-reads the deployment's last-24-hour recipient
# count while it is far from the daily limit, and how near (in recipients)
# the bulk limit it starts re-reading it for every message instead.
DAILY_COUNT_SECONDS = 15
NEAR_DAILY_LIMIT = 100
# How often a sending worker re-verifies its Task ownership in SQL while the
# private helper runs; see _inflight_check.
INFLIGHT_VERIFY_SECONDS = 1.0


# This mail consumer process's Family mail circuit, once its handler is built,
# so its service status record can report the sender's state (ADM-13).
FAMILY_CIRCUIT = None


def family_sender_state():
    """The Family mail sender's state in this process (``DeliveryCircuit``)."""
    if FAMILY_CIRCUIT is None:
        return "running", None
    return FAMILY_CIRCUIT.sender_state()


class DeliveryCircuit:
    """Bound shared-outage probes across Families within this worker lifetime.

    A known temporary handshake outage waits a minute before another probe;
    three consecutive outages (or one SYSTEMIC fault) halt the run. No cooldown
    changes durable mail outcomes or prevents settling work already in flight.
    Restart resets this process-local circuit.

    With ``recovery_seconds`` a halt lifts by itself after that cooldown, so a
    short Google or network outage during a send pauses mail instead of
    stopping it until someone restarts the worker. After a pause exactly one
    probe is admitted: one more outage result pauses again at once, so a long
    outage costs one attempt per cooldown, not three. ``repeated`` tells the
    caller a pause is not the first since the last healthy result, so it can
    be reported quietly. ``systemic_stops`` keeps a
    SYSTEMIC fault (a configuration or credential problem that waiting cannot
    fix) stopped until restart even then. The durable record of both is the
    provider evidence: the stored outcomes that halt the circuit also raise
    the CRITICAL ``mail_provider_failed`` incident (schema/mail_health.sql), and
    the first healthy outcome after the cooldown resolves it
    (jobs/mail_health.py).

    Each mail consumer process has its own circuit, and the mail-dispatch
    container runs two (so do the Administrator alert and security mail
    circuits, jobs/operational_mail_tasks.py). Every outcome a circuit
    reacts to is durable, so sharing it is needed only for a stop:
    - SYSTEMIC: the stop is container-wide through ``shared_stop``. The
      other process may already have one message in flight, so a SYSTEMIC
      fault can fail up to two messages (one per process) before both stop.
      A fault before DATA was sent leaves its message definitely unsent, so
      an Administrator retry is safe. One after DATA was sent is delivery
      unknown and never retried automatically; it must be settled from
      provider evidence (family_delivery.py).
    - Outage: a process pauses after its own three outage results, so an
      outage costs at most three attempts per process (six instead of
      three), and each cooldown ends with one probe per process. A
      connection that never reached DATA is definitely unsent and retried
      without spending the message's budget; one lost after DATA is
      delivery unknown and never retried, as with one process. The CRITICAL
      pause log may appear once per process.
    - Mailbox limit: a refusal holds only the process that saw it. The other
      process is held by its own next refusal, so at most one more message
      is refused. That message is also definitely unsent and is retried
      after the limit without spending its budget.
    - Daily count: the count behind the daily cap is shared through
      PostgreSQL; see daily_sends.
    """

    def __init__(
        self, *, recovery_seconds=None, systemic_stops=False, shared_stop=None
    ):
        """Keep circuit state shared by all dispatches using this handler.

        ``shared_stop`` is a file that makes a SYSTEMIC stop container-wide:
        the process that sees one creates it, and every mail consumer
        process in the container stops sending new mail when it exists (the
        main process removes it at startup, see runtime_process).
        """
        if recovery_seconds is not None and (
            type(recovery_seconds) is not int or not 60 <= recovery_seconds <= 3600
        ):
            raise ValueError("Circuit recovery requires a bounded cooldown.")
        self.recovery_seconds = recovery_seconds
        self.systemic_stops = systemic_stops
        self.shared_stop = shared_stop
        # A halt that no cooldown lifts (SYSTEMIC under systemic_stops).
        self.stopped = False
        # Whether a pause was already reported since the last healthy result.
        self.paused_before = False
        self.repeated = False
        self.recover_after = 0.0
        self.halted = Event()
        self.lock = Lock()
        self.failures = 0
        self.probe_after = 0.0
        self.limit_until = 0.0
        self.counted_at = None
        self.counted = 0
        self.capped = False

    def daily_sends(self):
        """The deployment's last-24-hour recipient count, from PostgreSQL.

        Every queued message consults it, so far from the limit it is re-read
        at most every DAILY_COUNT_SECONDS: a fresh count per message would
        repeat the same query thousands of times during a bulk send. Within
        NEAR_DAILY_LIMIT recipients of the bulk limit it is re-read for every
        message instead.

        The count is shared state: every mail consumer process (the
        mail-dispatch container runs two) reads the same stored outcomes, so
        the processes cannot each spend the whole limit. Once a process's
        count is within NEAR_DAILY_LIMIT, what it cannot see is only the
        other process's message still in flight (submitted, not yet
        settled). The deployment can then pass the bulk limit by at most
        that one message, and SQL caps a message at 100 recipients
        (schema/delivery.sql), so at worst 1,700 of 1,800: still inside
        RESERVED_SENDS.

        Far from the limit, whether a process is "near" is judged from a
        count up to DAILY_COUNT_SECONDS old. That is a rate assumption, not
        a hard bound: it misses whatever both processes settled in that
        time, about 35 recipients at launch speed, well under
        NEAR_DAILY_LIMIT. Only sending more than 100 recipients in 15
        seconds could cross into the last 100 unseen.
        """
        now = monotonic()
        near = self.counted >= (DAILY_SEND_LIMIT - RESERVED_SENDS - NEAR_DAILY_LIMIT)
        if (
            self.counted_at is None
            or near
            or now - self.counted_at >= DAILY_COUNT_SECONDS
        ):
            self.counted = sends_in_last_day()
            self.counted_at = now
        return self.counted

    def note_capped(self, capped):
        """Log once when bulk sending reaches the daily limit and when it resumes."""
        with self.lock:
            changed, self.capped = capped != self.capped, capped
        if changed and capped:
            LOG.critical(
                "Family mail reached this deployment's daily sending limit; "
                "bulk sending resumes automatically as the day rolls on."
            )
        elif changed:
            LOG.warning("Family mail is below its daily sending limit again.")

    def blocks_new_send(self):
        """Admission alone observes the circuit; draining never consults it."""
        self._follow_shared_stop()
        with self.lock:
            recovered = (
                self.halted.is_set()
                and self.recovery_seconds is not None
                and not self.stopped
                and monotonic() >= self.recover_after
            )
            if recovered:
                self.halted.clear()
                # One probe: its failure would be the third in a row again.
                self.failures = 2
                self.probe_after = 0.0
            blocked = self.halted.is_set() or monotonic() < self.probe_after
        if recovered:
            LOG.warning("The mail provider cooldown has ended; sending resumes.")
        return blocked

    def _follow_shared_stop(self):
        """Stop too when another mail consumer in this container has stopped."""
        if self.shared_stop is None or self.stopped:
            return
        try:
            stopped = self.shared_stop.exists()
        except OSError:
            stopped = False
        if not stopped:
            return
        with self.lock:
            newly = not self.stopped
            self.stopped = True
            self.halted.set()
        if newly:
            LOG.critical(
                "Another mail consumer stopped Family mail after a configuration "
                "or credential fault; this one stops too until the mail worker "
                "restarts."
            )

    def _share_stop(self):
        """Tell the container's other mail consumers about a SYSTEMIC stop."""
        if self.shared_stop is None:
            return
        try:
            from parishkit.stewardship.installer_health import mark_stopped

            mark_stopped(self.shared_stop)
        except Exception as error:
            # The other consumer then stops on its own SYSTEMIC outcome.
            emit_failure(error)

    def hold(self, seconds):
        """Hold new sends for ``seconds`` after a Gmail sending limit.

        Return true when this starts a new hold (so it is reported once). A
        limit is not a failure: it is kept apart from the outage probe, so a
        later healthy observation cannot clear it, and it lifts by itself
        instead of stopping the run.
        """
        with self.lock:
            now = monotonic()
            newly = self.limit_until <= now
            self.limit_until = max(self.limit_until, now + seconds)
            return newly

    def limit_remaining(self):
        """Seconds left in the current sending-limit hold (0 when none)."""
        with self.lock:
            return max(0.0, self.limit_until - monotonic())

    def sender_state(self):
        """This sender's state and seconds until it ends, for its status record.

        One of ``service_status_models.SENDER_STATES``: halted (a SYSTEMIC
        stop, here or in the other consumer, that only a restart lifts),
        paused after an outage (until its cooldown ends), held at Gmail's
        sending limit (until the hold ends), waiting for the daily limit, or
        running. The seconds are ``None`` when the end is not known. Display
        only (ADM-13): nothing decides anything from it, and it changes
        nothing. The other consumer's stop marker is only looked at here;
        this circuit follows it (and logs that) at its next admission check.
        """
        try:
            shared = self.shared_stop is not None and self.shared_stop.exists()
        except OSError:
            shared = False
        with self.lock:
            now = monotonic()
            if shared or (
                self.halted.is_set() and (self.stopped or self.recovery_seconds is None)
            ):
                return "halted", None
            if self.halted.is_set():
                return "outage_paused", max(0.0, self.recover_after - now)
            if self.limit_until > now:
                return "gmail_held", self.limit_until - now
            if self.capped:
                return "daily_limit", None
            return "running", None

    def observe(self, health):
        """Return true when a shared failure newly stops this sending run."""
        with self.lock:
            if health is ProviderHealth.UNOBSERVED:
                return False
            if health is ProviderHealth.UNAVAILABLE:
                self.failures += 1
                self.probe_after = monotonic() + 60
            else:
                self.failures = 0
                self.probe_after = 0.0
                if health is ProviderHealth.HEALTHY:
                    self.paused_before = False
            if health is ProviderHealth.SYSTEMIC or self.failures >= 3:
                newly_halted = not self.halted.is_set()
                self.halted.set()
                if newly_halted:
                    self.repeated = self.paused_before
                    self.paused_before = True
                if newly_halted and self.recovery_seconds is not None:
                    self.recover_after = monotonic() + self.recovery_seconds
                # A SYSTEMIC fault seen during an outage pause (for example an
                # in-flight result) still turns it into a stop; report that too.
                if health is ProviderHealth.SYSTEMIC and self.systemic_stops:
                    newly_halted = newly_halted or not self.stopped
                    shared = not self.stopped
                    self.stopped = True
                else:
                    shared = False
            else:
                return False
        if shared:
            self._share_stop()
        return newly_halted


def preparation_attempts(status):
    """Exclude journaled admission holds from the bounded pre-provider budget."""
    held = TaskRunEvent.objects.filter(
        run_id=status.run_id,
        action__in=("retryable_failure", "recovery_retry"),
        phase=TaskPhase.RECONCILING,
    ).count()
    current_hold = (
        status.state in {"running", "abandoned"}
        and status.phase is TaskPhase.RECONCILING
    )
    return max(0, status.attempt - held - int(current_hold))


def recovery_plan(status):
    """Decide abandoned work's disposition from durable evidence; write nothing.

    Admission repeats this decision (including the scheduler's recovery
    hints), so it must stay pure; ``recover_delivery`` applies its effects.
    A missing receipt never authorizes SMTP retransmission after submission.
    """
    row = bound_dispatch(status)
    if status.state != "abandoned":
        raise PermissionError("Family recovery requires an abandoned Task.")
    if row.state == "submitting":
        if row.provider_deadline > database_now():
            return None
        # The recovery owner records the uncertain attempt before this plan
        # is applied; the message then reads delivery_unknown.
        return RecoveryPlan("recovery_fail")
    action = {
        "delivered": "recovery_complete",
        "cancelled": "recovery_cancel",
        "delivery_unknown": "recovery_fail",
        "permanent_failure": "recovery_fail",
        "pending": "recovery_retry",
        "retry_wait": "recovery_retry",
    }[row.state]
    if action == "recovery_retry" and preparation_attempts(status) >= MAX_ATTEMPTS:
        # A chosen-Family test nothing else can settle is cancelled, not left
        # unsent; the SQL recovery clause admits exactly these two states and
        # its literal budget mirrors MAX_ATTEMPTS.
        action = (
            "recovery_cancel"
            if row.purpose == "family_test" and definitely_unsent(row)
            else "recovery_fail"
        )
    return (
        RecoveryPlan(action, retry_seconds=RECOVERY_RETRY_SECONDS)
        if action == "recovery_retry"
        else RecoveryPlan(action)
    )


def recover_delivery(status):
    """Apply the plan's owning effects under the abandoned Task, then return it.

    Only the recovering MAIL consumer reaches this; admission never does.
    """
    plan = recovery_plan(status)
    if plan is None:
        return None
    row = bound_dispatch(status)
    if row.state == "submitting":
        record_abandoned_submission(status, actor_id=uuid4())
    elif plan.action == "recovery_cancel" and row.state != "cancelled":
        cancel_abandoned_family_test(status, actor_id=uuid4())
    return plan


def admit_task(action, status, *, store, circuit):
    """Metadata/drain observes stored outcomes even after live scope is revoked."""
    row = bound_dispatch(status)
    if action == "lease_expired" or (
        action == "recovery_hint" and status.state == "running"
    ):
        return True
    if action in {
        "recovery_hint",
        "recovery_complete",
        "recovery_fail",
        "recovery_cancel",
        "recovery_retry",
    }:
        if row.state == "submitting":
            return action == "recovery_hint" and row.provider_deadline <= database_now()
        plan = recovery_plan(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action == "complete":
        return row.state == "delivered"
    if action == "safe_cancel":
        return row.state == "cancelled"
    if action == "permanent_failure":
        return row.state in {"permanent_failure", "delivery_unknown"} or (
            row.state in {"pending", "retry_wait"}
            and preparation_attempts(status) >= MAX_ATTEMPTS
        )
    if action == "retryable_failure":
        return row.state in {"pending", "retry_wait"}
    if action in {"heartbeat", "progress"}:
        return True
    if row.state in {
        "delivered",
        "cancelled",
        "permanent_failure",
        "delivery_unknown",
        "submitting",
    }:
        return action in {"effect", "progress"} or (
            row.state != "submitting" and action in {"hint", "claim"}
        )
    if action not in {"hint", "claim", "effect", "progress"}:
        return False
    reason = disposition(row)
    if reason == "delivery_paused":
        return row.pause_hold_id is None or action in {"effect", "progress"}
    if reason is not None:
        return True
    if circuit.blocks_new_send():
        # A stop or pause that lands between the claim and its effect (the
        # other mail consumer's SYSTEMIC stop, say) holds the claimed
        # message like the configuration hold below, without spending one of
        # its preparation attempts.
        if action == "effect":
            raise FamilyDeliveryHeld("Family mail sending is paused or stopped.")
        return False
    try:
        mail_authority(store)
    except ConfigError:
        if action == "effect":
            raise FamilyDeliveryHeld("Mail configuration requires recovery.") from None
        return False
    return True


def delivery_handler(
    store,
    *,
    private=None,
    public_origin=None,
    credential_path=None,
    scheduler=False,
    batched=True,
    shared_stop=None,
    bulk=None,
):
    """Schedulers own metadata only; mounted private keys stay in the mail worker.

    ``batched`` (the default) sends Family mail through one long-lived helper
    (#284). False is the operator fallback, deployment setting
    ``family_mail_transport: per_message``: one helper per message, as before.
    ``shared_stop`` makes a SYSTEMIC stop container-wide (DeliveryCircuit).
    ``bulk`` (a family_mail_bulk.BulkSettings) turns on the batched bulk send
    of scheduled Family mail (#430); it needs the batched transport.
    """
    if not scheduler and not isinstance(credential_path, Path):
        raise TypeError("Family dispatch requires an installed Workspace path.")
    circuit = DeliveryCircuit(
        recovery_seconds=OUTAGE_RECOVERY_SECONDS,
        systemic_stops=True,
        shared_stop=shared_stop,
    )
    if not scheduler:
        global FAMILY_CIRCUIT
        FAMILY_CIRCUIT = circuit
    # One batched private helper for this worker's Family messages (#284). It
    # spans Tasks but holds no lease or database state; see FamilyMailSession.
    session = FamilyMailSession() if batched and not scheduler else None
    handler = Handler(
        queue=WorkQueue.MAIL,
        admit=partial(admit_task, store=store, circuit=circuit),
        recover=recover_delivery,
        execute=_unavailable
        if scheduler
        else partial(
            _execute,
            private=private,
            public_origin=public_origin,
            credential_path=credential_path,
            circuit=circuit,
            session=session,
        ),
        scope=work_transaction,
    )
    if scheduler or bulk is None:
        return handler
    from .family_mail_bulk import delivery_bulk, with_bulk

    return with_bulk(
        handler,
        delivery_bulk(
            handler,
            private=private,
            public_origin=public_origin,
            credential_path=credential_path,
            circuit=circuit,
            session=session,
            settings=bulk,
        ),
    )


def log_family_mail_transport(transport):
    """Say once, at mail-dispatch startup, which Family mail transport is in use.

    The fallback is a WARNING: it is slower, and it must not stay in effect
    unnoticed after the operator meant to return to batched sending.
    """
    if transport == "per_message":
        LOG.warning(
            "Family mail uses one helper per message (the per_message fallback); "
            "recreate mail-dispatch without PARISHKIT_STEWARDSHIP_FAMILY_MAIL_"
            "TRANSPORT to return to batched sending."
        )
    else:
        LOG.info("Family mail uses one batched helper per mail worker.")


# Numbers the one-message helpers this worker process starts, so their
# statistics (#284) compare with a batched helper's helper_seq.
_ONE_SHOT = itertools.count(1)


def _waited(execution):
    """``wait_ms``: how long the Task ran after it was due; {} if unknown.

    Statistics never change behavior: this reads in its own savepoint, uses
    no clock the outcome depends on, and gives up silently on any error.
    """
    try:
        with transaction.atomic():
            due = (
                TaskRun.objects.only("not_before")
                .get(pk=execution.claim.run_id)
                .not_before
            )
        return {"wait_ms": max(0, round((timezone.now() - due).total_seconds() * 1000))}
    except Exception:
        return {}


def _with_stats(result, stats):
    """Attach worker-side send statistics; never let them affect the outcome.

    ``stats`` joins the helper's own (phase times, connection use) with the
    worker's: transport, helper numbering, ``wait_ms`` (the Task's due time
    to execution), ``request_ms`` (execution to the helper request, which
    includes preparation and the committed "submitting" state),
    ``submit_ms`` (the whole helper call) and ``total_ms`` (execution to
    outcome). Anything invalid is dropped, and the outcome settles as is.
    """
    try:
        return replace(result, stats={**(result.stats or {}), **stats})
    except Exception:
        return result


def _unavailable(execution):
    """A scheduler registration never grants private key or provider access."""
    raise PermissionError("Schedulers cannot submit Family mail.")


def _check(execution):
    """Continue draining under Task ownership, without new-send admission.

    Returns whether ownership was verified in SQL (False: the tick was
    skipped at a lock limit; see Execution.check_inflight).
    """
    try:
        return execution.check_inflight()
    finally:
        release()


def _inflight_check(execution):
    """Build the helper's in-flight check, verifying in SQL at most once a second.

    The helper calls its check as soon as the request is written and then
    every 0.25 s. Each SQL check joins the deployment-wide work-order lock,
    so a typical 0.5 s Gmail send cost two or three more lock waits per
    message, behind every other holder (#147). The message's "submitting"
    state was committed under that lock a moment before, so ownership was
    just verified. The process-local state (a failed renewal, a finished
    execution) is still checked on every tick; the SQL check (lease, fence
    and domain admission) runs only once INFLIGHT_VERIFY_SECONDS have passed
    since the last verification. A SQL check skipped at its lock limit does
    not count as one, so the next tick tries again. Losing ownership is then
    noticed up to a second later (plus any skipped ticks), well inside the
    60 s lease and the helper's own deadline.
    """
    verified = [monotonic()]

    def check():
        """Check local state now (a failed renewal, a finished execution, or
        a lease that has run out locally, #797) and, when due, ownership in
        SQL."""
        execution.control.check(allow_drain=True, inflight=True)
        if monotonic() - verified[0] < INFLIGHT_VERIFY_SECONDS:
            return
        if _check(execution):
            verified[0] = monotonic()

    return check


def _launch_budget(deadline):
    """Seconds left before this message's committed provider deadline.

    Only a clock read: the deadline was committed by begin_submission(), and
    nothing here writes or needs the writers' order. So it runs in a plain
    transaction (database_now() needs one), not under the deployment-wide
    work-order lock, which a source refresh can hold for a second or more at
    a time (#394). Every message used to queue behind that lock here.
    """
    with transaction.atomic():
        return (deadline - database_now()).total_seconds()


def _execute(
    execution, *, private, public_origin, credential_path, circuit, session=None
):
    """Pin credentials, durably begin, then run one bounded private submission.

    Family mail goes to the worker's batched helper ``session`` (#284); the
    rare digests keep their one-message helpers. Either way this message's
    "submitting" state and provider deadline are committed first, and its
    result is settled here before the next message is claimed.
    """
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Family mail requires maintained worker lifetime.")
    started = time.monotonic()
    submitted = False
    launched = False
    message = None
    # Whether this message started on a kept database session (#365).
    stats = {"db_kept": hint_kept()}
    try:
        # Read before effect(): near the daily limit this runs for every
        # message, and it needs no lock, so it stays outside the deployment-
        # wide work-order lock both mail consumers wait on.
        sent = circuit.daily_sends()
        with execution.effect():
            # The PREPARING phase commits with this effect's reads rather
            # than in a transaction of its own: one fewer wait for the
            # deployment-wide work-order lock per message (#147). If the
            # effect rolls back, the phase stays STARTING (set by the claim).
            # preparation_attempts() and limit_history() read the phase, but
            # treat STARTING and PREPARING alike and single out only
            # RECONCILING, so the retry budget and recovery are unchanged.
            # Keep it that way: a rule that treats STARTING as "not yet
            # attempted" would undercount effects that failed here.
            execution.progress(0, 0, phase=TaskPhase.PREPARING)
            message = bound_dispatch(_status(lock_task_claim(execution.claim)))
            terminal = {
                "delivered": "complete",
                "cancelled": "safe_cancel",
                "permanent_failure": "permanent_failure",
                "delivery_unknown": "permanent_failure",
            }.get(message.state)
            metadata_only = (
                terminal is not None
                or disposition(message, check_recipient=True) is not None
            )
            if not metadata_only:
                from parishkit.stewardship.accounts.runtime_models import (
                    SystemConfiguration,
                )

                configuration_id = (
                    SystemConfiguration.objects.get().active_configuration_id
                )
                workspace = AppliedIntegration.objects.get(
                    configuration_id=configuration_id, kind="google_workspace"
                )
                # Display only: the From name is not part of any admission check.
                sender_name = configured_sender_name(configuration_id)
                recipients = len(message.render.routed_recipients)
                stats.update(_waited(execution))
                capped = over_daily_limit(message.purpose, sent, recipients)
                bulk_capped = over_daily_limit("initial", sent)
        if terminal is not None:
            execution.transition(terminal)
            return
        if metadata_only:
            begin_submission(
                message.pk,
                execution.claim,
                private=private,
                public_origin=public_origin,
                metadata_only=True,
            )
            _finish_no_send(execution)
            return
        # A sending limit defers the message here, after its claim, rather
        # than refusing the claim: a refused claim is retried (and logged) on
        # every scan, while a deferral moves not_before and quiets the queue.
        # Neither spends the message's attempt budget.
        circuit.note_capped(bulk_capped)
        wait = circuit.limit_remaining()
        if capped or wait:
            # Jitter spreads the held messages out, so they do not all probe
            # the provider together the moment a hold lifts.
            _defer_held(
                execution,
                seconds=(max(60, int(wait)) if wait else CAPPED_RETRY_SECONDS)
                + randint(0, 300),
            )
            return
        candidate = read_private(credential_path)
        if file_fingerprint(candidate) != workspace.credential_fingerprint:
            # Mid-switch to a new key: hold without spending an attempt.
            if switching("google_workspace", file_fingerprint(candidate)):
                raise FamilyDeliveryHeld("Workspace key change is switching.")
            raise PermissionError("Installed Workspace credential differs.")
        execution.check()
        if session is not None and message.purpose not in {
            "daily_digest",
            "weekly_digest",
        }:
            # Settle retired helpers before this message commits
            # "submitting": failing to reap one is fatal, and must not leave
            # an unsent message looking possibly sent.
            session.reap()
        prepared = begin_submission(
            message.pk,
            execution.claim,
            private=private,
            public_origin=public_origin,
            configuration_id=configuration_id,
        )
        if prepared is None:
            _finish_no_send(execution)
            return
        mail, deadline, _, attempt = prepared
        submitted = True
        settings = workspace.settings | {
            "sender": mail.sender,
            "reply_to": mail.reply_to,
            "sender_name": sender_name,
        }
        remaining = _launch_budget(deadline)
        release()
        # No helper has started yet, so an already elapsed launch budget is
        # definitive non-acceptance, unlike a lost acknowledgement after IO.
        result = FamilyDeliveryResult(
            FamilyDeliveryStatus.TRANSIENT,
            len(mail.recipients),
            health=ProviderHealth.UNOBSERVED,
        )
        batched = session is not None and message.purpose not in {
            "daily_digest",
            "weekly_digest",
        }
        stats.update(
            {"transport": "batched"}
            if batched
            else {
                "transport": "per_message",
                "helper_seq": next(_ONE_SHOT),
                "helper_index": 1,
            }
        )
        stats["request_ms"] = elapsed_ms(started)
        if remaining > 0:
            launched = True
            options = {
                "seconds": min(30, remaining),
                "check": _inflight_check(execution),
            }
            calling = time.monotonic()
            try:
                if message.purpose == "weekly_digest":
                    result = submit_weekly(candidate, settings, mail, **options)
                elif message.purpose == "daily_digest":
                    result = submit_digest(candidate, settings, mail, **options)
                else:
                    result = submit_family(
                        candidate, settings, mail, session=session, **options
                    )
            finally:
                stats["submit_ms"] = elapsed_ms(calling)
                if batched:
                    stats.update(session.take_stats())
    except ProviderCheckDrainFailure:
        raise
    except FamilyDeliveryHeld:
        _defer_held(execution)
        return
    except Exception:
        if not submitted:
            with work_transaction():
                status = _status(lock_task_claim(execution.claim))
                attempt = preparation_attempts(status)
            if attempt >= MAX_ATTEMPTS:
                # The task id survives the production log formatter; the
                # message id and Family DUID show with debug logging.
                with work_transaction():
                    identity = failure_identity(status.domain_request_id)
                LOG.error(
                    "Family mail message %s (Family DUID %s) preparation failed "
                    "after bounded retries.",
                    identity["message"],
                    identity["family_duid"],
                    extra=log_extra({"task_id": execution.claim.run_id}),
                )
                if _settle_failed_family_test(execution):
                    execution.transition("safe_cancel")
                else:
                    execution.transition("permanent_failure")
            else:
                execution.transition(
                    "retryable_failure", retry_seconds=retry_delay(attempt)
                )
            return
        result = FamilyDeliveryResult(
            FamilyDeliveryStatus.UNKNOWN
            if launched
            else FamilyDeliveryStatus.TRANSIENT,
            len(mail.recipients),
            health=ProviderHealth.UNAVAILABLE,
        )
    stats["total_ms"] = elapsed_ms(started)
    result = _with_stats(result, stats)
    # A kept connection may have been ended during SMTP (a PostgreSQL
    # restart): replace it now, so recording an accepted outcome does not
    # fail and leave the message to recovery as delivery_unknown (#365).
    drop_unusable()
    status = finish_submission(message.pk, execution.claim, result)
    if circuit.observe(result.health):
        if circuit.stopped:
            LOG.critical(
                "Family mail provider refused this configuration; further sending "
                "is stopped until the mail worker restarts."
            )
        else:
            # Only the first pause of an outage is CRITICAL; later pauses, until
            # a healthy result, are WARNING so a long outage alerts once.
            LOG.log(
                logging.WARNING if circuit.repeated else logging.CRITICAL,
                "Family mail provider is unavailable; sending pauses for %d "
                "minutes, then resumes automatically.",
                OUTAGE_RECOVERY_SECONDS // 60,
            )
    # A rate limit answering one message's DATA holds that message only.
    if result.limit in MAILBOX_LIMITS and circuit.hold(
        LIMIT_RETRY_SECONDS[result.limit]
    ):
        # Logged once per hold, not per refused message.
        LOG.critical(
            "Google Workspace refused mail at its %s sending limit; sending "
            "pauses and resumes automatically.",
            result.limit,
        )
    if status.state.value == "retry_wait":
        if result.limit is not None or result.health is ProviderHealth.UNAVAILABLE:
            # A limit or outage deferral is an admission hold, not a failed
            # attempt: in the RECONCILING phase it is not counted by
            # preparation_attempts, so a later crash cannot exhaust the budget
            # early (budget_spent spares it from the message's budget too).
            execution.progress(0, 0, phase=TaskPhase.RECONCILING)
        execution.transition(
            "retryable_failure", retry_seconds=result_retry_seconds(result, attempt)
        )
    else:
        execution.transition(
            "complete" if status.state.value == "delivered" else "permanent_failure"
        )


def _settle_failed_family_test(execution):
    """A chosen-Family test that cannot be prepared is cancelled, never left unsent.

    Its key is an Admin ticket, not a schedule occurrence: no planner, retry or
    Admin resolution would ever settle a pending test, and an unsent Testing
    message blocks Testing cleanup for good. Scheduled mail keeps its ordinary
    failed task, which planning and Admin retry still own.
    """
    with execution.control.lock, work_transaction():
        message = bound_dispatch(_status(lock_task_claim(execution.claim)))
        if message.purpose != "family_test":
            return False
        # Already cancelled (for example by go-live invalidation) is settled.
        if message.state == "cancelled":
            return True
        # The same predicate recovery uses: an uncertain idempotent retry is
        # never cancelled here either; it falls through to the failed task.
        if not definitely_unsent(message):
            return False
        cancel_unsent(message.pk, execution.claim, reason="preparation_failed")
    return True


def _finish_no_send(execution):
    """A pause is an ordinary held retry, not an invented cancellation outcome."""
    with work_transaction():
        current = bound_dispatch(_status(lock_task_claim(execution.claim)))
    if current.state == "cancelled":
        execution.transition("safe_cancel")
    else:
        _defer_held(execution)


def _defer_held(execution, *, seconds=30):
    """Retain an ordinary admission hold without charging the failure budget."""
    execution.progress(0, 0, phase=TaskPhase.RECONCILING)
    execution.transition("retryable_failure", retry_seconds=seconds)
