"""Bounded notice-to-outbox preparation with immutable per-Admin identities."""

from functools import partial
from uuid import UUID, uuid5

from django.db import connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import (
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.runtime_background import matching_authority
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .family_mail_delivery_tasks import preparation_attempts
from .family_mail_dispatch import FamilyDeliveryHeld
from .models import TaskRun
from .operational_models import (
    OperationalCohort,
    OperationalNotice,
    OperationalRecipient,
)
from .operational_routing import current_routing, mail_render, notice_alert
from .outbox_storage import create_message
from .outbox_validation import DeliveryIdentity
from .ownership import lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .scheduler import SchedulerGuard
from .storage import _status, enqueue

TASK_TYPE = "operational_prepare"
NAMESPACE = UUID("fed3b97f-3692-4672-a7d1-f559a0fa71b1")
BATCH_SIZE = 25


def _configured():
    """Before initial setup supplies routing, retain notices without failed jobs."""
    version = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    return (
        version is not None
        and Parish.objects.filter(configuration_id=version).exists()
        and AddressRule.objects.filter(
            configuration_id=version, roles__contains=["administrator"]
        ).exists()
        and AppliedIntegration.objects.filter(
            configuration_id=version, kind="email"
        ).exists()
    )


def produce_fanout(guard):
    """Allocate only bounded opaque work; no recipient body or provider is loaded."""
    if not isinstance(guard, SchedulerGuard):
        raise PermissionError("Operational preparation requires the owned scheduler.")
    guard.check()
    with work_transaction():
        if not _configured():
            return ()
        pending = (
            OperationalNotice.objects.exclude(
                pk__in=TaskRun.objects.filter(task_type=TASK_TYPE).values(
                    "domain_request_id"
                )
            )
            .order_by("created_at", "pk")
            .values_list("pk", flat=True)[:BATCH_SIZE]
        )
        return tuple(
            enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                idempotency_key=uuid5(NAMESPACE, str(identifier)),
                actor_id=None,
                correlation_id=identifier,
                admit=admit_fanout,
            ).run_id
            for identifier in pending
        )


def _current(status):
    """Match every live Task identity before using persisted progress or ownership."""
    require_work_order()
    if status.task_type != TASK_TYPE:
        raise PermissionError("Operational preparation task type differs.")
    return TaskRun.objects.get(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=TASK_TYPE,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
        worker_id=status.worker_id,
    )


def _completed(row):
    """Every frozen address must have its atomic intent, not just a progress count."""
    cohort = (
        OperationalCohort.objects.only("id", "recipient_count")
        .filter(notice_id=row.domain_request_id, run__root_id=row.root_id)
        .first()
    )
    return (
        cohort is not None
        and row.phase == TaskPhase.VERIFYING.value
        and row.progress_current == row.progress_total == cohort.recipient_count
        and OperationalRecipient.objects.filter(cohort=cohort).count()
        == cohort.recipient_count
    )


def recovery_fanout(status):
    """Retry only preparation; no preparation receipt ever means provider success."""
    row = _current(status)
    if row.state != "abandoned":
        return None
    if _completed(row):
        return RecoveryPlan("recovery_complete")
    return (
        RecoveryPlan("recovery_fail")
        if preparation_attempts(status) >= 5
        else RecoveryPlan("recovery_retry", 30)
    )


def admit_fanout(action, status, *, store=None):
    """Campaign holds cannot suppress alerts, but Task and input identity still bind."""
    require_work_order()
    if status.task_type != TASK_TYPE or not isinstance(status.domain_request_id, UUID):
        return False
    if action == "enqueue":
        return (
            _configured()
            and OperationalNotice.objects.filter(pk=status.domain_request_id).exists()
        )
    row = _current(status)
    if action in {"lease_expired", "heartbeat", "progress"}:
        return True
    if action == "complete":
        return _completed(row)
    if action == "retryable_failure":
        return row.phase == TaskPhase.RECONCILING.value
    if action == "recovery_hint" and row.state == "running":
        return True
    if action.startswith("recovery_"):
        plan = recovery_fanout(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action not in {"hint", "claim", "effect"}:
        return False
    if store is None:
        return False
    # A recovery mismatch is an admission hold, not five consumed attempts.
    # Running owners convert a mid-page mismatch to a journaled held retry.
    try:
        matching_authority(store)
    except ConfigError:
        if action == "effect":
            raise FamilyDeliveryHeld(
                "Operational configuration requires recovery."
            ) from None
        return False
    configured = _configured()
    if action == "effect" and not configured:
        raise FamilyDeliveryHeld("Operational routing is not configured yet.")
    return configured


def _capture(claim, store):
    """Capture once; later pages preserve the cohort despite configuration changes."""
    row = _current(_status(lock_task_claim(claim)))
    cohort = OperationalCohort.objects.filter(notice_id=row.domain_request_id).first()
    if cohort is not None:
        if not TaskRun.objects.filter(pk=cohort.run_id, root_id=row.root_id).exists():
            raise PermissionError("Operational cohort belongs to a different Task.")
        return cohort
    route = current_routing(store)
    if route.sender is None or route.reply_to is None or not route.admins:
        raise FamilyDeliveryHeld("Operational routing is not configured yet.")
    return OperationalCohort.objects.create(
        notice_id=row.domain_request_id,
        configuration_id=route.configuration_id,
        parish=Parish.objects.get(configuration_id=route.configuration_id),
        mode=route.mode.value,
        addresses=list(route.admins),
        recipient_count=len(route.admins),
        slack_channel=route.slack_channel,
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )


def prepare_page(claim, store):
    """Commit at most one page of outbox/task/render/recipient bindings together."""
    require_work_order()
    with correlation(claim.run_id):
        cohort = _capture(claim, store)
        existing = set(
            OperationalRecipient.objects.filter(cohort=cohort).values_list(
                "address", flat=True
            )
        )
        pending = [address for address in cohort.addresses if address not in existing]
        email = AppliedIntegration.objects.get(
            configuration_id=cohort.configuration_id, kind="email"
        )
        alert = notice_alert(cohort.notice_id, SystemMode(cohort.mode))
        for address in pending[:BATCH_SIZE]:
            identifier = uuid5(cohort.pk, address)
            identity = DeliveryIdentity(
                scope_id=cohort.parish_id,
                semantic_key=identifier,
                mode=cohort.mode,
                routing="operational",
                purpose="operational",
            )
            _, render = mail_render(
                configuration_id=cohort.configuration_id,
                sender=email.settings["sender"],
                reply_to=email.settings["reply_to"],
                address=address,
                semantic_key=identifier,
                alert=alert,
            )

            def admit(action, candidate, status, *, expected=identity):
                """Only this current preparation claim may allocate its exact child."""
                _current(_status(lock_task_claim(claim)))
                return action in {"create", "create_task"} and candidate == expected

            message = create_message(
                identity=identity,
                render=render,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
                command_id=identifier,
                admit=admit,
            )
            OperationalRecipient.objects.create(
                id=identifier,
                cohort=cohort,
                address=address,
                outbox_id=message.message_id,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
            )
        return len(existing) + min(len(pending), BATCH_SIZE), cohort.recipient_count


def _execute(execution, *, store):
    """Maintain and checkpoint a bounded page; no transaction contains provider IO."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Operational preparation requires maintained ownership."
        )
    execution.progress(0, 0, phase=TaskPhase.PREPARING)
    while True:
        execution.check()
        try:
            with execution.effect():
                count, total = prepare_page(execution.claim, store)
                execution.progress(count, total, phase=TaskPhase.VERIFYING)
                if count == total:
                    execution.transition("complete")
                    return
        except (ConfigError, FamilyDeliveryHeld):
            execution.progress(0, 0, phase=TaskPhase.RECONCILING)
            execution.transition("retryable_failure", retry_seconds=30)
            return


def fanout_handler(store, *, scheduler=False):
    """Register isolated preparation; metadata schedulers cannot execute a page."""
    if type(scheduler) is not bool:
        raise TypeError("Operational preparation requires a compiled role.")

    def unavailable(execution):
        """A scheduler has no recipient capture or content preparation authority."""
        raise PermissionError("The scheduler cannot prepare operational mail.")

    return Handler(
        WorkQueue.GENERAL,
        partial(admit_fanout, store=store),
        unavailable if scheduler else partial(_execute, store=store),
        recover=recovery_fanout,
        scope=work_transaction,
    )
