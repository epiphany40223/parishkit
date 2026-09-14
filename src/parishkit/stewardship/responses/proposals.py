"""Transactional census proposal derivation from immutable complete answers."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .capabilities import capability
from .census import HOUSEHOLD_FIELDS
from .comparison import ValueKind, canonical_value
from .effective import RESOLVED_EXECUTIONS, proposal_index
from .inputs import FieldInput
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
    if "census" not in validated.current.modules:
        # An unenabled census section cannot create or withdraw census work.
        return []
    previous = proposal_index(validated.prior_submission)
    resolved_semantics = {
        key
        for (entity, key, name), row in previous.items()
        if entity == "member"
        and name == "deceased_status"
        and row.execution in RESOLVED_EXECUTIONS
    }
    created = []
    fields = list(validated.current.fields)
    fields.extend(
        FieldInput(
            "proposed_member", key, "new_member", ValueKind.MEMBER, KnownValue(False)
        )
        for key in submission.answers["proposed_members"]
    )
    for field in fields:
        if field.entity not in {"family", "member", "proposed_member"}:
            continue
        if field.entity == "family" and field.field not in HOUSEHOLD_FIELDS:
            continue
        identity = (field.entity, str(field.identity), field.field)
        if field.entity == "member":
            answer = submission.answers["members"][str(field.identity)]
            # Terminal answers deliberately omit ordinary controls. Omission
            # cancels old actionable edits in the final leftover pass below.
            if field.field not in answer or (
                field.field == "death_date" and answer[field.field] is None
            ):
                continue
        old = previous.pop(identity, None)
        submitted = (
            submission.answers["family"][field.field]
            if field.entity == "family"
            else submission.answers["proposed_members"][field.identity]
            if field.entity == "proposed_member"
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
    # Omitted ordinary edits are withdrawn, but source scope changes alone
    # must not discard independent household-status or death-date requests.
    for old in previous.values():
        if old.entity_kind == "member" and old.field in {
            "moved_household",
            "deceased_status",
            "death_date",
        }:
            answer = submission.answers["members"].get(old.entity_key)
            # Source removal is not a Family withdrawal. Also do not withdraw
            # independent date work when completed status work is no longer
            # displayed to the Family. Staff retain these original queue rows.
            if answer is None or (
                old.field == "death_date"
                and old.entity_key in resolved_semantics
                and answer.get("death_date") is None
            ):
                continue
        if old.execution not in RESOLVED_EXECUTIONS | {"cancelled", "superseded"}:
            ProposedChange.objects.filter(pk=old.pk).update(
                execution="cancelled", version=F("version") + 1
            )
    return created
