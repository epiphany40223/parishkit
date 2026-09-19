"""Maintained, provider-free general workers for inactive go-live links.

Restored or purging state remains an independent hold. A source/epoch/config
change discards only this operation's inactive staging; it never rebinds the
original immutable request or silently chooses replacement input versions.
"""

from django.db import OperationalError, connection

from parishkit.stewardship.accounts.cryptography import TokenPublicKeyring
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .activation_cleanup import disposed, owned_cancellation, scrub_batch
from .activation_inputs import TokenPreparationInputs
from .activation_tokens import (
    owned_preparation,
    prepare_batch,
    prepared_generation,
    require_current,
)
from .credential_models import FamilyAccessToken
from .models import CampaignWorkGate
from .work_locks import work_transaction


def _admitted(preparation):
    """Local cancellation can finish after cleanup is cancelled, but not in restore."""
    scope = _scope(preparation.transition.campaign_id)
    return (
        not scope.runtime.restore_review_required
        and scope.campaign.state not in {"purging", "purge_cleanup_failed", "purged"}
        and not CampaignWorkGate.objects.filter(
            state__in=("preparing", "running")
        ).exists()
    )


def _stale(preparation):
    """Only known scope changes request disposal; unexpected errors still propagate."""
    try:
        require_current(preparation)
    except StaleRecordError:
        return True
    return False


def _ready(preparation):
    """Completion requires current inputs and the original exact ready manifest."""
    if _stale(preparation):
        return False
    generation = prepared_generation(preparation)
    return generation is not None and TokenPreparationInputs.retained(
        preparation
    ).covers(generation)


def token_handler(*, public=None, cleanup=False, scheduler=False):
    """Compile one fixed owner; queue payloads contain no flags, callbacks or keys."""
    if type(cleanup) is not bool or type(scheduler) is not bool:
        raise TypeError("Link preparation requires a compiled handler purpose.")
    if not scheduler and not cleanup and not isinstance(public, TokenPublicKeyring):
        raise ValueError("Link preparation requires its public sealing keyring.")
    if (scheduler or cleanup) and public is not None:
        raise ValueError("This metadata/disposal handler does not accept sealing keys.")

    def owned(status):
        """The selected immutable domain, not a message header, determines the scope."""
        return (
            owned_cancellation(status).preparation
            if cleanup
            else owned_preparation(status)
        )

    def recovery(status):
        """Committed manifests/disposal can finish after crashes without reissuing."""
        preparation = owned(status)
        if status.state != "abandoned" or not _admitted(preparation):
            return None
        if cleanup and disposed(preparation):
            return RecoveryPlan("recovery_complete")
        if not cleanup and _ready(preparation):
            return RecoveryPlan("recovery_complete")
        if not cleanup and _stale(preparation) and disposed(preparation):
            return RecoveryPlan("recovery_cancel")
        if status.attempt >= 5:
            return RecoveryPlan("recovery_fail")
        return RecoveryPlan(
            "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
        )

    def admit(action, status):
        """Repeat binding, current gates and terminal proof on every effect."""
        preparation = owned(status)
        if action in {"lease_expired", "recovery_hint"}:
            return True
        if action.startswith("recovery_"):
            plan = recovery(status)
            return plan is not None and plan.action == action
        if not _admitted(preparation):
            return False
        if action == "complete":
            return disposed(preparation) if cleanup else _ready(preparation)
        if action == "safe_cancel":
            return not cleanup and _stale(preparation) and disposed(preparation)
        return action in {
            "hint",
            "claim",
            "effect",
            "heartbeat",
            "progress",
            "retryable_failure",
            "permanent_failure",
        }

    def execute(execution):
        """Batch work and its terminal acknowledgement share current ownership locks."""
        if scheduler:
            raise PermissionError("The scheduler cannot prepare or dispose links.")
        if connection.in_atomic_block or not execution.control.active:
            raise StorageInvariantError("Link preparation needs a maintained worker.")
        try:
            while True:
                with execution.effect():
                    status = _status(lock_task_claim(execution.claim))
                    preparation = owned(status)
                    execution.heartbeat(seconds=60)
                    if cleanup or _stale(preparation):
                        scrub_batch(preparation, execution.claim)
                        if disposed(preparation):
                            execution.transition(
                                "complete" if cleanup else "safe_cancel"
                            )
                            return
                        continue
                    generation = prepare_batch(
                        preparation.pk, execution.claim, public=public
                    )
                    count = FamilyAccessToken.objects.filter(
                        generation=generation, destroyed_at__isnull=True
                    ).count()
                    execution.progress(
                        count, preparation.eligible_count, phase=TaskPhase.PREPARING
                    )
                    if generation.state == "ready":
                        execution.transition("complete")
                        return
        except OperationalError:
            # Permission, invariant and lost-ownership denials are not retries.
            # A database transport/transient failure is retryable only while the
            # exact claim and domain admission are still demonstrably current.
            with execution.effect():
                task = lock_task_claim(execution.claim)
                exhausted = task.attempt >= 5
                execution.transition(
                    "permanent_failure" if exhausted else "retryable_failure",
                    **(
                        {}
                        if exhausted
                        else {
                            "retry_seconds": min(
                                30 * 2 ** max(task.attempt - 1, 0), 600
                            )
                        }
                    ),
                )

    return Handler(
        WorkQueue.GENERAL,
        admit,
        execute,
        recover=recovery,
        scope=work_transaction,
    )
