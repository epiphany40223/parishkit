"""Immutable claim-event evidence for preparation writes without a context flag."""

from parishkit.stewardship.jobs.models import TaskRunEvent
from parishkit.stewardship.jobs.ownership import lock_task_claim


def claim_event(claim):
    """Bind SQL effects to this exact lease fence, not just a reusable run UUID."""
    lock_task_claim(claim)
    return TaskRunEvent.objects.values_list("id", flat=True).get(
        run_id=claim.run_id,
        action="claim",
        fence=claim.fence,
        worker_id=claim.worker_id,
        state="running",
    )
