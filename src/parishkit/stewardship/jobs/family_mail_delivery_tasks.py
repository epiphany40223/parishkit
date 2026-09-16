"""Maintained MAIL-only Family consumer; no network work inside a transaction."""

import logging
from functools import partial
from pathlib import Path
from threading import Event
from uuid import uuid4

from django.db import connection, connections

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.family_delivery_process import submit_family
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .family_mail_dispatch import (
    MAX_ATTEMPTS,
    begin_submission,
    bound_dispatch,
    disposition,
    finish_submission,
    retry_delay,
)
from .ownership import database_now, lock_task_claim
from .queues import WorkQueue
from .storage import _status

LOG = logging.getLogger(__name__)


def recovery_plan(status):
    """A missing receipt never authorizes SMTP retransmission after submission."""
    row = bound_dispatch(status)
    if status.state != "abandoned":
        raise PermissionError("Family recovery requires an abandoned Task.")
    if row.state == "submitting":
        if row.provider_deadline > database_now():
            return None
        from .family_mail_dispatch_recovery import record_abandoned_submission

        record_abandoned_submission(status, actor_id=uuid4())
        return RecoveryPlan("recovery_fail")
    action = {
        "delivered": "recovery_complete",
        "cancelled": "recovery_cancel",
        "delivery_unknown": "recovery_fail",
        "permanent_failure": "recovery_fail",
        "pending": "recovery_retry",
        "retry_wait": "recovery_retry",
    }[row.state]
    return (
        RecoveryPlan(action, retry_seconds=30)
        if action == "recovery_retry"
        else RecoveryPlan(action)
    )


def admit_task(action, status, *, store, halted):
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
            row.state in {"pending", "retry_wait"} and status.attempt >= MAX_ATTEMPTS
        )
    if action == "retryable_failure":
        return row.state in {"pending", "retry_wait"}
    if action == "heartbeat":
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
    if halted.is_set():
        return False
    mail_authority(store)
    return True


def delivery_handler(
    store, *, private=None, public_origin=None, credential_path=None, scheduler=False
):
    """Schedulers own metadata only; mounted private keys stay in the mail worker."""
    if not scheduler and not isinstance(credential_path, Path):
        raise TypeError("Family dispatch requires an installed Workspace path.")
    halted = Event()
    return Handler(
        queue=WorkQueue.MAIL,
        admit=partial(admit_task, store=store, halted=halted),
        recover=recovery_plan,
        execute=_unavailable
        if scheduler
        else partial(
            _execute,
            private=private,
            public_origin=public_origin,
            credential_path=credential_path,
            halted=halted,
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


def _execute(execution, *, private, public_origin, credential_path, halted):
    """Pin credentials, durably begin, then run one bounded private submission."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Family mail requires maintained worker lifetime.")
    submitted = False
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
            metadata_only = terminal is not None or disposition(message) is not None
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
        if terminal is not None:
            execution.transition(terminal)
            return
        if metadata_only:
            begin_submission(
                message.pk,
                execution.claim,
                private=private,
                public_origin=public_origin,
            )
            _finish_no_send(execution)
            return
        candidate = read_private(credential_path)
        if file_fingerprint(candidate) != workspace.credential_fingerprint:
            raise PermissionError("Installed Workspace credential differs.")
        execution.check()
        prepared = begin_submission(
            message.pk, execution.claim, private=private, public_origin=public_origin
        )
        if prepared is None:
            _finish_no_send(execution)
            return
        mail, deadline, applied_id, attempt = prepared
        submitted = True
        # A settings change between initial file check and the final locked
        # rendering is not permission to submit with the old credential context.
        if applied_id != configuration_id:
            result = FamilyDeliveryResult(
                FamilyDeliveryStatus.TRANSIENT, len(mail.recipients)
            )
        else:
            settings = workspace.settings | {
                "sender": mail.sender,
                "reply_to": mail.reply_to,
            }
            with work_transaction():
                remaining = (deadline - database_now()).total_seconds()
            connections.close_all()
            result = FamilyDeliveryResult(
                FamilyDeliveryStatus.UNKNOWN, len(mail.recipients)
            )
            if remaining > 0:
                result = submit_family(
                    candidate,
                    settings,
                    mail,
                    seconds=min(30, remaining),
                    check=lambda: _check(execution),
                )
    except ProviderCheckDrainFailure:
        raise
    except Exception:
        if not submitted:
            with work_transaction():
                attempt = lock_task_claim(execution.claim).attempt
            if attempt >= MAX_ATTEMPTS:
                LOG.error("Family mail preparation failed after bounded retries.")
                execution.transition("permanent_failure")
            else:
                execution.transition(
                    "retryable_failure", retry_seconds=retry_delay(attempt)
                )
            return
        result = FamilyDeliveryResult(
            FamilyDeliveryStatus.UNKNOWN, len(mail.recipients)
        )
    status = finish_submission(message.pk, execution.claim, result)
    if result.status is FamilyDeliveryStatus.SYSTEMIC:
        halted.set()
        LOG.critical("Family mail provider is unavailable; further sending is stopped.")
    if status.state.value == "retry_wait":
        execution.transition("retryable_failure", retry_seconds=retry_delay(attempt))
    else:
        execution.transition(
            "complete" if status.state.value == "delivered" else "permanent_failure"
        )


def _finish_no_send(execution):
    """A pause is an ordinary held retry, not an invented cancellation outcome."""
    with work_transaction():
        current = bound_dispatch(_status(lock_task_claim(execution.claim)))
    if current.state == "cancelled":
        execution.transition("safe_cancel")
    else:
        execution.transition("retryable_failure", retry_seconds=30)
