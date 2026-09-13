"""Original-attempt Task admission for staging, never active source publication."""

from django.db.models import F

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_exchange_models import SetupSourceResult
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_policy import SetupState
from parishkit.stewardship.accounts.setup_staging import _window
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.dispatch import RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import TaskStatus
from parishkit.stewardship.storage import StorageInvariantError

from .errors import SourceScopeChanged
from .models import SourceMutationLease
from .outcomes import MAX_AUTOMATIC_ATTEMPTS, retry_delay

TASK_TYPE = "setup_source_load"


def bound_attempt(status):
    """Repeat durable root/initiator binding even when expiry closes new work."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("The setup source task is unavailable.")
    attempt = (
        SetupAttempt.objects.select_related("source_task")
        .filter(pk=status.domain_request_id, source_task_id=status.root_id)
        .first()
    )
    if (
        attempt is None
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=attempt.pk,
            initiated_by_id=attempt.owner_id,
            version=status.version,
            state=status.state,
            fence=status.fence,
        ).exists()
    ):
        raise PermissionError("The setup source task binding differs.")
    return attempt


def require_live_setup(attempt):
    """Bootstrap policy cannot change, and the original login cannot be replaced."""
    require_work_order()
    now = database_now()
    login = (
        PortalSession.objects.only(
            "id", "principal_id", "revoked_at", "expires_at", "last_activity_at"
        )
        .filter(pk=attempt.session_id, principal_id=attempt.owner_id)
        .first()
    )
    if (
        attempt.state != SetupState.LOADING
        or login is None
        or not PortalUser.objects.filter(pk=attempt.owner_id, disabled=False).exists()
        or _window(attempt, login).expiry(now, session_live=login.revoked_at is None)
        is not None
        or not SystemConfiguration.objects.filter(
            active_configuration_id=attempt.base_id,
            mode="testing",
            restore_review_required=False,
            current_campaign_id=None,
        ).exists()
    ):
        raise SourceScopeChanged("The original setup source attempt has expired.")
    return attempt


def staged_result(status):
    """A Task succeeds only for its own exact-fence, still-valid ready corpus."""
    require_work_order()
    return SetupSourceResult.objects.filter(
        exchange__task_id=status.run_id,
        exchange__task_fence=status.fence,
        exchange__attempt_id=status.domain_request_id,
        exchange__scrubbed_at=None,
        exchange__credential_version=F("exchange__credential__version"),
        exchange__fingerprint=F("exchange__credential__fingerprint"),
        exchange__credential__scrubbed_at=None,
        snapshot__state="ready",
        snapshot__task_id=status.run_id,
        snapshot__source_fence=F("exchange__source_fence"),
    ).first()


def source_available():
    """Neither a held lease nor a retained external read deadline permits overlap."""
    require_work_order()
    row = SourceMutationLease.objects.first()
    if row is None:
        raise StorageInvariantError("Source ownership is not initialized.")
    now = database_now()
    return all(
        value is None or value <= now
        for value in (row.expires_at, row.external_deadline)
    )


def recovery_plan(status):
    """An abandoned read can retry only after drainage and original-login admission."""
    attempt = bound_attempt(status)
    if status.state != "abandoned":
        raise PermissionError("Setup recovery requires an abandoned source task.")
    if not source_available():
        return None
    try:
        require_live_setup(attempt)
    except PermissionError:
        return RecoveryPlan("recovery_cancel")
    if status.attempt >= MAX_AUTOMATIC_ATTEMPTS:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", retry_seconds=retry_delay(status.attempt))


def admit_setup_task(action, status):
    """Closed metadata transitions distinguish new reads from safe failed-work exit."""
    attempt = bound_attempt(status)
    if action == "lease_expired":
        return True
    if action == "recovery_hint" and status.state == "running":
        return True
    if action in {
        "recovery_hint",
        "recovery_retry",
        "recovery_cancel",
        "recovery_fail",
    }:
        plan = recovery_plan(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action in {"retryable_failure", "permanent_failure", "safe_cancel"}:
        # This task must release its own claim, not another task's reservation.
        # Release preserves the external deadline; retry admission still waits.
        lease = SourceMutationLease.objects.get(singleton=True)
        return lease.owner_id != status.run_id
    require_live_setup(attempt)
    if action in {"claim", "hint"}:
        return source_available()
    if action == "complete":
        return staged_result(status) is not None
    return action in {"heartbeat", "progress", "effect"}
