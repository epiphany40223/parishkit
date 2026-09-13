"""Resolve/conflict local proposals within the same source promotion transaction."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.source.pins import pin_snapshot, release_snapshot_pin
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin
from parishkit.stewardship.storage import StorageInvariantError

from .baselines import _pin_admission
from .census import FAMILY_FIELDS
from .comparison import canonical_value
from .inputs import MEMBER_FIELDS, family_field_value, member_field_value
from .merge import KnownValue, MergeState, PriorChange, merge_value
from .models import ProposedChange, Submission


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
    household_definitions = {field.name: field for field in FAMILY_FIELDS}
    proposals = ProposedChange.objects.filter(
        submission__campaign_id=campaign_id,
        execution__in=["pending", "conflict", "queued", "failed"],
    ).annotate(family_duid=F("submission__family__family_duid"))
    count = 0
    for row in proposals.iterator(chunk_size=500):
        if row.entity_kind == "family" and row.field in household_definitions:
            # No verified source read exists for these semantics yet. Retain
            # submitted household intent even if the Family becomes inactive;
            # loss of portal eligibility revokes access, not its prior history.
            field = household_definitions[row.field]
            current = family_field_value(field)
            if (row.current_available, row.current_value) != (
                current.available,
                current.value,
            ):
                raise StorageInvariantError(
                    "The household source comparison requires a verified adapter."
                )
            continue
        if row.entity_kind != "member" or row.field not in definitions:
            raise StorageInvariantError(
                "The response reconciliation schema is unavailable."
            )
        field = definitions[row.field]
        member = corpus["member"].get(row.entity_key)
        if member is not None and (
            member["family_key"] != str(row.family_duid)
            or not member["active"]
            or member["deceased"]
        ):
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
        if member is not None and (row.current_available, old_key) == (
            current.available,
            new_key,
        ):
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
            "cancelled"
            if member is None
            else "resolved_upstream"
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
        _release_unused_source(row.submission_id, row.current_source_id)
        count += 1
    return count


def _release_unused_source(submission_id, snapshot_id):
    """Drop only an intermediate comparison pin, retaining all provenance inputs.

    The ordered promotion transaction holds the source owner and serializes
    sibling proposals. Their references are checked after each change, so the
    last user releases a shared pin and no retained reference loses protection.
    """
    response = Submission.objects.only(
        "reviewed_source_id", "validation_source_id"
    ).get(pk=submission_id)
    if snapshot_id in (response.reviewed_source_id, response.validation_source_id):
        return
    if ProposedChange.objects.filter(
        submission_id=submission_id, current_source_id=snapshot_id
    ).exists():
        return
    pin = SourceSnapshotPin.objects.filter(
        parent_kind="submission", parent_id=submission_id, snapshot_id=snapshot_id
    ).first()
    if pin is not None:
        release_snapshot_pin(
            pin.pk,
            parent_kind="submission",
            parent_id=submission_id,
            admit=_pin_admission,
        )
