"""Optional Slack consumer: exact operational intent, bounded attempts, no email."""

from functools import partial
from pathlib import Path

from django.db import connection, connections

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.operational_delivery_process import submit_operational_slack
from parishkit.stewardship.provider_checks import ProviderCheckDrainFailure
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.runtime_background import mail_authority
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .family_mail_delivery_tasks import preparation_attempts
from .family_mail_dispatch import MAX_ATTEMPTS, FamilyDeliveryHeld, retry_delay
from .models import TaskRun
from .operational_models import OperationalNotice
from .operational_slack_storage import (
    TASK_TYPE,
    begin_submission,
    bound_task,
    configured_channel,
    finish_submission,
    latest_attempt,
    recover_submission,
)
from .ownership import database_now, lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .scheduler import SchedulerGuard
from .storage import _status, enqueue


def produce_slack(guard):
    """Allocate one stable notice intent without depending on an email cohort."""
    if not isinstance(guard, SchedulerGuard):
        raise PermissionError("Operational Slack requires its scheduler.")
    guard.check()
    with work_transaction():
        from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

        runtime = SystemConfiguration.objects.first()
        if runtime is None or not configured_channel(runtime):
            return ()
        pending = (
            OperationalNotice.objects.exclude(
                pk__in=TaskRun.objects.filter(task_type=TASK_TYPE).values(
                    "domain_request_id"
                )
            )
            .order_by("created_at", "pk")
            .values_list("pk", flat=True)[:25]
        )
        return tuple(
            enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                idempotency_key=identifier,
                actor_id=None,
                correlation_id=identifier,
                admit=_admit_enqueue,
            ).run_id
            for identifier in pending
        )


def _admit_enqueue(action, status):
    """Only the closed type and an existing immutable notice can allocate work."""
    return (
        action == "enqueue"
        and status.task_type == TASK_TYPE
        and (OperationalNotice.objects.filter(pk=status.domain_request_id).exists())
    )


def recovery_plan(status):
    """A lost submission becomes unknown; committed outcomes need only Task repair."""
    bound_task(status)
    if status.state != "abandoned":
        raise PermissionError("Operational Slack recovery requires abandonment.")
    attempt = latest_attempt(status)
    if attempt is not None and attempt["observed"] is None:
        if attempt["deadline_at"] > database_now():
            return None
        recover_submission(status)
        return RecoveryPlan("recovery_fail")
    outcome = None if attempt is None else attempt["observed"]
    if outcome == DeliveryOutcome.ACCEPTED:
        return RecoveryPlan("recovery_complete")
    if (
        outcome == DeliveryOutcome.UNKNOWN
        or preparation_attempts(status) >= MAX_ATTEMPTS
    ):
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", 30)


def admit_task(action, status, *, store, available):
    """Preserve outcome drainage/recovery across channel, mode and config changes."""
    bound_task(status)
    if action in {"heartbeat", "progress", "lease_expired"}:
        return True
    if action == "recovery_hint" and status.state == "running":
        return True
    attempt = latest_attempt(status)
    outcome = None if attempt is None else attempt["observed"]
    if action.startswith("recovery_"):
        if attempt is not None and outcome is None:
            return (
                action == "recovery_hint" and attempt["deadline_at"] <= database_now()
            )
        plan = recovery_plan(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action == "complete":
        return outcome == DeliveryOutcome.ACCEPTED
    if action == "permanent_failure":
        return (
            outcome == DeliveryOutcome.UNKNOWN
            or preparation_attempts(status) >= MAX_ATTEMPTS
        )
    if action == "retryable_failure":
        return attempt is None or outcome == DeliveryOutcome.NOT_SENT
    if action not in {"hint", "claim", "effect"}:
        return False
    if outcome in {DeliveryOutcome.ACCEPTED, DeliveryOutcome.UNKNOWN}:
        return True
    if attempt is not None and outcome is None:
        # The exact live owner must drain its committed provider window even
        # after routing changes. begin_submission independently forbids resend.
        return action == "effect"
    try:
        runtime = mail_authority(store)
    except ConfigError:
        if action == "effect":
            raise FamilyDeliveryHeld(
                "Operational Slack configuration requires recovery."
            ) from None
        return False
    if action == "effect" and (not available or not configured_channel(runtime)):
        raise FamilyDeliveryHeld("Operational Slack channel is not configured.")
    return available and configured_channel(runtime)


def slack_handler(store, *, credential_path=None, scheduler=False):
    """General worker alone owns the optional mounted Slack credential."""
    if type(scheduler) is not bool or (
        credential_path is not None and not isinstance(credential_path, Path)
    ):
        raise TypeError("Operational Slack requires a compiled credential path.")

    def unavailable(execution):
        """Metadata-only schedulers cannot invoke the private provider helper."""
        raise PermissionError("The scheduler cannot deliver operational Slack.")

    return Handler(
        WorkQueue.GENERAL,
        partial(
            admit_task, store=store, available=scheduler or credential_path is not None
        ),
        unavailable
        if scheduler
        else partial(_execute, store=store, credential_path=credential_path),
        recover=recovery_plan,
        scope=work_transaction,
    )


def _check(execution):
    """Drain only the started attempt without retaining an idle SQL connection."""
    try:
        execution.check_inflight()
    finally:
        connections.close_all()


def _execute(execution, *, store, credential_path):
    """Pin the credential, commit the attempt, then submit outside SQL transactions."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Operational Slack requires maintained ownership.")
    # A prior configuration hold must not label later real attempts as holds.
    execution.progress(0, 0, phase=TaskPhase.PREPARING)
    submitted = launched = False
    try:
        with execution.effect():
            status = _status(lock_task_claim(execution.claim))
            attempt = latest_attempt(status)
            outcome = None if attempt is None else attempt["observed"]
            if outcome not in {DeliveryOutcome.ACCEPTED, DeliveryOutcome.UNKNOWN}:
                runtime = mail_authority(store)
                channel = AppliedIntegration.objects.filter(
                    configuration_id=runtime.active_configuration_id, kind="slack"
                ).first()
        if outcome in {DeliveryOutcome.ACCEPTED, DeliveryOutcome.UNKNOWN}:
            execution.transition(
                "complete"
                if outcome == DeliveryOutcome.ACCEPTED
                else "permanent_failure"
            )
            return
        if channel is None or credential_path is None:
            raise FamilyDeliveryHeld("Operational Slack channel is not configured.")
        candidate = read_private(credential_path)
        if file_fingerprint(candidate) != channel.credential_fingerprint:
            raise PermissionError("Installed Slack credential differs.")
        execution.check()
        identifier, deadline, notification = begin_submission(
            execution.claim,
            store=store,
            configuration_id=runtime.active_configuration_id,
            fingerprint=channel.credential_fingerprint,
        )
        submitted = True
        with work_transaction():
            remaining = (deadline - database_now()).total_seconds() - 5
        connections.close_all()
        outcome = DeliveryOutcome.NOT_SENT
        if remaining > 0:
            launched = True
            outcome = submit_operational_slack(
                candidate,
                notification,
                seconds=min(30, remaining),
                check=lambda: _check(execution),
            )
    except ProviderCheckDrainFailure:
        raise
    except (ConfigError, FamilyDeliveryHeld):
        if submitted:
            outcome = DeliveryOutcome.UNKNOWN if launched else DeliveryOutcome.NOT_SENT
        else:
            execution.progress(0, 0, phase=TaskPhase.RECONCILING)
            execution.transition("retryable_failure", retry_seconds=30)
            return
    except Exception:
        if submitted:
            outcome = DeliveryOutcome.UNKNOWN if launched else DeliveryOutcome.NOT_SENT
        else:
            with work_transaction():
                attempts = preparation_attempts(
                    _status(lock_task_claim(execution.claim))
                )
            execution.transition(
                "permanent_failure"
                if attempts >= MAX_ATTEMPTS
                else "retryable_failure",
                **(
                    {}
                    if attempts >= MAX_ATTEMPTS
                    else {"retry_seconds": retry_delay(attempts)}
                ),
            )
            return
    finish_submission(identifier, execution.claim, outcome)
    with work_transaction():
        attempts = preparation_attempts(_status(lock_task_claim(execution.claim)))
    if outcome == DeliveryOutcome.NOT_SENT and attempts < MAX_ATTEMPTS:
        execution.transition("retryable_failure", retry_seconds=retry_delay(attempts))
    else:
        execution.transition(
            "complete" if outcome == DeliveryOutcome.ACCEPTED else "permanent_failure"
        )
