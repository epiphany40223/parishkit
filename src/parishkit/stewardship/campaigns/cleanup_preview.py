"""Exact, read-only Testing impact before irreversible go-live acknowledgement."""

from dataclasses import dataclass

from django.db.models import Count

from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.source.version_models import SnapshotFamily

from .cleanup_catalog import (
    CleanupCategory,
    inventory_queries,
    iter_inventory,
    summarize_targets,
)
from .credential_models import FamilyCampaign
from .production_storage import CleanupInventory
from .work_locks import require_work_order

TERMINAL = frozenset({"delivered", "permanent_failure", "cancelled"})


@dataclass(frozen=True)
class CleanupPreview:
    """Non-sensitive totals; Family identities are a separately paginated query."""

    inventory: CleanupInventory
    submissions: int
    families: int
    message_states: tuple[tuple[str, int], ...]

    @property
    def messages(self):
        """Include unresolved deliveries so the preview never hides blockers."""
        return sum(count for _, count in self.message_states)

    @property
    def unresolved(self):
        """Uncertain provider acceptance is not equivalent to terminal failure."""
        return sum(
            count for state, count in self.message_states if state not in TERMINAL
        )


def cleanup_preview(campaign_id):
    """Use the cleanup owner's exact inventory without acquiring its deletion gate.

    All selections share the caller's ordered transaction. A nonterminal outbox
    blocks cleanup but not this diagnostic preview, unlike the later aggregate
    writer which must reject nonterminal delivery counts.
    """
    require_work_order()
    responses = inventory_queries(campaign_id)[CleanupCategory.SUBMISSION]
    states = tuple(
        OutboxMessage.objects.filter(
            campaign_id=campaign_id, mode="testing", routing="testing_override"
        )
        .values("state")
        .annotate(total=Count("id"))
        .order_by("state")
        .values_list("state", "total")
    )
    return CleanupPreview(
        summarize_targets(iter_inventory(campaign_id)),
        responses.count(),
        responses.values("family_id").distinct().count(),
        states,
    )


def cleanup_families(campaign_id, *, source_id, window):
    """Read one bounded page of distinct affected Families, never their answers."""
    require_work_order()
    responses = inventory_queries(campaign_id)[CleanupCategory.SUBMISSION]
    rows, has_next = window.rows(
        FamilyCampaign.objects.filter(
            campaign_id=campaign_id,
            pk__in=responses.values("family_id"),
        )
        .order_by("family_duid")
        .only("id", "family_duid")
    )
    names = {}
    if source_id is not None:
        for row in SnapshotFamily.objects.filter(
            snapshot_id=source_id,
            source_key__in=[str(family.family_duid) for family in rows],
        ).select_related("payload"):
            value = row.payload.payload
            names[int(row.source_key)] = (
                value.get("mailingName")
                or " ".join(
                    value.get(field) or "" for field in ("firstName", "lastName")
                ).strip()
            )
    return [
        {"duid": row.family_duid, "name": names.get(row.family_duid, "")}
        for row in rows
    ], has_next
