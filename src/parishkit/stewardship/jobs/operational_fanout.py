"""Bounded source-to-outbox preparation with immutable per-recipient identities.

One engine serves every Administrator-routed owner: the operational incident
owner, whose cohort is the current Administrators, and the security alert
owner, whose cohort is the recipients the activation trigger recorded. The
owner is compiled in, never chosen from a broker message or a caller.
"""

from functools import partial
from uuid import UUID, uuid5

from django.db import connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
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
from .operational_owner import NAMESPACE, OPERATIONAL, TASK_TYPE  # noqa: F401
from .operational_routing import current_routing, mail_render  # noqa: F401
from .outbox_storage import create_message
from .outbox_validation import DeliveryIdentity
from .ownership import lock_task_claim
from .phases import TaskPhase
from .queues import WorkQueue
from .scheduler import SchedulerGuard
from .storage import _status, enqueue

BATCH_SIZE = 25


def produce_fanout(guard, owner=OPERATIONAL):
    """Allocate only bounded opaque work; no recipient body or provider is loaded."""
    if not isinstance(guard, SchedulerGuard):
        raise PermissionError(
            f"{owner.label} preparation requires the owned scheduler."
        )
    guard.check()
    with work_transaction():
        if not owner.configured():
            return ()
        return tuple(
            enqueue(
                task_type=owner.task_type,
                domain_request_id=identifier,
                idempotency_key=uuid5(owner.namespace, str(identifier)),
                actor_id=None,
                correlation_id=identifier,
                admit=partial(admit_fanout, owner=owner),
            ).run_id
            for identifier in owner.pending_sources(BATCH_SIZE)
        )


def _current(status, owner):
    """Match every live Task identity before using persisted progress or ownership."""
    require_work_order()
    if status.task_type != owner.task_type:
        raise PermissionError(f"{owner.label} preparation task type differs.")
    return TaskRun.objects.get(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=owner.task_type,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
        worker_id=status.worker_id,
    )


def _completed(row, owner):
    """Every frozen address must have its atomic intent, not just a progress count."""
    cohort = (
        owner.cohort_model.objects.only("id", "recipient_count")
        .filter(**{owner.source_field: row.domain_request_id}, run__root_id=row.root_id)
        .first()
    )
    return (
        cohort is not None
        and row.phase == TaskPhase.VERIFYING.value
        and row.progress_current == row.progress_total == cohort.recipient_count
        and owner.recipient_model.objects.filter(cohort=cohort).count()
        == cohort.recipient_count
    )


def recovery_fanout(status, owner=OPERATIONAL):
    """Retry only preparation; no preparation receipt ever means provider success."""
    row = _current(status, owner)
    if row.state != "abandoned":
        return None
    if _completed(row, owner):
        return RecoveryPlan("recovery_complete")
    return (
        RecoveryPlan("recovery_fail")
        if preparation_attempts(status) >= 5
        else RecoveryPlan("recovery_retry", 30)
    )


def admit_fanout(action, status, *, store=None, owner=OPERATIONAL):
    """Campaign holds cannot suppress alerts, but Task and input identity still bind."""
    require_work_order()
    if status.task_type != owner.task_type or not isinstance(
        status.domain_request_id, UUID
    ):
        return False
    if action == "enqueue":
        return owner.configured() and owner.source_exists(status.domain_request_id)
    row = _current(status, owner)
    if action in {"lease_expired", "heartbeat", "progress"}:
        return True
    if action == "complete":
        return _completed(row, owner)
    if action == "retryable_failure":
        return row.phase == TaskPhase.RECONCILING.value
    if action == "recovery_hint" and row.state == "running":
        return True
    if action.startswith("recovery_"):
        plan = recovery_fanout(status, owner)
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
                f"{owner.label} configuration requires recovery."
            ) from None
        return False
    configured = owner.configured()
    if action == "effect" and not configured:
        raise FamilyDeliveryHeld(f"{owner.label} routing is not configured yet.")
    return configured


def _capture(claim, store, owner):
    """Capture once; later pages preserve the cohort despite configuration changes."""
    row = _current(_status(lock_task_claim(claim)), owner)
    cohort = owner.cohort_model.objects.filter(
        **{owner.source_field: row.domain_request_id}
    ).first()
    if cohort is not None:
        if not TaskRun.objects.filter(pk=cohort.run_id, root_id=row.root_id).exists():
            raise PermissionError(f"{owner.label} cohort belongs to a different Task.")
        return cohort
    return owner.capture(row, claim, store)


def prepare_page(claim, store, owner=OPERATIONAL):
    """Commit at most one page of outbox/task/render/recipient bindings together."""
    require_work_order()
    with correlation(claim.run_id):
        cohort = _capture(claim, store, owner)
        existing = set(
            owner.recipient_model.objects.filter(cohort=cohort).values_list(
                "address", flat=True
            )
        )
        pending = [address for address in cohort.addresses if address not in existing]
        email = AppliedIntegration.objects.get(
            configuration_id=cohort.configuration_id, kind="email"
        )
        alert = owner.alert(cohort, cohort.mode)
        for address in pending[:BATCH_SIZE]:
            identifier = uuid5(cohort.pk, address)
            identity = DeliveryIdentity(
                scope_id=cohort.parish_id,
                semantic_key=identifier,
                mode=cohort.mode,
                routing="operational",
                purpose=owner.purpose,
            )
            _, render = owner.mail_render(
                configuration_id=cohort.configuration_id,
                sender=email.settings["sender"],
                reply_to=email.settings["reply_to"],
                address=address,
                semantic_key=identifier,
                alert=alert,
            )

            def admit(action, candidate, status, *, expected=identity):
                """Only this current preparation claim may allocate its exact child."""
                _current(_status(lock_task_claim(claim)), owner)
                return action in {"create", "create_task"} and candidate == expected

            message = create_message(
                identity=identity,
                render=render,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
                command_id=identifier,
                admit=admit,
            )
            owner.recipient_model.objects.create(
                id=identifier,
                cohort=cohort,
                address=address,
                outbox_id=message.message_id,
                actor_id=claim.worker_id,
                correlation_id=claim.run_id,
            )
        return len(existing) + min(len(pending), BATCH_SIZE), cohort.recipient_count


def _execute(execution, *, store, owner):
    """Maintain and checkpoint a bounded page; no transaction contains provider IO."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            f"{owner.label} preparation requires maintained ownership."
        )
    execution.progress(0, 0, phase=TaskPhase.PREPARING)
    while True:
        execution.check()
        try:
            with execution.effect():
                count, total = prepare_page(execution.claim, store, owner)
                execution.progress(count, total, phase=TaskPhase.VERIFYING)
                if count == total:
                    execution.transition("complete")
                    return
        except (ConfigError, FamilyDeliveryHeld):
            execution.progress(0, 0, phase=TaskPhase.RECONCILING)
            execution.transition("retryable_failure", retry_seconds=30)
            return


def fanout_handler(store, *, scheduler=False, owner=OPERATIONAL):
    """Register isolated preparation; metadata schedulers cannot execute a page."""
    if type(scheduler) is not bool:
        raise TypeError(f"{owner.label} preparation requires a compiled role.")

    def unavailable(execution):
        """A scheduler has no recipient capture or content preparation authority."""
        raise PermissionError(
            f"The scheduler cannot prepare {owner.label.lower()} mail."
        )

    return Handler(
        WorkQueue.GENERAL,
        partial(admit_fanout, store=store, owner=owner),
        unavailable if scheduler else partial(_execute, store=store, owner=owner),
        recover=partial(recovery_fanout, owner=owner),
        scope=work_transaction,
    )
