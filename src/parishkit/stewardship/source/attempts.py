"""Bind one concrete worker claim to its source request and loaded provider key."""

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.storage import StorageInvariantError

from .credentials import SourceCredential
from .leases import SourceClaim, verify_source
from .refresh_models import SourceRefreshAttempt, SourceRefreshRequest
from .requests import _organization, _window
from .snapshots import begin_snapshot


def _scope(request, credential_fingerprint):
    """Harmless config edits may survive; tenant/window/key changes do not."""
    scope = require_source_refresh(campaign_id=request.campaign_id)
    integration = AppliedIntegration.objects.get(
        configuration_id=scope.runtime.active_configuration_id, kind="parishsoft"
    )
    if (
        _organization(scope) != request.organization_id
        or _window(scope).digest != request.window_digest
        or integration.credential_fingerprint != credential_fingerprint
    ):
        raise PermissionError("The source attempt scope or loaded credential is stale.")
    return scope


def _bindings(execution, claim):
    """No source/Task claim components can be mixed between executions."""
    if (
        not isinstance(execution, Execution)
        or not isinstance(claim, SourceClaim)
        or (claim.task_id, claim.task_fence, claim.worker_id)
        != (execution.claim.run_id, execution.claim.fence, execution.claim.worker_id)
    ):
        raise ValueError("Source attempt requires its exact worker/source claim.")


def begin_refresh_attempt(execution, claim, credential):
    """Create one manifest before network work, atomically bound to its real input.

    Replaying the same claim returns the existing manifest rather than opening
    a second observation. A fresh provider scan needs a new task/source claim,
    not an overwrite of partially observed staging from this attempt.
    """
    _bindings(execution, claim)
    if not isinstance(credential, SourceCredential):
        raise TypeError("Source attempt requires the loaded private credential.")
    with execution.effect():
        verify_source(claim)
        task = TaskRun.objects.get(pk=claim.task_id)
        request = SourceRefreshRequest.objects.get(pk=task.domain_request_id)
        if request.task_root_id != task.root_id or request.kind != claim.phase:
            raise PermissionError("Source attempt does not match its request.")
        scope = _scope(request, credential.fingerprint)
        existing = (
            SourceRefreshAttempt.objects.select_related("snapshot")
            .filter(task_id=task.pk, task_fence=claim.task_fence)
            .first()
        )
        if existing is not None:
            if (
                existing.snapshot.source_fence != claim.fence
                or existing.credential_fingerprint != credential.fingerprint
            ):
                raise StorageInvariantError(
                    "Source attempt is already bound to other inputs."
                )
            return existing
        snapshot = begin_snapshot(
            claim,
            organization_id=request.organization_id,
            admit=lambda *args: _scope(request, credential.fingerprint) is not None,
        )
        return SourceRefreshAttempt.objects.create(
            request=request,
            snapshot=snapshot,
            task=task,
            task_fence=claim.task_fence,
            configuration_id=scope.runtime.active_configuration_id,
            credential_fingerprint=credential.fingerprint,
            actor_id=claim.worker_id,
        )


def verify_refresh_attempt(attempt_id, execution, claim):
    """Recheck live attempt scope before each page/staging/completion boundary."""
    _bindings(execution, claim)
    with execution.effect():
        verify_source(claim)
        attempt = SourceRefreshAttempt.objects.select_related(
            "request", "snapshot"
        ).get(pk=attempt_id)
        if (
            attempt.task_id != claim.task_id
            or attempt.task_fence != claim.task_fence
            or attempt.snapshot.source_fence != claim.fence
        ):
            raise PermissionError("Source attempt belongs to another worker claim.")
        _scope(attempt.request, attempt.credential_fingerprint)
        return attempt
