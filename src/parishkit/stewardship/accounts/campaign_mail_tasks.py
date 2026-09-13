"""Installed mail consumer for explicit fictional campaign-readiness tests."""

from functools import partial
from pathlib import Path

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
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.readiness_delivery_process import submit_sample
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_mail import TASK_TYPE, live
from .campaign_mail_delivery import begin_submission, finish_submission
from .campaign_mail_models import CampaignMailTest
from .configuration_models import AppliedIntegration
from .key_files import file_fingerprint, read_private
from .sessions import database_now


def bound_delivery(status):
    """An exact stored task/fence/initiator must own the immutable test root."""
    require_work_order()
    row = CampaignMailTest.objects.filter(pk=status.domain_request_id).first()
    if (
        row is None
        or status.task_type != TASK_TYPE
        or status.root_id != row.task_id
        or status.run_id != row.task_id
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=row.task_id,
            domain_request_id=row.pk,
            task_type=TASK_TYPE,
            initiated_by_id=row.requested_by_id,
            version=status.version,
            state=status.state,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Campaign test Task binding differs.")
    return row


def recovery_plan(status):
    """Recovery completes metadata only; it never retries a possibly submitted test."""
    row = bound_delivery(status)
    if status.state != "abandoned":
        raise PermissionError("Campaign test recovery requires abandonment.")
    if row.state == "submitting":
        return None
    return RecoveryPlan(
        {"accepted": "recovery_complete", "cancelled": "recovery_cancel"}.get(
            row.state, "recovery_fail"
        )
    )


def admit_task(action, status, *, store):
    """Terminal observation/drain survives stale configuration; new sending does not."""
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
        return True
    if row.state == "cancelled":
        return action in {"hint", "claim", "effect", "progress"}
    mail_authority(store)
    if action in {"hint", "claim"}:
        return row.state == "queued" and live(row)
    return (
        action in {"effect", "progress"}
        and row.state in {"queued", "submitting"}
        and live(row)
    )


def campaign_mail_handler(store, *, credential_path=None, scheduler=False):
    """Only a mounted installed Workspace credential enables provider execution."""
    if not scheduler and not isinstance(credential_path, Path):
        raise TypeError("Campaign test delivery requires an installed Workspace path.")
    return Handler(
        queue=WorkQueue.MAIL,
        admit=partial(admit_task, store=store),
        execute=_unavailable
        if scheduler
        else partial(_execute, credential_path=credential_path),
        recover=recovery_plan,
        scope=work_transaction,
    )


def _unavailable(execution):
    """Schedulers have metadata ownership, never provider execution authority."""
    raise PermissionError("The scheduler cannot deliver campaign tests.")


def _check(execution, path, fingerprint):
    """Each finite helper pulse repeats Task/config admission and mounted identity."""
    try:
        execution.check_inflight()
        if file_fingerprint(read_private(path)) != fingerprint:
            raise PermissionError("The installed mail credential changed.")
    finally:
        connections.close_all()


def _execute(execution, *, credential_path):
    """Read the installed key, commit submitting, then invoke the finite helper."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Campaign test requires maintained worker lifetime."
        )
    with execution.effect():
        row = bound_delivery(_status(lock_task_claim(execution.claim)))
        if row.state != "cancelled":
            workspace = AppliedIntegration.objects.get(
                configuration_id=row.configuration_id, kind="google_workspace"
            )
    if row.state == "cancelled":
        execution.transition("safe_cancel")
        return
    submitted = False
    try:
        candidate = read_private(credential_path)
        if file_fingerprint(candidate) != row.fingerprint:
            raise PermissionError("The installed mail credential differs from preview.")
        execution.check()
        mail, deadline = begin_submission(row.pk, execution.claim)
        submitted = True
        settings = workspace.settings | {
            key: getattr(mail, key) for key in ("sender", "reply_to", "recipient")
        }
        with work_transaction():
            remaining = (deadline - database_now()).total_seconds()
        connections.close_all()
        outcome = DeliveryOutcome.UNKNOWN
        if remaining > 0:
            outcome = submit_sample(
                candidate,
                settings,
                mail,
                seconds=min(30, remaining),
                check=lambda: _check(execution, credential_path, row.fingerprint),
            )
    except ProviderCheckDrainFailure:
        # A helper not proven drained leaves the submission checkpoint intact;
        # it cannot be classified as a safely completed worker lifetime.
        raise
    except Exception:
        if not submitted:
            # Cancellation can win the locked begin-submission recheck. Select
            # its disposition and transition under the same claim/work locks,
            # rather than abandoning an already safely cancelled journal.
            with execution.control.lock, work_transaction():
                status = _status(lock_task_claim(execution.claim))
                current = bound_delivery(status)
                execution.transition(
                    "safe_cancel"
                    if current.state == "cancelled"
                    else "permanent_failure"
                )
            return
        outcome = DeliveryOutcome.UNKNOWN
    result = finish_submission(row.pk, execution.claim, outcome)
    execution.transition(
        "complete" if result.state == "accepted" else "permanent_failure"
    )
