"""Resolve current boundary execution without reviving immutable old outcomes."""

from parishkit.stewardship.storage import StorageInvariantError

from .models import CampaignBoundaryOccurrence
from .work_locks import require_work_order


def current_boundary(*, campaign_id, kind, due_at, actor_id, correlation_id):
    """Reuse the latest revision or allocate its successor under owning locks.

    End-date configuration commits retire and replace future close occurrences
    atomically. This shared path also creates initial due work after an outage.
    The SQL guard independently enforces sequential revisions and rejects an
    unretired predecessor, including callers outside the compiled producer.
    """
    require_work_order()
    previous = (
        CampaignBoundaryOccurrence.objects.filter(campaign_id=campaign_id, kind=kind)
        .order_by("-execution_revision")
        .first()
    )
    if previous is not None:
        if previous.due_at == due_at and previous.reason != "boundary_replaced":
            return previous
        if previous.state == "pending":
            raise StorageInvariantError("Boundary replacement is not committed.")
    return CampaignBoundaryOccurrence.objects.create(
        campaign_id=campaign_id,
        kind=kind,
        due_at=due_at,
        execution_revision=1 if previous is None else previous.execution_revision + 1,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
