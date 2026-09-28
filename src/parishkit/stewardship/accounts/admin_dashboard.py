"""Read-only campaign overview with role-appropriate aggregates and no credentials."""

import json
from datetime import timedelta
from uuid import uuid4

from django.db import connection
from django.db.models import Count, Q

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.refresh_status import (
    full_refresh_status,
    refresh_schedule,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot

from .policy import Capability, allows


def summary(actor, configuration, now):
    """Read under the caller's work lock, which pins source/configuration promotion."""
    campaign = configuration.current_campaign
    # One query for the promoted snapshot's time: the page has a fixed query
    # budget, and the pointer's own row need not be read first.
    refreshed_at = (
        SourceSnapshot.objects.filter(
            pk__in=SourceCurrent.objects.exclude(snapshot_id=None).values("snapshot_id")
        )
        .values_list("promoted_at", flat=True)
        .first()
    )
    result = {
        "campaign": campaign,
        "refreshed_at": refreshed_at,
        "full_refresh": full_refresh_status(refresh_schedule(configuration), now),
        # Only an Administrator may request a manual refresh; the link is
        # offered to nobody else.
        "can_refresh": allows(actor, Capability.CONFIGURE),
        # A fresh key for the one-click full refresh button (no query).
        "refresh_key": uuid4() if allows(actor, Capability.CONFIGURE) else None,
        # The failure notice links to the failed task only for users who may
        # open background task pages; others see the notice alone.
        "can_view_task": allows(actor, Capability.BACKGROUND_WORK),
    }
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
        if campaign.state in {"draft", "scheduled", "active"} and allows(
            actor, Capability.FAMILY_CODES
        ):
            result["unreachable"] = unreachable_families(campaign.pk)
    if allows(actor, Capability.CONFIGURE):
        from .backup_destination import offsite_status

        kinds = {
            record["values"]["kind"]
            for record in configuration.active_configuration.canonical_document[
                "sections"
            ].get("integrations", [])
        }
        # Read copy outcomes only when off-site copies are set up, so the
        # page's query budget is unchanged otherwise; until then the page
        # invites the Administrator to set them up.
        if "backup" in kinds:
            result["offsite"] = offsite_status()
        else:
            result["offsite_unset"] = True
    if "administrator" in actor.roles:
        from .security_events import open_events

        # Every Administrator's dashboard shows an expansion of who may sign
        # in until an Administrator acknowledges it.
        result["security_events"] = open_events(actor)
    if allows(actor, Capability.BACKGROUND_WORK):
        result["recent_failures"] = list(
            TaskRun.objects.filter(
                state="failed", updated_at__gte=now - timedelta(hours=24)
            )
            .order_by("-updated_at", "-id")
            .values("id", "task_type", "updated_at")[:5]
        )
    return result


def unreachable_families(campaign_id):
    """Count Families no campaign mail can reach: no email and no mailing address.

    A readiness figure, from the same SQL selection the Family-code
    directory uses (its "neither" reach filter), so the number matches the list
    its link opens. None when there is no promoted source to count from.
    """
    from parishkit.stewardship.reports.directory_query import DIRECTORY

    parameters = {
        "filters": {
            "search": "",
            "reason": "any",
            "phone": "any",
            "response": "any",
            "sort": "name",
            "reach": "neither",
        },
        "postal": False,
        "exact": False,
        "family_id": None,
    }
    # One statement; the dashboard's own error handling covers a failure.
    with connection.cursor() as cursor:
        cursor.execute(DIRECTORY, (campaign_id, json.dumps(parameters), 1))
        row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(row[0])["unreachable_total"]
