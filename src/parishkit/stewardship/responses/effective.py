"""Effective Family values with no exposure of hidden competing source/Admin data."""

from dataclasses import dataclass

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .inputs import FieldInput
from .merge import EffectiveValue, KnownValue, PriorChange, merge_value
from .models import ProposedChange

RESOLVED_EXECUTIONS = frozenset({"published", "resolved_upstream", "resolved_external"})


@dataclass(frozen=True)
class EffectiveField:
    """Public field identity and merged value; the raw source input is omitted."""

    entity: str
    identity: int
    field: str
    effective: EffectiveValue


def proposal_index(submission):
    """Include preserved terminal work within the admitted response namespace.

    Source scope changes can leave a terminal/date request on an older response.
    Select the newest record for each such key, including terminal outcomes so
    an older actionable predecessor can never reappear. Ordinary census and
    proposed-Member values still belong only to the exact effective response.
    """
    require_work_order()
    if submission is None:
        return {}
    preserved = (
        ProposedChange.objects.filter(
            submission__family_id=submission.family_id,
            submission__campaign_id=submission.campaign_id,
            submission__mode=submission.mode,
            submission__rehearsal_epoch_id=submission.rehearsal_epoch_id,
            submission__family_version__lte=submission.family_version,
            entity_kind="member",
            field__in=["moved_household", "deceased_status", "death_date"],
        )
        .order_by("entity_key", "field", "-submission__family_version")
        .distinct("entity_key", "field")
    )
    indexed = {(row.entity_kind, row.entity_key, row.field): row for row in preserved}
    indexed.update(
        {
            (row.entity_kind, row.entity_key, row.field): row
            for row in ProposedChange.objects.filter(submission=submission)
        }
    )
    return indexed


def effective_field(field: FieldInput, proposal=None):
    """Only a published/resolved edit may participate in upstream resolution."""
    prior = None
    if proposal is not None and proposal.execution not in {"cancelled", "superseded"}:
        prior = PriorChange(
            KnownValue(proposal.baseline_available, proposal.baseline_value),
            proposal.submitted_value,
            KnownValue(True, proposal.admin_value)
            if proposal.admin_value_set and proposal.execution in RESOLVED_EXECUTIONS
            else KnownValue(False),
        )
    return EffectiveField(
        field.entity,
        field.identity,
        field.field,
        merge_value(field.kind, field.source, prior),
    )


def effective_fields(inputs, submission):
    """Render from the same field registry as concurrency, never raw proposal rows."""
    proposals = proposal_index(submission)
    return tuple(
        effective_field(
            field, proposals.get((field.entity, str(field.identity), field.field))
        )
        for field in inputs.fields
    )
