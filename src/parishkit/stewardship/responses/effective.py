"""Effective Family values with no exposure of hidden competing source/Admin data."""

from dataclasses import dataclass

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .census import FAMILY_FIELDS
from .comparison import canonical_value
from .inputs import FieldInput
from .member_census import MEMBER_FIELDS
from .merge import EffectiveValue, KnownValue, PriorChange, merge_value
from .models import ProposedChange, Submission

RESOLVED_EXECUTIONS = frozenset({"published", "resolved_upstream", "resolved_external"})
# Changes a person entered by hand or publication wrote, which ParishSoft's
# download may not show yet; a Family's unchanged answer keeps them (#1683).
ENTERED_EXECUTIONS = frozenset({"published", "resolved_external"})
# Executions of a change that is no longer in force (closed), as against an
# open one (pending, conflict, queued, failed).
CLOSED_EXECUTIONS = RESOLVED_EXECUTIONS | {"cancelled", "superseded"}
# The ordinary census fields whose latest change lives on the last census
# response, by (entity, field), with the kind their answers compare by.
ORDINARY_KINDS = {
    **{("family", field.name): field.kind for field in FAMILY_FIELDS},
    **{("member", field.name): field.kind for field in MEMBER_FIELDS},
}


# An ordinary field a census response does not answer (a Member it no longer
# presents), told apart from an answer of None (a removed value).
_ABSENT = object()


def same_answer(kind, answer, proposal):
    """Whether ``answer`` is the proposal's own submitted answer, canonically."""
    submitted = proposal.submitted_value
    return canonical_value(kind, answer) == canonical_value(kind, submitted)


def _answer(submission, entity, key, field):
    """A census response's answer for one ordinary field; a sentinel when absent."""
    if entity == "family":
        found = submission.answers["family"]
    else:
        found = submission.answers["members"].get(key) or {}
    return found.get(field, _ABSENT)


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
    census = census_submission(submission)
    indexed.update(_kept_still_answered(census))
    indexed.update(_shown_still_answered(census))
    indexed.update(
        {
            (row.entity_kind, row.entity_key, row.field): row
            for row in ProposedChange.objects.filter(submission=census)
        }
    )
    return indexed


def _kept_still_answered(census):
    """Ordinary changes kept from before ``census`` that it still answers.

    A Family that answers again unchanged keeps, with no new row, a change
    already Entered by hand or written to ParishSoft (but before the
    download shows it; #1683), and an open change ParishSoft already holds
    (strict phase order, #1895: it waits for its review and step). Either
    row stays on its earlier response, so the next response must still find
    it, as long as each later census response answered it the same way: the
    newest row for the key, from an earlier response, when it is entered,
    published or open and ``census``'s answer is its own. A different answer
    since then made a row of its own (newer), and an answer set back to
    ParishSoft's value made none (an open change it withdrew is closed),
    which this check tells apart. The census guard's predecessor lookup and
    the completeness check (frozen file 0160) find the same open change.
    Terminal requests are found by ``proposal_index``'s own preserved query;
    a New Member once entered is no longer presented.
    """
    if census is None:
        return {}
    newest = (
        ProposedChange.objects.filter(
            submission__family_id=census.family_id,
            submission__campaign_id=census.campaign_id,
            submission__mode=census.mode,
            submission__rehearsal_epoch_id=census.rehearsal_epoch_id,
            submission__family_version__lt=census.family_version,
            entity_kind__in=["family", "member"],
        )
        .order_by("entity_kind", "entity_key", "field", "-submission__family_version")
        .distinct("entity_kind", "entity_key", "field")
    )
    found = {}
    for row in newest:
        kind = ORDINARY_KINDS.get((row.entity_kind, row.field))
        # Skip a closed change unless it was entered (still in force).
        closed_for_good = (
            row.execution in CLOSED_EXECUTIONS
            and row.execution not in ENTERED_EXECUTIONS
        )
        if kind is None or closed_for_good:
            continue
        answer = _answer(census, row.entity_kind, row.entity_key, row.field)
        if answer is not _ABSENT and same_answer(kind, answer, row):
            found[(row.entity_kind, row.entity_key, row.field)] = row
    return found


def _shown_still_answered(census):
    """Changes a reviewed form showed that ``census`` left as shown (#1705).

    A Family that leaves a reviewed form's shown value as it was makes no
    new row: the change it showed stays in force on its earlier response
    (the review pipeline's reviewed rule 1). Such a response's
    ``shown_items`` name that change, so the next form finds it here. A
    change ``census`` answered differently (rule 2) has a row of its own on
    ``census``, which ``proposal_index`` lets win; one ``census`` set back
    to ParishSoft's value (rule 3) is left out, so an entered one that the
    Family withdrew is not shown again. An Ignored change the form showed
    as the baseline stays Ignored whatever the answer that made no row.
    """
    if census is None or not census.reviewed_view:
        return {}
    from .reviewed import (
        CENSUS,
        census_answer,
        census_key,
        census_rows,
        entries_of,
        left_as_shown,
    )

    entries = entries_of(census.shown_items, CENSUS)
    if not entries:
        return {}
    rows, reviews = census_rows(entries)
    found = {}
    for entry in entries:
        row = rows.get(entry["item"])
        if row is None or row.execution in {"cancelled", "superseded"}:
            continue
        if entry["shown"] != "baseline":
            answer = census_answer(census.answers, *census_key(entry))
            review = reviews.get(entry["decision"] or "")
            # The baseline is not needed for a value the form showed.
            if not left_as_shown(entry, row, review, KnownValue(False), answer):
                continue
        found[census_key(entry)] = row
    return found


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
