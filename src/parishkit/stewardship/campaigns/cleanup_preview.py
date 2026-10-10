"""Exact, read-only Testing impact before irreversible go-live acknowledgement."""

from dataclasses import dataclass

from django.db.models import Count, F, TextField, Value
from django.db.models.functions import Coalesce, Lower

from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.source.snapshot_name_sql import SnapshotFamilyName
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.web.tables import Sorting, bounded_count, read_window

from .cleanup_catalog import (
    CleanupCategory,
    inventory_queries,
    iter_inventory,
    summarize_targets,
)
from .credential_models import FamilyCampaign
from .production_storage import CleanupInventory
from .work_locks import require_work_order

# The Testing Families table sorts on the server by either column. DUID is
# the default and is covered by the family_campaign_duid unique index
# (campaign_id, family_duid). Family name is not a FamilyCampaign column: it
# is built per row from the current source snapshot (SnapshotFamilyName,
# through the (snapshot, source_key) unique indexes), the same "Surname,
# heads" string snapshot_family_names shows, ordered by surname and then the
# whole name as the Family codes directory is. The inventory is only
# Families with Testing submissions, so that per-row lookup is bounded. id
# is the unique tiebreak.
TESTING_FAMILY_SORTING = Sorting.by_column(
    {"name": ("sort_surname", "sort_name"), "duid": ("family_duid",)},
    default="duid",
    tiebreak=("id",),
)

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


def _sort_name(source_id, *, surname_only=False):
    """The shown Family name (or its surname), lowercased to sort; "" without
    a source snapshot or for a Family missing from it."""
    if source_id is None:
        return Value("", output_field=TextField())
    name = SnapshotFamilyName(source_id, F("family_duid"), surname_only=surname_only)
    return Lower(Coalesce(name, Value(""), output_field=TextField()))


def cleanup_families(campaign_id, *, source_id, window, sort="duid"):
    """Read one bounded page of distinct affected Families, never their answers.

    Returns (window, rows, has_next, total); ``sort`` is a
    TESTING_FAMILY_SORTING token, ``total`` a ``bounded_count`` of every
    affected Family and ``window`` the page actually read (the last one when
    the requested page is past it).
    """
    require_work_order()
    responses = inventory_queries(campaign_id)[CleanupCategory.SUBMISSION]
    families = FamilyCampaign.objects.filter(
        campaign_id=campaign_id,
        pk__in=responses.values("family_id"),
    )
    query = families.only("id", "family_duid")
    if TESTING_FAMILY_SORTING.tokens[sort][0] == "name":
        query = query.annotate(
            sort_surname=_sort_name(source_id, surname_only=True),
            sort_name=_sort_name(source_id),
        )
    total = bounded_count(families)
    window, rows, has_next = read_window(
        window, TESTING_FAMILY_SORTING.order(query, sort), total
    )
    names = snapshot_family_names(source_id, [row.family_duid for row in rows])
    return (
        window,
        [
            {"duid": row.family_duid, "name": names.get(row.family_duid, "")}
            for row in rows
        ],
        has_next,
        total,
    )
