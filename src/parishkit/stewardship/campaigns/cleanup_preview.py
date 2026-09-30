"""Exact, read-only Testing impact before irreversible go-live acknowledgement."""

from dataclasses import dataclass

from django.db.models import (
    CharField,
    Count,
    JSONField,
    OuterRef,
    Subquery,
    TextField,
    Value,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce, Concat, Lower, NullIf, Trim

from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.source.family_names import family_display_name
from parishkit.stewardship.source.version_models import SnapshotFamily
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
# is read per row from the current source snapshot's Family payload through
# the (snapshot, source_key) unique index, the same lastName, mailingName,
# then "first last" choice family_display_name makes for display. The
# inventory is only Families with Testing submissions, so that per-row
# lookup is bounded. id is the unique tiebreak.
TESTING_FAMILY_SORTING = Sorting.by_column(
    {"name": ("sort_name",), "duid": ("family_duid",)},
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


def _source_name(source_id):
    """The Family's display name from the source snapshot, lowercased to sort."""
    if source_id is None:
        return Value("", output_field=TextField())

    def text(key):
        """One trimmed payload field, NULL when blank."""
        return NullIf(Trim(KT(f"document__{key}")), Value(""), output_field=TextField())

    name = Coalesce(
        text("lastName"),
        text("mailingName"),
        Trim(
            Concat(
                text("firstName"),
                Value(" "),
                text("lastName"),
                output_field=TextField(),
            )
        ),
        Value(""),
        output_field=TextField(),
    )
    return Subquery(
        SnapshotFamily.objects.filter(
            snapshot_id=source_id,
            source_key=Cast(OuterRef("family_duid"), CharField()),
        )
        # The payload is canonical JSON text; read it as jsonb to pick fields.
        .annotate(document=Cast("payload__canonical", JSONField()))
        .annotate(sort_name=Lower(name))
        .values("sort_name")[:1]
    )


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
            sort_name=Coalesce(
                _source_name(source_id), Value(""), output_field=TextField()
            )
        )
    total = bounded_count(families)
    window, rows, has_next = read_window(
        window, TESTING_FAMILY_SORTING.order(query, sort), total
    )
    names = {}
    if source_id is not None:
        for row in SnapshotFamily.objects.filter(
            snapshot_id=source_id,
            source_key__in=[str(family.family_duid) for family in rows],
        ).select_related("payload"):
            value = row.payload.payload
            names[int(row.source_key)] = family_display_name(value)
    return (
        window,
        [
            {"duid": row.family_duid, "name": names.get(row.family_duid, "")}
            for row in rows
        ],
        has_next,
        total,
    )
