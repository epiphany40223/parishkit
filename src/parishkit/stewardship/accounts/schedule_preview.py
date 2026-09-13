"""Bounded schedule-level impact counts without reading Family or email content."""

import hashlib
import json

from django.db.models import Count, F, Q, Sum

from parishkit.stewardship.campaigns.models import (
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES


def work_summary(campaign_id):
    """Capture aggregate monotonic versions and exact current-revision work counts.

    Every occurrence/task mutation increments its version. Count plus version
    sums therefore invalidates a preview when that retained corpus changes,
    without materializing per-Family rows. Purge is separately fenced by the
    campaign work scope; ordinary schedule work cannot delete these records.
    Pending outbox-linked work stays blocked until its owning delivery workflow
    can atomically reconcile it; a UUID alone never proves provider safety.
    """
    blocked = Q(state__in=["running", "delivery_unknown"]) | (
        Q(state="pending")
        & (Q(outbox_id__isnull=False) | Q(task__state__in=NONTERMINAL_STATES))
    )
    rows = {
        str(row["id"]): {
            "definition_version": row["version"],
            "revision": str(row["current_revision_id"]),
            "occurrences": 0,
            "versions": 0,
            "task_versions": 0,
            "outboxes": 0,
            "blocking": 0,
            "cancellable": 0,
            "failed": 0,
            "delivered": 0,
            "covered": 0,
        }
        for row in ScheduleDefinition.objects.filter(
            campaign_id=campaign_id, current_revision_id__isnull=False
        ).values("id", "version", "current_revision_id")
    }
    for aggregate in (
        ScheduleOccurrence.objects.filter(
            definition__campaign_id=campaign_id,
            revision_id=F("definition__current_revision_id"),
        )
        .values("definition_id")
        .annotate(
            occurrences=Count("id"),
            versions=Sum("version", default=0),
            task_versions=Sum("task__version", default=0),
            outboxes=Count("outbox_id", distinct=True),
            blocking=Count("id", filter=blocked),
            cancellable=Count("id", filter=Q(state="pending") & ~blocked),
            failed=Count("id", filter=Q(state="failed")),
        )
    ):
        identifier = str(aggregate.pop("definition_id"))
        if identifier in rows:
            rows[identifier].update(aggregate)
    for aggregate in (
        ScheduleFulfillment.objects.filter(definition_id__in=rows)
        .values("definition_id")
        .annotate(
            delivered=Count("id", filter=Q(disposition="delivered")),
            covered=Count("id"),
        )
    ):
        identifier = str(aggregate.pop("definition_id"))
        rows[identifier].update(aggregate)
    return rows


def fingerprint(domain_fingerprint, summary):
    """A signed preview binds domain scope and the exact aggregate work generation."""
    return hashlib.sha256(
        json.dumps([domain_fingerprint, summary], sort_keys=True).encode()
    ).hexdigest()
