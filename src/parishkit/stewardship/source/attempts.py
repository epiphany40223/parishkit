"""Bind one concrete worker claim to its source request and loaded provider key."""

from contextlib import ExitStack, contextmanager

from django.db import transaction

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.activation_hold import wait_out_activation
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .credentials import SourceCredential
from .errors import SourceCredentialChanged, SourceScopeChanged
from .leases import SourceClaim, verify_source
from .refresh_models import SourceRefreshAttempt, SourceRefreshRequest
from .requests import _organization, _window
from .snapshots import begin_snapshot


def _scope(request, credential_fingerprint, *, lock=True):
    """Harmless config edits may survive; tenant/window/key changes do not.

    ``lock=False`` reads the scope without locks, for a source step.
    """
    scope = require_source_refresh(campaign_id=request.campaign_id, lock=lock)
    fingerprint = (
        AppliedIntegration.objects.filter(
            configuration_id=scope.runtime.active_configuration_id, kind="parishsoft"
        )
        .values_list("credential_fingerprint", flat=True)
        .first()
    )
    if fingerprint is None or fingerprint != credential_fingerprint:
        raise SourceCredentialChanged("The loaded source credential is stale.")
    if (
        _organization(scope) != request.organization_id
        or _window(scope).digest != request.window_digest
    ):
        raise SourceScopeChanged("The source attempt scope is stale.")
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


@contextmanager
def source_step(execution):
    """One short fenced source step outside the global work order (#147).

    A source refresh's fetch admission (one per ParishSoft request) and each
    staging batch used to run as ``execution.effect()``, which joins the
    work-order lock: about 170 holds per full refresh, most of a refresh's
    database time, with every Family form, mail step and installer queued
    behind each. Those steps publish nothing. A fetch admission only
    reserves the source lease for the request; a staging batch writes only
    its own attempt's snapshot membership and immutable payload versions,
    which become source truth only at promotion. Snapshot completion and
    promotion stay ``execution.effect()`` steps under the exclusive lock and
    re-verify the attempt's whole scope there, so a transition that commits
    while staging runs is always seen before anything is published.

    The step keeps every ownership check: the execution's control lock, the
    task root and run locked with the claim's fence (``lock_task_claim``), and
    the handler's ``source_step`` admission. Through the compiled handler that
    admission first matches the selected configuration with the database
    (``runtime_background.matching_authority``, no row locks), so a
    configuration activation in progress is waited out (#429), then reads the
    request's admission without row locks. Callers then
    lock the source lease (``verify_source``) and, for staging, the snapshot,
    so its row locks are always task root, task run, lease, snapshot: the
    order every other holder of those rows uses. It takes no runtime or
    campaign row lock, so it cannot deadlock with a transition that locks
    those rows and task rows in either order. The SQL membership guard still
    refuses staging unless the snapshot is staging and its lease and task
    are live. Like ``execution.effect()``, it nests inside an open step.
    """
    with (
        execution.control.lock,
        wait_out_activation(
            lambda: _admitted_step(execution), task_id=execution.claim.run_id
        ),
    ):
        yield


def _admitted_step(execution):
    """Open the transaction and admit one source step, or undo it.

    Returns the open transaction as an ExitStack for the caller's ``with``,
    as ``Execution._admitted_effect`` does, so a refusal rolls back before it
    propagates and the step can be repeated with nothing held.
    """
    with ExitStack() as stack:
        execution.control.check()
        stack.enter_context(transaction.atomic())
        row = lock_task_claim(execution.claim)
        if execution.handler.admit("source_step", _status(row)) is not True:
            raise PermissionError("This source step is not admitted.")
        return stack.pop_all()


def verify_refresh_attempt(attempt_id, execution, claim, *, step=False):
    """Recheck live attempt scope before each page/staging/completion boundary.

    ``step=True`` checks inside a ``source_step`` (fetch admission and
    staging); otherwise inside ``execution.effect()``, under the work order.
    """
    _bindings(execution, claim)
    with source_step(execution) if step else execution.effect():
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
        _scope(attempt.request, attempt.credential_fingerprint, lock=not step)
        return attempt
