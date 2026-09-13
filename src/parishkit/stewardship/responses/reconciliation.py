"""Resolve/conflict local proposals within the same source promotion transaction."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.pins import pin_snapshot
from parishkit.stewardship.storage import StorageInvariantError

from .baselines import _pin_admission
from .comparison import canonical_value
from .inputs import MEMBER_FIELDS, member_field_value
from .merge import KnownValue, MergeState, PriorChange, merge_value
from .models import ProposedChange


def reconcile_proposals(snapshot, corpus, *, campaign_id):
    """Use the promotion owner's coherent corpus, never fetch a second generation.

    Family membership is checked before reading a Member's new values, even for
    staff-derived reconciliation. A Member moved elsewhere becomes unavailable,
    not a source of another household's private data. Canonically unchanged
    inputs keep their old reference; unrelated nightly refreshes do not create
    new retention pins or rewrite every pending proposal.
    """
    require_work_order()
    definitions = {field.name: field for field in MEMBER_FIELDS}
    proposals = ProposedChange.objects.filter(
        submission__campaign_id=campaign_id,
        execution__in=["pending", "conflict", "queued", "failed"],
    ).annotate(family_duid=F("submission__family__family_duid"))
    count = 0
    for row in proposals.iterator(chunk_size=500):
        if row.entity_kind != "member" or row.field not in definitions:
            raise StorageInvariantError(
                "The response reconciliation schema is unavailable."
            )
        field = definitions[row.field]
        member = corpus["member"].get(row.entity_key)
        if member is not None and member["family_key"] != str(row.family_duid):
            member = None
        current = member_field_value(
            member, corpus["contact"].get("member:" + row.entity_key), field
        )
        old_key = (
            canonical_value(field.kind, row.current_value)
            if row.current_available
            else None
        )
        new_key = (
            canonical_value(field.kind, current.value) if current.available else None
        )
        if (row.current_available, old_key) == (current.available, new_key):
            continue
        result = merge_value(
            field.kind,
            current,
            PriorChange(
                KnownValue(row.baseline_available, row.baseline_value),
                row.submitted_value,
            ),
        )
        execution = (
            "resolved_upstream"
            if result.state is MergeState.UPSTREAM_CAUGHT_UP
            else "conflict"
            if result.conflict
            else "pending"
        )
        pin_snapshot(
            snapshot.pk,
            parent_kind="submission",
            parent_id=row.submission_id,
            admit=_pin_admission,
        )
        ProposedChange.objects.filter(pk=row.pk).update(
            current_available=current.available,
            current_value=current.value,
            current_source_id=snapshot.pk,
            execution=execution,
            version=F("version") + 1,
        )
        count += 1
    return count
