"""Fenced rejection of failed or superseded staging without deleting evidence."""

from uuid import UUID

from django.db import transaction

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action

from .canonical import InvalidSourcePayload
from .leases import verify_source
from .snapshot_models import SourceSnapshot


def reject_snapshot(snapshot_id, claim, *, admit):
    """A live read owner may retire its own or an older drained staging attempt.

    A newer SourceMutationLease fence proves takeover respected the previous
    external deadline. This is not permission to reuse the old payloads or to
    advance a watermark: the old task, fence, cursor and counts remain intact.
    The compiled caller enters its work/task admission scope before this call.
    """
    if not isinstance(snapshot_id, UUID) or not callable(admit):
        raise ValueError("Source rejection requires an identity and owning admission.")
    if claim.phase not in {"full", "delta"}:
        raise InvalidSourcePayload("Only a source refresh owner may reject staging.")
    with transaction.atomic():
        verify_source(claim)
        snapshot = SourceSnapshot.objects.select_for_update().get(pk=snapshot_id)
        if admit("reject", snapshot) is not True:
            raise PermissionError("Source rejection is not admitted.")
        if snapshot.source_fence > claim.fence or (
            snapshot.source_fence == claim.fence
            and (snapshot.task_id != claim.task_id or snapshot.kind != claim.phase)
        ):
            raise InvalidSourcePayload("Source rejection does not own this attempt.")
        if snapshot.state == "rejected":
            return snapshot
        if snapshot.state not in {"staging", "ready"}:
            raise InvalidSourcePayload(
                "Only unpromoted source staging may be rejected."
            )
        before = snapshot.version
        snapshot.state = "rejected"
        snapshot.version += 1
        snapshot.actor_id = claim.worker_id
        snapshot.save()
        record_action(
            Action.SOURCE_REJECTED,
            actor_kind=ActorKind.SYSTEM,
            actor_id=claim.worker_id,
            subject_id=snapshot.pk,
            context={
                "before_version": before,
                "after_version": snapshot.version,
                "outcome": Outcome.CANCELLED,
            },
        )
        verify_source(claim)
        return snapshot
