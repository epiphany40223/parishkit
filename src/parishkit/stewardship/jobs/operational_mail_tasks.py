"""Isolated operational MAIL execution with bounded retries and no alert recursion."""

import logging
from functools import partial
from pathlib import Path

from django.db import connection, connections

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
    ProviderHealth,
)
from parishkit.stewardship.operational_delivery_process import submit_operational_mail
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .family_mail_delivery_tasks import DeliveryCircuit, preparation_attempts
from .family_mail_dispatch import MAX_ATTEMPTS, FamilyDeliveryHeld, retry_delay
from .operational_dispatch import (
    begin_submission,
    bound_operational,
    cancel_unsent,
    complete_cohort,
    finish_submission,
    recipient_current,
    recover_submission,
)
from .ownership import database_now, lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .storage import _status

LOG = logging.getLogger(__name__)


def recovery_plan(status):
    """Only definitive unsent work may retry; abandoned submissions stay uncertain."""
    message = bound_operational(status)
    if status.state != "abandoned":
        raise PermissionError("Operational recovery requires abandonment.")
    if message.state == "submitting":
        if message.provider_deadline > database_now():
            return None
        recover_submission(status)
        return RecoveryPlan("recovery_fail")
    action = {
        "delivered": "recovery_complete",
        "cancelled": "recovery_cancel",
        "delivery_unknown": "recovery_fail",
        "permanent_failure": "recovery_fail",
        "pending": "recovery_retry",
        "retry_wait": "recovery_retry",
    }[message.state]
    if action == "recovery_retry" and preparation_attempts(status) >= MAX_ATTEMPTS:
        action = "recovery_fail"
    return (
        RecoveryPlan(action, 30) if action == "recovery_retry" else RecoveryPlan(action)
    )


def admit_task(action, status, *, store, circuit):
    """Preserve drainage even after configuration changes or recipient revocation."""
    message = bound_operational(status)
    if action in {"heartbeat", "progress", "lease_expired"}:
        return True
    if action == "recovery_hint" and status.state == "running":
        return True
    if action.startswith("recovery_"):
        if message.state == "submitting":
            return (
                action == "recovery_hint"
                and message.provider_deadline <= database_now()
            )
        plan = recovery_plan(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action == "complete":
        return message.state == "delivered"
    if action == "safe_cancel":
        return message.state == "cancelled"
    if action == "permanent_failure":
        return message.state in {"permanent_failure", "delivery_unknown"} or (
            message.state in {"pending", "retry_wait"}
            and preparation_attempts(status) >= MAX_ATTEMPTS
        )
    if action == "retryable_failure":
        return message.state in {"pending", "retry_wait"}
    if message.state not in {"pending", "retry_wait"}:
        return action == "effect" or (
            message.state != "submitting" and action in {"hint", "claim"}
        )
    if action not in {"hint", "claim", "effect"} or not complete_cohort(message):
        return False
    if circuit.blocks_new_send():
        return False
    mail_authority(store)
    return True


def delivery_handler(store, *, credential_path=None, scheduler=False):
    """Only MAIL owns a mounted credential; schedulers receive metadata callbacks."""
    if type(scheduler) is not bool or (
        not scheduler and not isinstance(credential_path, Path)
    ):
        raise TypeError("Operational mail requires its compiled service profile.")
    circuit = DeliveryCircuit()

    def unavailable(execution):
        """No scheduler callback can be used to open a private provider pipe."""
        raise PermissionError("Schedulers cannot submit operational mail.")

    return Handler(
        WorkQueue.MAIL,
        partial(admit_task, store=store, circuit=circuit),
        unavailable
        if scheduler
        else partial(
            _execute, store=store, credential_path=credential_path, circuit=circuit
        ),
        recover=recovery_plan,
        scope=work_transaction,
    )


def _check(execution):
    """Drain one already-started attempt while retaining no idle database socket."""
    try:
        execution.check_inflight()
    finally:
        connections.close_all()


def _execute(execution, *, store, credential_path, circuit):
    """Pin credentials, commit the attempt, submit privately, and retain certainty."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Operational mail requires maintained ownership.")
    submitted = launched = False
    try:
        with execution.effect():
            message = bound_operational(_status(lock_task_claim(execution.claim)))
            if message.state in {"pending", "retry_wait"} and not recipient_current(
                message
            ):
                cancel_unsent(message, execution.claim)
                message.refresh_from_db()
            terminal = {
                "delivered": "complete",
                "cancelled": "safe_cancel",
                "permanent_failure": "permanent_failure",
                "delivery_unknown": "permanent_failure",
            }.get(message.state)
            if terminal is None:
                runtime = mail_authority(store)
                configuration_id = runtime.active_configuration_id
                workspace = AppliedIntegration.objects.get(
                    configuration_id=configuration_id, kind="google_workspace"
                )
        if terminal is not None:
            execution.transition(terminal)
            return
        candidate = read_private(credential_path)
        if file_fingerprint(candidate) != workspace.credential_fingerprint:
            raise PermissionError("Installed Workspace credential differs.")
        execution.check()
        prepared = begin_submission(
            message.pk, execution.claim, store=store, configuration_id=configuration_id
        )
        if prepared is None:
            execution.transition("safe_cancel")
            return
        mail, deadline, attempt = prepared
        submitted = True
        settings = workspace.settings | {
            "sender": mail.sender,
            "reply_to": mail.reply_to,
        }
        with work_transaction():
            remaining = (deadline - database_now()).total_seconds()
        connections.close_all()
        result = FamilyDeliveryResult(
            FamilyDeliveryStatus.TRANSIENT, 1, health=ProviderHealth.UNOBSERVED
        )
        if remaining > 0:
            launched = True
            result = submit_operational_mail(
                candidate,
                settings,
                mail,
                seconds=min(30, remaining),
                check=lambda: _check(execution),
            )
    except ProviderCheckDrainFailure:
        raise
    except FamilyDeliveryHeld:
        execution.progress(0, 0, phase=TaskPhase.RECONCILING)
        execution.transition("retryable_failure", retry_seconds=30)
        return
    except Exception:
        if not submitted:
            with work_transaction():
                attempt = preparation_attempts(
                    _status(lock_task_claim(execution.claim))
                )
            if attempt >= MAX_ATTEMPTS:
                LOG.error("Operational mail preparation exhausted bounded retries.")
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
            1,
            health=ProviderHealth.UNAVAILABLE
            if launched
            else ProviderHealth.UNOBSERVED,
        )
    outcome = finish_submission(message.pk, execution.claim, result)
    if circuit.observe(result.health):
        # Never feed a failed alert email back into the critical-email producer.
        LOG.error("Operational email provider unavailable; further attempts are held.")
    if outcome.state.value == "retry_wait":
        execution.transition("retryable_failure", retry_seconds=retry_delay(attempt))
    else:
        execution.transition(
            "complete" if outcome.state.value == "delivered" else "permanent_failure"
        )
