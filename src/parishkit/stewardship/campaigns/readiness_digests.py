"""Read-only exact Admin mail impact, without materializing dates or report facts."""

import hashlib
from dataclasses import dataclass

from parishkit.stewardship.reports.readiness_weekly import weekly_message_count
from parishkit.stewardship.storage import StorageInvariantError

from .models import RestoreDeliveryHold
from .readiness_families import _bind
from .schedule_evaluation import SchedulePlan
from .schedule_models import ScheduleDefinition, ScheduleFulfillment, ScheduleOccurrence
from .schedule_recovery import digest_recovery_kind
from .work_locks import require_work_order


@dataclass(frozen=True)
class DigestImpact:
    """As-of physical messages, distinct from covered dates and empty intervals."""

    daily_messages: int
    weekly_messages: int
    coalesced_slots: int
    empty_weekly_reports: int
    blocked_groups: int
    digest: str


def digest_impact(campaign, *, cutoff, recipients):
    """Coalesce the complete due range using the execution owner's count planner.

    Read at most 100 dates' metadata per page. A weekly interval with no new
    actionable information or correction produces no message, and previously
    accepted per-recipient item coverage suppresses duplicates. These counts
    exclude Testing history even while the campaign is still in Testing mode.
    """
    require_work_order()
    definitions = list(
        ScheduleDefinition.objects.filter(
            campaign=campaign,
            current_revision__isnull=False,
            kind__in=("daily_digest", "weekly_digest"),
        )
        .select_related("current_revision")
        .order_by("id")[:3]
    )
    if len(definitions) > 2:
        raise StorageInvariantError("Readiness digest definitions exceed their bound.")
    digest = hashlib.sha256(b"stewardship-readiness-digests-v1\x00")

    def bind(value):
        """Frame each relevant input without persisting report contents."""
        _bind(digest, value)

    bind(recipients)
    daily, weekly, coalesced, empty, blocked = 0, 0, 0, 0, 0
    for definition in definitions:
        bind((definition.pk, definition.version, definition.current_revision_id))
        count, held = _candidates(campaign, definition, cutoff, bind)
        choice = digest_recovery_kind(definition.kind, count)
        bind((count, held, choice))
        if held:
            blocked += 1
            continue
        if choice == "empty":
            continue
        coalesced += count if choice == "aggregate" else count - 1
        if definition.kind == "daily_digest":
            daily += len(recipients)
        else:
            weekly += weekly_message_count(campaign.pk, recipients, bind=bind)
            empty += int(weekly == 0)
    return DigestImpact(daily, weekly, coalesced, empty, blocked, digest.hexdigest())


def _candidates(campaign, definition, cutoff, bind):
    """Count uncovered original dates; allocated/uncertain work fails closed."""
    plan = SchedulePlan.from_values(
        definition.current_revision.values, campaign.active_configuration.values
    )
    common = {"definition": definition, "mode": "production", "target": "admins"}
    # A recovery aggregate is not an original civil-date slot. Never omit its
    # pending authority merely because date pagination cannot encounter its key.
    blocked = False
    for row in (
        ScheduleOccurrence.objects.filter(
            **common, due_at__lte=cutoff, slot__startswith="recovery:"
        )
        .order_by("id")
        .values("id", "state", "version")
        .iterator(chunk_size=200)
    ):
        bind(row)
        blocked |= row["state"] in {"pending", "running", "delivery_unknown"}
    after, count = None, 0
    while True:
        page = plan.page(through=cutoff, after=after, limit=100)
        keys = [slot.key for slot in page.slots]
        excluded = set()
        for model, field, filters in (
            (ScheduleFulfillment, "disposition", {}),
            (
                RestoreDeliveryHold,
                "state",
                {"state__in": ("unreviewed", "assumed_delivered")},
            ),
        ):
            for row in (
                model.objects.filter(**common, slot__in=keys, **filters)
                .order_by("slot")
                .values("slot", field)
            ):
                bind((field, row))
                excluded.add(row["slot"])
        existing = {}
        for row in (
            ScheduleOccurrence.objects.filter(
                **common, revision_id=definition.current_revision_id, slot__in=keys
            )
            .order_by("slot", "-recovery_generation")
            .distinct("slot")
            .values("id", "slot", "state", "task_id", "outbox_id", "version")
        ):
            bind(row)
            existing[row["slot"]] = row
        for slot in page.slots:
            bind((slot.key, slot.due_at))
            if slot.key in excluded:
                continue
            row = existing.get(slot.key)
            if row is None:
                count += 1
            elif row["state"] in {"running", "delivery_unknown"} or (
                row["state"] == "pending" and (row["task_id"] or row["outbox_id"])
            ):
                blocked = True
            elif row["state"] == "pending":
                count += 1
        if page.exhausted:
            return count, blocked
        after = page.cursor
