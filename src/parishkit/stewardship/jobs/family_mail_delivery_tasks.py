"""Maintained MAIL-only Family consumer; no network work inside a transaction."""

import logging
from functools import partial
from pathlib import Path
from random import randint
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from django.db import connection, connections

from parishkit.config import ConfigError
from parishkit.logging import log_extra
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
)
from parishkit.stewardship.family_delivery_process import (
    submit_digest,
    submit_family,
    submit_weekly,
)
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.sender_name import configured_sender_name
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .family_mail_dispatch import (
    CAPPED_RETRY_SECONDS,
    LIMIT_RETRY_SECONDS,
    MAILBOX_LIMITS,
    MAX_ATTEMPTS,
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
from .models import TaskRunEvent
from .ownership import database_now, lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .storage import _status

LOG = logging.getLogger(__name__)
# How soon an abandoned, still-unsent delivery may be claimed again.
RECOVERY_RETRY_SECONDS = 30
# How long Family mail pauses after a shared outage before one probe is sent.
OUTAGE_RECOVERY_SECONDS = 600


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
    """

    def __init__(self, *, recovery_seconds=None, systemic_stops=False):
        """Keep circuit state shared by all dispatches using this handler."""
        if recovery_seconds is not None and (
            type(recovery_seconds) is not int or not 60 <= recovery_seconds <= 3600
        ):
            raise ValueError("Circuit recovery requires a bounded cooldown.")
        self.recovery_seconds = recovery_seconds
        self.systemic_stops = systemic_stops
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
        """The last-24-hour recipient count, re-read at most every 15 seconds.

        Every queued message consults it, so a fresh count per message would
        repeat the same query thousands of times during a bulk send.
        """
        now = monotonic()
        if self.counted_at is None or now - self.counted_at >= 15:
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
                    self.stopped = True
                return newly_halted
        return False


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
        return False
    try:
        mail_authority(store)
    except ConfigError:
        if action == "effect":
            raise FamilyDeliveryHeld("Mail configuration requires recovery.") from None
        return False
    return True


def delivery_handler(
    store, *, private=None, public_origin=None, credential_path=None, scheduler=False
):
    """Schedulers own metadata only; mounted private keys stay in the mail worker."""
    if not scheduler and not isinstance(credential_path, Path):
        raise TypeError("Family dispatch requires an installed Workspace path.")
    circuit = DeliveryCircuit(
        recovery_seconds=OUTAGE_RECOVERY_SECONDS, systemic_stops=True
    )
    return Handler(
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
        ),
        scope=work_transaction,
    )


def _unavailable(execution):
    """A scheduler registration never grants private key or provider access."""
    raise PermissionError("Schedulers cannot submit Family mail.")


def _check(execution):
    """Continue draining under Task ownership, without new-send admission."""
    try:
        execution.check_inflight()
    finally:
        connections.close_all()


def _execute(execution, *, private, public_origin, credential_path, circuit):
    """Pin credentials, durably begin, then run one bounded private submission."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Family mail requires maintained worker lifetime.")
    execution.progress(0, 0, phase=TaskPhase.PREPARING)
    submitted = False
    launched = False
    message = None
    try:
        with execution.effect():
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
                sent = circuit.daily_sends()
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
            raise PermissionError("Installed Workspace credential differs.")
        execution.check()
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
        with work_transaction():
            remaining = (deadline - database_now()).total_seconds()
        connections.close_all()
        # No helper has started yet, so an already elapsed launch budget is
        # definitive non-acceptance, unlike a lost acknowledgement after IO.
        result = FamilyDeliveryResult(
            FamilyDeliveryStatus.TRANSIENT,
            len(mail.recipients),
            health=ProviderHealth.UNOBSERVED,
        )
        if remaining > 0:
            launched = True
            submit = (
                submit_weekly
                if message.purpose == "weekly_digest"
                else submit_digest
                if message.purpose == "daily_digest"
                else submit_family
            )
            result = submit(
                candidate,
                settings,
                mail,
                seconds=min(30, remaining),
                check=lambda: _check(execution),
            )
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
