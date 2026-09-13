"""Durable, bounded cleanup of expired unselected logo bundles.

The scheduler owns only metadata. A general worker owns the private media mount
and checks its Task fence at both durable checkpoints. Retained configurations
(including prepared versions) pin assets, except for never-applied setup versions
whose journaled abort has completed. A pending request pins only the bundles its
patch explicitly selects; inherited references are already pinned by its base.
"""

from pathlib import Path

from django.db import connection
from django.db.models import BooleanField, Exists, Func, OuterRef, Q

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import TaskStatus, enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .branding_models import BrandingAsset, BrandingBundle
from .branding_staging import cleanup_branding
from .configuration_models import Parish
from .runtime_models import SystemConfiguration
from .setup_models import SetupAttempt

TASK_TYPE = "branding_cleanup"


def unpinned_bundles():
    """Exclude any historical/prepared reference before paging, avoiding starvation."""
    from .setup_cleanup import aborted_setup_candidates

    parishes = Parish.objects.exclude(configuration_id__in=aborted_setup_candidates())
    pinned = BrandingAsset.objects.filter(
        Q(pk__in=parishes.values("large_logo_id"))
        | Q(pk__in=parishes.values("menu_logo_id"))
        | Q(pk__in=parishes.values("icon_logo_id"))
        | Q(pk__in=parishes.values("favicon_id"))
    ).values("bundle_id")
    return (
        BrandingBundle.objects.exclude(pk__in=pinned)
        .alias(
            pending_reference=Func(
                "pk",
                function="public.stewardship_branding_pending_v1",
                output_field=BooleanField(),
            )
        )
        .filter(pending_reference=False)
    )


def available():
    """A restore hold prevents all deletion; individual request pins are scoped."""
    require_work_order()
    return SystemConfiguration.objects.filter(restore_review_required=False).exists()


def eligible(row):
    """Original expiry/cancel evidence is immutable; no login can adopt old staging."""
    if row.state in {"cleanup_pending", "scrubbed"}:
        return True
    return row.expires_at <= database_now() or (
        row.setup_attempt_id is not None
        and SetupAttempt.objects.filter(
            pk=row.setup_attempt_id, state="expired"
        ).exists()
    )


def _bundle(status, *, creating=False):
    """Bind a task to one real bundle, not an arbitrary filesystem name or argument."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("This task does not own branding cleanup.")
    if (
        not creating
        and not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=status.domain_request_id,
            version=status.version,
            fence=status.fence,
            state=status.state,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Branding cleanup requires the current Task view.")
    row = BrandingBundle.objects.filter(pk=status.domain_request_id).first()
    if row is None:
        raise PermissionError("Branding cleanup binding is unavailable.")
    return row


def admit_cleanup(action, status):
    """Completion requires its durable scrub receipt; work requires current gates."""
    row = _bundle(status, creating=action == "enqueue")
    if action in {
        "lease_expired",
        "recovery_hint",
        "retryable_failure",
        "permanent_failure",
        "explicit_retry_replay",
    }:
        # A failure acknowledges this exact claim; it performs no new file IO
        # and remains recordable after an unrelated configuration hold arrives.
        return True
    if action in {"complete", "recovery_complete"}:
        return row.state == "scrubbed"
    if action in {"recovery_retry", "recovery_fail"}:
        plan = recover_cleanup(status)
        return plan is not None and plan.action == action
    if action not in {
        "enqueue",
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
    }:
        return False
    # A completed filesystem checkpoint can be acknowledged after other gates
    # change. It authorizes no new file mutation.
    if row.state == "scrubbed":
        return action in {"enqueue", "hint", "claim", "heartbeat", "progress"}
    return (
        available() and eligible(row) and unpinned_bundles().filter(pk=row.pk).exists()
    )


def recover_cleanup(status):
    """Local removal is idempotent and cross-process locked; retry only five times."""
    row = _bundle(status)
    if status.state != "abandoned":
        raise PermissionError("Cleanup recovery requires abandoned work.")
    if row.state == "scrubbed":
        return RecoveryPlan("recovery_complete")
    if (
        not available()
        or not eligible(row)
        or not unpinned_bundles().filter(pk=row.pk).exists()
    ):
        return None
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def cleanup_handler(media_root=None):
    """A scheduler omits the media dependency and cannot execute this handler."""
    if media_root is not None and (
        not isinstance(media_root, Path) or not media_root.is_absolute()
    ):
        raise ValueError("Cleanup requires an admitted absolute media root.")

    def execute(execution):
        """Remove at most four files, outside SQL but inside filesystem exclusion."""
        if media_root is None:
            raise PermissionError("The scheduler cannot remove branding files.")
        execution.check()
        task = TaskRun.objects.get(pk=execution.claim.run_id)
        row = BrandingBundle.objects.get(pk=task.domain_request_id)
        if row.state == "scrubbed":
            execution.transition("complete")
            return

        def admitted(row):
            """Execution.effect already rechecks the claim, YAML and domain gates."""
            return row.pk == task.domain_request_id and eligible(row)

        try:
            cleanup_branding(
                media_root,
                row.pk,
                admit=admitted,
                actor_id=execution.claim.worker_id,
                effect=execution.effect,
            )
        except PermissionError:
            raise
        except (ConfigError, OSError):
            # Never serialize paths or filesystem diagnostics into task metadata.
            # A crash/SQL failure stays abandoned for checkpoint-based recovery.
            if task.attempt >= 5:
                execution.transition("permanent_failure")
            else:
                execution.transition("retryable_failure", retry_seconds=60)
            return
        execution.transition("complete")

    return Handler(
        WorkQueue.GENERAL,
        admit_cleanup,
        execute,
        recover=recover_cleanup,
        scope=work_transaction,
    )


def produce_cleanup(guard, *, limit=20):
    """One durable root per bundle; terminal failure never spawns a new retry loop."""
    if (
        not isinstance(guard, SchedulerGuard)
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise TypeError("Cleanup production requires owned bounded scheduler inputs.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Cleanup production owns its short transaction.")
    guard.check()
    with work_transaction():
        if not available():
            return ()
        expired_setup = SetupAttempt.objects.filter(state="expired").values("pk")
        existing = TaskRun.objects.filter(
            task_type=TASK_TYPE, domain_request_id=OuterRef("pk")
        )
        rows = (
            unpinned_bundles()
            .exclude(state="scrubbed")
            .filter(
                Q(state="cleanup_pending")
                | Q(expires_at__lte=database_now())
                | Q(setup_attempt_id__in=expired_setup)
            )
            .filter(~Exists(existing))
            .order_by("expires_at", "pk")[:limit]
        )
        result = []
        for row in rows:
            guard.check()
            result.append(
                enqueue(
                    task_type=TASK_TYPE,
                    domain_request_id=row.pk,
                    actor_id=None,
                    correlation_id=row.pk,
                    admit=admit_cleanup,
                    idempotency_key=row.pk,
                )
            )
        guard.check()
        return tuple(result)
