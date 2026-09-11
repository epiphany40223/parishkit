"""Ordered boundary storage; BG-02 supplies scheduler and worker admission."""

from uuid import UUID, uuid5

from django.db.models import F

from parishkit.stewardship.storage import StorageInvariantError

from .admission import CampaignAdmissionUnavailable
from .lifecycle import Action
from .models import CampaignBoundaryOccurrence
from .runtime import _emit, _now, campaign_transaction


def apply_due_boundaries(
    *, campaign_id, task_id, fence, actor_id, correlation_id, admit
):
    """Commit overdue start then close together, irrespective of hint ordering.

    TaskRun owns attempts and leases. Both occurrence outcomes reference the
    same campaign-bound worker when an overdue close must apply its predecessor.
    No provider or queue operation is performed in this transaction.
    """
    if (
        not callable(admit)
        or any(not isinstance(value, UUID) for value in (task_id, actor_id))
        or type(fence) is not int
        or fence < 1
    ):
        raise TypeError("Boundary processing requires attributed fenced admission.")
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        if runtime.restore_review_required:
            raise CampaignAdmissionUnavailable(
                "Campaign boundaries are held for restore review."
            )
        if runtime.current_campaign_id != campaign.pk:
            raise StorageInvariantError("Campaign boundaries are held.")
        projection = campaign.active_configuration
        results = []
        now = _now()
        for kind, due, source in (
            (Action.START, projection.starts_at, "scheduled"),
            (Action.CLOSE, projection.ends_at, "active"),
        ):
            if due > now:
                continue
            admit(kind, campaign, runtime, None)
            occurrence, _ = CampaignBoundaryOccurrence.objects.get_or_create(
                campaign=campaign,
                kind=kind.value,
                due_at=due,
                defaults={"actor_id": actor_id, "correlation_id": correlation_id},
            )
            if occurrence.state != "pending":
                results.append(occurrence)
                continue
            if (occurrence.task_id, occurrence.task_fence) != (task_id, fence):
                CampaignBoundaryOccurrence.objects.filter(pk=occurrence.pk).update(
                    task_id=task_id,
                    task_fence=fence,
                    version=F("version") + 1,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                )
            if campaign.state != source or runtime.mode != "production":
                CampaignBoundaryOccurrence.objects.filter(pk=occurrence.pk).update(
                    state="skipped",
                    completed_at=now,
                    reason="not_applicable",
                    version=F("version") + 1,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                )
            else:
                _emit(
                    campaign,
                    runtime,
                    action=kind,
                    request_id=uuid5(occurrence.pk, "boundary-transition"),
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    boundary_id=occurrence.pk,
                    task_fence=fence,
                )
                campaign.refresh_from_db()
                runtime.refresh_from_db()
            occurrence.refresh_from_db()
            results.append(occurrence)
        return tuple(results)
