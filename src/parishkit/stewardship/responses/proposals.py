"""Transactional census proposal derivation from immutable complete answers."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .capabilities import capability
from .census import HOUSEHOLD_FIELDS
from .comparison import canonical_value
from .effective import RESOLVED_EXECUTIONS, proposal_index
from .merge import KnownValue, MergeState, PriorChange, merge_value
from .models import ProposedChange


def derive_proposals(submission, validated):
    """Preserve unchanged review intent while replacing the effective aggregate.

    A same-intent pending proposal retains its original comparison baseline and
    decision, so a revisit cannot silently clear an upstream conflict or revive
    an ignored request. A genuinely edited field starts a new unreviewed intent.
    Published/resolved outcomes remain immutable; later requests get new rows.
    No immutable Family answer is edited, including to an Admin-corrected value.
    """
    require_work_order()
    previous = proposal_index(validated.prior_submission)
    created = []
    for field in validated.current.fields:
        if field.entity not in {"family", "member"}:
            continue
        if field.entity == "family" and field.field not in HOUSEHOLD_FIELDS:
            continue
        identity = (field.entity, str(field.identity), field.field)
        old = previous.pop(identity, None)
        submitted = (
            submission.answers["family"][field.field]
            if field.entity == "family"
            else submission.answers["members"][str(field.identity)][field.field]
        )
        key = canonical_value(field.kind, submitted)
        current_key = (
            canonical_value(field.kind, field.source.value)
            if field.source.available
            else None
        )
        unchanged = (field.source.available and key == current_key) or (
            not field.source.available and submitted is None
        )
        new = None
        if not unchanged:
            same_intent = (
                old is not None
                and old.execution
                not in RESOLVED_EXECUTIONS | {"cancelled", "superseded"}
                and canonical_value(field.kind, old.submitted_value) == key
            )
            baseline = (
                KnownValue(old.baseline_available, old.baseline_value)
                if same_intent
                else field.source
            )
            merge = merge_value(
                field.kind, field.source, PriorChange(baseline, submitted)
            )
            carry_decision = same_intent
            new = ProposedChange.objects.create(
                submission=submission,
                entity_kind=field.entity,
                entity_key=str(field.identity),
                field=field.field,
                baseline_available=baseline.available,
                baseline_value=baseline.value,
                submitted_value=submitted,
                current_available=field.source.available,
                current_value=field.source.value,
                current_source_id=validated.validation_snapshot_id,
                handling=capability(field.entity, field.field).handling.value,
                decision=old.decision if carry_decision else "unreviewed",
                admin_value_set=old.admin_value_set if carry_decision else False,
                admin_value=old.admin_value if carry_decision else None,
                execution="conflict"
                if merge.state is MergeState.CONFLICT
                else "pending",
                actor_id=submission.family_id,
            )
            created.append(new)
        if old is not None and old.execution not in RESOLVED_EXECUTIONS | {
            "cancelled",
            "superseded",
        }:
            caught_up = (
                field.source.available
                and canonical_value(field.kind, old.submitted_value) == current_key
            )
            ProposedChange.objects.filter(pk=old.pk).update(
                execution="superseded"
                if new
                else "resolved_upstream"
                if caught_up
                else "cancelled",
                superseded_by=new,
                version=F("version") + 1,
            )
    # A removed/inaccessible Member is not replayed into the new answer set.
    # Preserve the old request's history while taking it out of actionable work.
    for old in previous.values():
        if old.execution not in RESOLVED_EXECUTIONS | {"cancelled", "superseded"}:
            ProposedChange.objects.filter(pk=old.pk).update(
                execution="cancelled", version=F("version") + 1
            )
    return created
