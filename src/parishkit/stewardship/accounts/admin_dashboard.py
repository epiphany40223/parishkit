"""Read-only campaign overview with role-appropriate aggregates and no credentials."""

from datetime import timedelta

from django.db.models import Count, Q

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .policy import Capability, allows


def summary(actor, configuration, now):
    """Read under the caller's work lock, which pins source/configuration promotion."""
    campaign = configuration.current_campaign
    current = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    refreshed_at = (
        SourceSnapshot.objects.filter(pk=current)
        .values_list("promoted_at", flat=True)
        .first()
        if current
        else None
    )
    result = {"campaign": campaign, "refreshed_at": refreshed_at}
    if campaign is not None:
        result["next_mail"] = (
            ScheduleRevision.objects.filter(
                configuration_id=configuration.active_configuration_id,
                campaign_id=campaign.pk,
                kind__in=["initial", "reminder"],
                due_at__gt=now,
            )
            .order_by("due_at", "record_id")
            .values("kind", "due_at")
            .first()
        )
        if allows(actor, Capability.CAMPAIGN_REPORT):
            counts = FamilyCampaign.objects.filter(campaign=campaign).aggregate(
                active=Count("id", filter=Q(active=True)),
                eligible=Count("id", filter=Q(portal_eligible=True)),
                responded=Count("id", filter=Q(first_live_submission_id__isnull=False)),
                eligible_responded=Count(
                    "id",
                    filter=Q(
                        portal_eligible=True, first_live_submission_id__isnull=False
                    ),
                ),
            )
            counts["participation"] = Percentage(
                counts["eligible_responded"], counts["eligible"]
            )
            result["families"] = counts
    if allows(actor, Capability.BACKGROUND_WORK):
        result["recent_failures"] = list(
            TaskRun.objects.filter(
                state="failed", updated_at__gte=now - timedelta(hours=24)
            )
            .order_by("-updated_at", "-id")
            .values("id", "task_type", "updated_at")[:5]
        )
    return result
