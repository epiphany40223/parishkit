"""Compiled setup-mail Task ownership; no scheduled Family-mail fulfillment."""

from time import monotonic

from django.db import connection, connections

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.readiness_delivery_process import submit_sample
from parishkit.stewardship.storage import StorageInvariantError

from .sessions import database_now
from .setup_delivery_models import SetupMailDelivery
from .setup_mail import TASK_TYPE, begin_submission, finish_submission, live
from .setup_mail_exchange import publish_recipient, receive_credential
from .setup_mail_handoff import EphemeralMailRecipient, MailCredentialScope
from .setup_secret_models import SetupSealedCredential


def bound_delivery(status):
    """Only a current stored Task view can select its immutable journal binding."""
    require_work_order()
    # The scheduler uses this same admission path but cannot read rendered mail.
    # Only the dedicated consumer's begin_submission owner may load that payload.
    row = (
        SetupMailDelivery.objects.only(
            "id",
            "task_id",
            "state",
            "attempt_id",
            "attempt_version",
            "credential_id",
            "credential_version",
            "fingerprint",
        )
        .filter(pk=status.domain_request_id)
        .first()
    )
    if (
        row is None
        or status.task_type != TASK_TYPE
        or row.task_id != status.root_id
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=row.task_id,
            domain_request_id=row.pk,
            task_type=TASK_TYPE,
            version=status.version,
            state=status.state,
            fence=status.fence,
        ).exists()
    ):
        raise PermissionError("The setup mail Task binding differs.")
    return row


def recovery_plan(status):
    """A lost send is never retried, even when it failed before provider submission."""
    row = bound_delivery(status)
    if status.state != "abandoned":
        raise PermissionError("Setup mail recovery requires an abandoned Task.")
    if row.state == "submitting":
        # The scheduler's journal recovery waits for both deadlines before
        # classifying uncertainty. A queue hint cannot preempt that barrier.
        return None
    if row.state == "accepted":
        return RecoveryPlan("recovery_complete")
    return RecoveryPlan(
        "recovery_cancel" if row.state == "cancelled" else "recovery_fail"
    )


def admit_task(action, status):
    """No caller-selected retry or successful return bypasses durable outcome proof."""
    row = bound_delivery(status)
    if action == "lease_expired" or (
        action == "recovery_hint" and status.state == "running"
    ):
        return True
    if action in {
        "recovery_hint",
        "recovery_complete",
        "recovery_fail",
        "recovery_cancel",
    }:
        plan = recovery_plan(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action == "complete":
        return row.state == "accepted"
    if action == "safe_cancel":
        return row.state == "cancelled"
    if action == "permanent_failure":
        return row.state in {"queued", "not_sent", "delivery_unknown"}
    if action == "heartbeat":
        # The still-owned Task may be draining after journal completion or
        # cancellation. A renewal must not race that verified final transition.
        return True
    if action in {"hint", "claim"}:
        return row.state == "cancelled" or (row.state == "queued" and live(row))
    return action in {"progress", "effect"} and (
        row.state == "cancelled"
        or (row.state in {"queued", "submitting"} and live(row))
    )


def setup_mail_handler(*, scheduler=False):
    """Startup binds the actual mail owner; schedulers can only inspect/reconcile."""

    def unavailable(execution):
        """A metadata producer cannot invoke a provider-capable execution path."""
        raise PermissionError("The scheduler cannot deliver setup mail.")

    return Handler(
        queue=WorkQueue.MAIL,
        admit=admit_task,
        execute=unavailable if scheduler else _execute,
        recover=recovery_plan,
        scope=work_transaction,
    )


def _receive(execution, row):
    """Wait at most two minutes for the actual isolated Workspace target owner."""
    recipient = EphemeralMailRecipient(
        MailCredentialScope(
            row.pk, row.credential_id, row.credential_version, execution.claim
        )
    )
    publish_recipient(recipient.public)
    deadline = monotonic() + 120
    while monotonic() < deadline:
        execution.check()
        with execution.effect():
            current = bound_delivery(_status(lock_task_claim(execution.claim)))
            if current.state != "queued" or not live(current):
                raise PermissionError("The setup mail request is no longer pending.")
            candidate = receive_credential(recipient)
        if candidate is not None:
            return candidate
        connections.close_all()
        execution.control.finished.wait(1)
    raise TimeoutError(
        "The Workspace target did not reply before the setup mail deadline."
    )


def _check(execution):
    """Each helper pulse repeats current SQL ownership and closes its connection."""
    try:
        # This pulse observes a send that already crossed its irreversible
        # checkpoint. A graceful process stop may drain that bounded send;
        # failed renewal, lost SQL ownership or revoked admission still aborts.
        execution.check_inflight()
    finally:
        connections.close_all()


def _execute(execution):
    """One maintained mail claim receives a key and sends at most once."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Setup mail requires a maintained worker lifetime.")
    with execution.effect():
        row = bound_delivery(_status(lock_task_claim(execution.claim)))
        if row.state != "cancelled":
            settings = SetupSealedCredential.objects.values_list(
                "settings", flat=True
            ).get(pk=row.credential_id)
    if row.state == "cancelled":
        execution.transition("safe_cancel")
        return
    submitted = False
    try:
        candidate = _receive(execution, row)
        execution.check()
        # This service rejects outer transactions: provider IO cannot precede
        # the durable one-way submission checkpoint's actual commit.
        mail, deadline = begin_submission(row.pk, execution.claim)
        submitted = True
        with work_transaction():
            remaining = (deadline - database_now()).total_seconds()
        connections.close_all()
        outcome = DeliveryOutcome.UNKNOWN
        if remaining > 0:
            outcome = submit_sample(
                candidate.value,
                settings,
                mail,
                seconds=min(30, remaining),
                check=lambda: _check(execution),
            )
    except Exception:
        if submitted:
            # Even an unexpected exception after the marker is not evidence of
            # no provider effect. The owner keeps uncertainty, never retries.
            outcome = DeliveryOutcome.UNKNOWN
        else:
            with execution.control.lock, work_transaction():
                current = bound_delivery(_status(lock_task_claim(execution.claim)))
                execution.transition(
                    "safe_cancel"
                    if current.state == "cancelled"
                    else "permanent_failure"
                )
            return
    # A failure committing either receipt is left for journal/Task recovery.
    # In particular, never overwrite known acceptance if Task completion fails.
    result = finish_submission(row.pk, execution.claim, outcome)
    execution.transition(
        "complete" if result.state == "accepted" else "permanent_failure"
    )
