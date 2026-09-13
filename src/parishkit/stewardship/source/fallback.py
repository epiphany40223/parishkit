"""Durable full-refresh dependencies for incomplete or unseeded delta requests."""

from uuid import UUID, uuid5

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.jobs.dispatch import Execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .refresh_models import (
    SourceRefreshAttempt,
    SourceRefreshFallback,
    SourceRefreshRequest,
)
from .requests import admit_refresh_request, request_refresh
from .snapshot_models import SourceCurrent


def request_full_fallback(execution, *, attempt_id=None):
    """Record one rejected-delta dependency, using normal durable full coalescing.

    A missing baseline is detected before observation and needs no fake attempt.
    Otherwise the exact current claim must already have rejected its incomplete
    delta. Creating a dependency is not completing either Task or advancing the
    watermark. Replays retain the original dependency and creator provenance.
    """
    if not isinstance(execution, Execution) or (
        attempt_id is not None and not isinstance(attempt_id, UUID)
    ):
        raise TypeError("Source fallback requires its execution and optional attempt.")
    with execution.effect():
        task = TaskRun.objects.get(pk=execution.claim.run_id)
        admit_refresh_request("effect", _status(task))
        request = SourceRefreshRequest.objects.get(pk=task.domain_request_id)
        if request.kind != "delta":
            raise PermissionError("Only a delta can require full fallback.")
        reason = "no_base" if attempt_id is None else "incomplete_delta"
        previous = SourceRefreshFallback.objects.filter(request=request).first()
        if previous is not None:
            if (
                previous.task_id,
                previous.task_fence,
                previous.attempt_id,
                previous.reason,
            ) != (
                task.pk,
                execution.claim.fence,
                attempt_id,
                reason,
            ):
                raise StorageInvariantError("Source fallback is already bound.")
            return previous
        if attempt_id is None:
            if SourceCurrent.objects.get(singleton=True).snapshot_id is not None:
                raise PermissionError(
                    "No-base fallback requires absent current source."
                )
        elif not SourceRefreshAttempt.objects.filter(
            pk=attempt_id,
            request=request,
            task=task,
            task_fence=execution.claim.fence,
            snapshot__state="rejected",
            snapshot__kind="delta",
        ).exists():
            raise PermissionError("Fallback requires its rejected delta attempt.")

        def authorize(scope):
            """The full producer must still see the original current scope."""
            return admit_refresh_request("effect", _status(task))

        command = request_refresh(
            command_id=uuid5(request.pk, "source-full-fallback-command-v1"),
            cause="fallback",
            actor_id=None,
            correlation_id=execution.correlation_id,
            authorize=authorize,
        )
        record = SourceRefreshFallback.objects.create(
            id=uuid5(request.pk, "source-full-fallback-v1"),
            request=request,
            command_id=command.command_id,
            task=task,
            task_fence=execution.claim.fence,
            attempt_id=attempt_id,
            reason=reason,
            actor_id=execution.claim.worker_id,
            correlation_id=execution.correlation_id,
        )
        record_action(
            Action.SOURCE_FALLBACK,
            actor_kind=ActorKind.SYSTEM,
            actor_id=execution.claim.worker_id,
            subject_id=record.pk,
            context={
                "source_fingerprint": request.window_digest,
                "outcome": Outcome.STARTED,
            },
        )
        return record
