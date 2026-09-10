"""Exact semantic resolution storage; owning workflows verify external work."""

from datetime import date
from uuid import UUID

from django.db import connection
from psycopg.types.json import Jsonb

from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .models import PostCloseMailResolution, RestoreDeliveryHold, RestoreHoldResolution
from .runtime import campaign_transaction


def coverage_digest(coverage):
    """Hash a bounded identifiers-only coverage manifest using PostgreSQL JSONB.

    Explicit versions and a daily inclusive date range prevent a confirmation
    from suppressing later submissions/corrections. Text responses never belong
    in this manifest. JSONB canonicalization is shared with the SQL validator.
    """
    if type(coverage) is not dict or set(coverage) != {"items", "daily_range"}:
        raise ValueError("Invalid mail coverage manifest.")
    items, days = coverage["items"], coverage["daily_range"]
    if type(items) is not list or len(items) > 10000:
        raise ValueError("Invalid mail coverage item count.")
    identities = []
    for item in items:
        if type(item) is not dict or set(item) != {"kind", "id", "version"}:
            raise ValueError("Invalid mail coverage item.")
        if (
            item["kind"] not in {"submission", "item", "correction"}
            or type(item["version"]) is not int
            or item["version"] < 1
        ):
            raise ValueError("Invalid mail coverage version.")
        try:
            if str(UUID(item["id"])) != item["id"]:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid mail coverage identifier.") from None
        identities.append((item["kind"], item["id"], item["version"]))
    if identities != sorted(set(identities)):
        raise ValueError("Mail coverage must be sorted and unique.")
    if days is not None:
        try:
            if type(days) is not dict or set(days) != {"start", "end"}:
                raise ValueError
            start, end = (date.fromisoformat(days[key]) for key in ("start", "end"))
            if (
                start > end
                or start.isoformat() != days["start"]
                or end.isoformat() != days["end"]
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Invalid mail coverage date range.") from None
    if not items and days is None:
        raise ValueError("Empty mail coverage cannot be resolved.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')",
            [Jsonb(coverage)],
        )
        return cursor.fetchone()[0]


def resolve_postclose(
    *,
    campaign_id,
    mode,
    obligation_key,
    coverage,
    reason,
    actor_id,
    correlation_id,
    admit,
    occurrence_id=None,
    task_id=None,
    outbox_id=None,
):
    """Persist one explicit skip after exact outcome cancellation in the callback."""
    if not callable(admit) or not isinstance(actor_id, UUID) or not reason.strip():
        raise TypeError(
            "Post-close resolution requires attributed admission and a reason."
        )
    with campaign_transaction(campaign_id, correlation_id=correlation_id) as (
        campaign,
        runtime,
    ):
        admit("postclose_resolution", campaign, runtime)
        digest = coverage_digest(coverage)
        identity = dict(
            campaign_id=campaign_id,
            mode=mode,
            obligation_key=obligation_key,
            coverage_digest=digest,
        )
        existing = PostCloseMailResolution.objects.filter(**identity).first()
        if existing:
            if (
                existing.actor_id,
                existing.reason,
                existing.occurrence_id,
                existing.task_id,
                existing.outbox_id,
            ) != (actor_id, reason, occurrence_id, task_id, outbox_id):
                raise StorageInvariantError(
                    "Post-close resolution has different intent."
                )
            return existing
        return PostCloseMailResolution.objects.create(
            **identity,
            coverage=coverage,
            reason=reason,
            actor_id=actor_id,
            correlation_id=correlation_id,
            occurrence_id=occurrence_id,
            task_id=task_id,
            outbox_id=outbox_id,
        )


def resolve_restore_hold(
    *,
    hold_id,
    expected_version,
    state,
    evidence,
    actor_id,
    correlation_id,
    admit,
    recovery_occurrence_id=None,
):
    """Append a review decision; assumed delivery never inserts fulfillment."""
    if not callable(admit) or not isinstance(actor_id, UUID):
        raise TypeError("Restore hold resolution requires current owning admission.")
    original = RestoreDeliveryHold.objects.select_related("definition").get(pk=hold_id)
    with campaign_transaction(
        original.definition.campaign_id, correlation_id=correlation_id
    ) as (campaign, runtime):
        hold = RestoreDeliveryHold.objects.select_for_update().get(pk=hold_id)
        admit("restore_hold_resolution", campaign, runtime, hold)
        existing = RestoreHoldResolution.objects.filter(
            hold=hold, version=expected_version + 1
        ).first()
        if existing:
            if (
                existing.actor_id,
                existing.state,
                existing.evidence,
                existing.recovery_occurrence_id,
            ) != (actor_id, state, evidence, recovery_occurrence_id):
                raise StorageInvariantError(
                    "Restore hold resolution has different intent."
                )
            return existing
        if hold.version != expected_version:
            raise StaleRecordError("Restore hold changed; reload before retrying.")
        return RestoreHoldResolution.objects.create(
            hold=hold,
            version=expected_version + 1,
            state=state,
            evidence=evidence,
            recovery_occurrence_id=recovery_occurrence_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
