"""Effective Family values with no exposure of hidden competing source/Admin data."""

from dataclasses import dataclass

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .inputs import FieldInput
from .merge import EffectiveValue, KnownValue, PriorChange, merge_value
from .models import ProposedChange, Submission

RESOLVED_EXECUTIONS = frozenset({"published", "resolved_upstream", "resolved_external"})


@dataclass(frozen=True)
class EffectiveField:
    """Public field identity and merged value; the raw source input is omitted."""

    entity: str
    identity: int
    field: str
    effective: EffectiveValue


def census_submission(submission):
    """Find the last complete census response within the admitted history.

    Disabled modules intentionally have an empty Family aggregate. Skipping
    those responses preserves census intent without resurrecting edits omitted
    by a later *census* response. The complete schema requires home_address even
    when its value is unknown, making key presence an immutable module witness.
    """
    require_work_order()
    if submission is None or "home_address" in submission.answers["family"]:
        return submission
    return (
        Submission.objects.filter(
            family_id=submission.family_id,
            campaign_id=submission.campaign_id,
            mode=submission.mode,
            rehearsal_epoch_id=submission.rehearsal_epoch_id,
            family_version__lte=submission.family_version,
            answers__family__has_key="home_address",
        )
        .order_by("-family_version")
        .first()
    )


def proposal_index(submission):
    """Include preserved terminal work within the admitted response namespace.

    Source scope changes can leave a terminal/date request on an older response.
    Select the newest record for each such key, including terminal outcomes so
    an older actionable predecessor can never reappear. Ordinary census and
    proposed-Member values belong to the last census-bearing response; an
    intervening Ministry-only response cannot withdraw unpresented census work.
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
            for row in ProposedChange.objects.filter(
                submission=census_submission(submission)
            )
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


@dataclass(frozen=True)
class HouseholdComposition:
    """Internal effective count/eligibility; no terminal reasons or proposed details."""

    member_count: int
    terminal_members: frozenset[str]


def effective_household(member_duids, submission):
    """Derive shared financial wording and Ministry eligibility from retained intent.

    Disabled census must not reveal terminal/proposed details, but its retained
    unresolved requests still affect household composition. Resolved terminal
    requests defer to the current active source identity set.
    """
    proposals = proposal_index(submission)
    inactive = RESOLVED_EXECUTIONS | {"cancelled", "superseded"}
    terminal = {
        key
        for (entity, key, field), row in proposals.items()
        if entity == "member"
        and field in {"moved_household", "deceased_status"}
        and row.execution not in inactive
        and row.submitted_value is True
    }
    proposed = sum(
        entity == "proposed_member"
        and field == "new_member"
        and row.execution not in inactive
        for (entity, _, field), row in proposals.items()
    )
    members = frozenset(map(str, member_duids))
    return HouseholdComposition(
        len(members - terminal) + proposed, frozenset(terminal & members)
    )
