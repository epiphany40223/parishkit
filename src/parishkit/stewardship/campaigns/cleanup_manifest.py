"""Persist exact gated inventory; no HTTP endpoint or deletion authority lives here."""

import hashlib

from django.db import transaction
from django.db.models import Count, Sum

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.outbox_models import OutboxMessage, OutboxRender
from parishkit.stewardship.storage import StorageInvariantError

from .cleanup_catalog import CleanupCategory, inventory_queries, iter_inventory
from .production_models import ProductionCleanupManifest, ProductionCleanupTarget
from .production_storage import TestingSummary, _locked
from .work_locks import require_work_order


def testing_summary(campaign_id, *, readiness_digest):
    """Capture actual response and terminal delivery totals under the work gate."""
    responses = inventory_queries(campaign_id)[CleanupCategory.SUBMISSION]
    messages = OutboxMessage.objects.filter(
        campaign_id=campaign_id, mode="testing", routing="testing_override"
    )
    totals = {
        row["state"]: row["total"]
        for row in messages.values("state").annotate(total=Count("id"))
    }
    if set(totals) - {"delivered", "permanent_failure", "cancelled"}:
        raise StorageInvariantError("Cleanup requires terminal Testing deliveries.")
    return TestingSummary(
        readiness_digest=readiness_digest,
        submissions=responses.count(),
        families=responses.values("family_id").distinct().count(),
        messages=sum(totals.values()),
        delivered=totals.get("delivered", 0),
        failed=totals.get("permanent_failure", 0),
        cancelled=totals.get("cancelled", 0),
    )


def delivery_summary(campaign_id):
    """Aggregate only terminal Testing mail; retain no address, body or provider ID.

    The manifest's creation timestamp records cleanup start. Its request already
    references the overall submission/Family/message aggregate. These fields
    preserve its type/result, attempt, template and recipient-fingerprint detail.
    SQL independently recomputes them before the marker can commit.
    """
    require_work_order()
    messages = OutboxMessage.objects.filter(
        campaign_id=campaign_id, routing="testing_override", mode="testing"
    )
    if messages.exclude(
        state__in=["delivered", "permanent_failure", "cancelled"]
    ).exists():
        raise StorageInvariantError("Cleanup requires terminal Testing deliveries.")
    counts = {}
    for row in messages.values("purpose", "state").annotate(total=Count("id")):
        counts.setdefault(row["purpose"], {})[row["state"]] = row["total"]
    templates = OutboxRender.objects.filter(
        message_id__in=messages.values("pk"), template_id__isnull=False
    )
    recipient = SystemConfiguration.objects.get().testing_recipient
    return {
        "delivery_counts": counts,
        "delivery_attempts": messages.aggregate(total=Sum("attempt"))["total"] or 0,
        "template_ids": [
            str(value)
            for value in templates.order_by("template_id")
            .values_list("template_id", flat=True)
            .distinct()
        ],
        "testing_recipient_fingerprint": hashlib.sha256(
            b"stewardship-testing-recipient-v1\x00" + recipient.encode("utf-8")
        ).hexdigest(),
    }


def seal_manifest(request_id):
    """Capture current exact membership in the caller's admitted begin transaction.

    The Production readiness owner must call this before releasing the same
    work transaction that recorded acknowledgement and acquired the gate. SQL
    independently compares every target, count and digest before sealing; target
    rows cannot commit without that verified marker. Only UUIDs are copied, in
    bounded insert groups, and no census answers or session keys enter inventory.
    """
    require_work_order()
    with transaction.atomic():
        request, campaign = _locked(request_id)
        existing = ProductionCleanupManifest.objects.filter(request=request).first()
        if existing is not None:
            return existing.pk
        if request.state != "cleanup_queued" or request.version != 1:
            raise StorageInvariantError("Cleanup inventory is no longer open.")
        pending = []
        for position, target in enumerate(iter_inventory(campaign.pk), start=1):
            pending.append(
                ProductionCleanupTarget(
                    request=request,
                    category=target.category.value,
                    target_id=target.identifier,
                    position=position,
                    actor_id=request.initiated_by_id,
                    correlation_id=request.correlation_id,
                )
            )
            if len(pending) == 500:
                ProductionCleanupTarget.objects.bulk_create(pending)
                pending.clear()
        if pending:
            ProductionCleanupTarget.objects.bulk_create(pending)
        return ProductionCleanupManifest.objects.create(
            request=request,
            actor_id=request.initiated_by_id,
            correlation_id=request.correlation_id,
            **delivery_summary(campaign.pk),
        ).pk
