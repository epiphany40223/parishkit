"""Safely retire stale waiting refresh requests without changing their bindings."""

from uuid import UUID

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _locked, _status, change_run

from .outcomes import (
    admit_refresh_metadata,
    read_attempts_drained,
    scope_fingerprint,
    superseding_digest,
)
from .refresh_models import SourceRefreshRequest
from .requests import TASK_TYPE


def cancel_superseded_refresh(run_id, *, worker_id):
    """Cancel only waiting/abandoned old-window work with drained-read proof.

    The scanner calls this before attempting a stale hint. A live running owner
    is never impersonated; ordinary lease-expiry recovery fences it first.
    Current-window holds, completed observations and unrelated tasks are not
    cancellation evidence. Neither the request nor historical staging is deleted.
    """
    if not isinstance(run_id, UUID) or not isinstance(worker_id, UUID):
        raise TypeError("Source cancellation requires canonical worker/task IDs.")
    original = TaskRun.objects.filter(pk=run_id, task_type=TASK_TYPE).first()
    if original is None:
        return False
    with work_transaction(), _locked(original.correlation_id, root_id=original.root_id):
        row = TaskRun.objects.select_for_update().get(pk=run_id)
        if row.state not in {"queued", "retry_wait", "abandoned"}:
            return False
        status = _status(row)
        digest = superseding_digest(status)
        if digest is None or not read_attempts_drained(status):
            return False
        change_run(
            run_id=row.pk,
            expected_version=row.version,
            action="recovery_cancel" if row.state == "abandoned" else "safe_cancel",
            actor_id=worker_id,
            correlation_id=row.correlation_id,
            admit=admit_refresh_metadata,
        )
        request = SourceRefreshRequest.objects.get(pk=row.domain_request_id)
        record_action(
            Action.SOURCE_SUPERSEDED,
            actor_kind=ActorKind.SYSTEM,
            actor_id=worker_id,
            subject_id=row.pk,
            context={
                "source_fingerprint": scope_fingerprint(
                    request.organization_id, request.window_digest
                ),
                "candidate_fingerprint": digest,
                "outcome": Outcome.CANCELLED,
            },
        )
        return True
