"""Transactional census proposal derivation from immutable complete answers."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order

from .accepted_base import has_active_base
from .capabilities import proposal_handling
from .census import HOUSEHOLD_FIELDS
from .comparison import ValueKind, canonical_value
from .effective import (
    CLOSED_EXECUTIONS,
    ENTERED_EXECUTIONS,
    RESOLVED_EXECUTIONS,
    proposal_index,
)
from .inputs import FieldInput
from .merge import KnownValue, MergeState, PriorChange, merge_value
from .models import ProposedChange

# Terminal Member requests, whose latest row outlives later responses.
TERMINAL_FIELDS = frozenset({"moved_household", "deceased_status", "death_date"})


def derive_proposals(submission, validated):
    """Preserve unchanged review intent while replacing the effective aggregate.

    A same-intent pending proposal retains its original comparison baseline and
    decision, so a revisit cannot silently clear an upstream conflict or revive
    an ignored request. A genuinely edited field starts a new unreviewed intent.
    Published/resolved outcomes remain immutable; later requests get new rows,
    except the same answer again for a change already entered or published,
    which keeps that change and makes none (#1683), and the same answer again
    for an open change ParishSoft already holds, which also keeps it as it is
    (#1895). No immutable Family answer is edited, including to an
    Admin-corrected value, and no Family response resolves a change upstream.
    """
    require_work_order()
    if "census" not in validated.current.modules:
        # An unenabled census section cannot create or withdraw census work.
        return []
    if submission.reviewed_view:
        return _derive_reviewed(submission, validated)
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
        # The Family answered again as before for a change already Entered
        # by hand or written to ParishSoft, which the download may not show
        # yet: it stays as it is, with no new row (the review pipeline's
        # resubmission rule, #1683), rather than a second To review item
        # for work already done.
        # Terminal requests (moved, deceased, a death date) are left out: an
        # entered one is no longer presented, so asking for it again is a
        # new request, not the Family's previous answer.
        entered = (
            old is not None
            and field.field not in TERMINAL_FIELDS
            and old.execution in ENTERED_EXECUTIONS
            and canonical_value(field.kind, old.submitted_value) == key
        )
        if not unchanged and not entered:
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
            decision = old.decision if carry_decision else "unreviewed"
            if carry_decision and decision == "approved" and has_active_base(old):
                # The Administrator approved it over a ParishSoft value they
                # accepted (#528, ADM-09.03). That base belongs to the old
                # row and is not carried, so neither is the approval: the
                # new row is compared with the Family's baseline again.
                decision = "unreviewed"
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
                # Per field and per value: an answer that clears a value is
                # By hand, as the API cannot clear (#1596); SQL checks it.
                handling=proposal_handling(field.entity, field.field, submitted).value,
                decision=decision,
                admin_value_set=old.admin_value_set if carry_decision else False,
                admin_value=old.admin_value if carry_decision else None,
                execution="conflict"
                if merge.state is MergeState.CONFLICT
                else "pending",
                actor_id=submission.family_id,
            )
            created.append(new)
        # ParishSoft has a value and it is the answer (not an unknown value).
        held = unchanged and field.source.available
        outcome = _replacement(old, new, held, field.kind, key)
        if outcome is not None:
            ProposedChange.objects.filter(pk=old.pk).update(
                execution=outcome, superseded_by=new, version=F("version") + 1
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


def _replacement(old, new, held, kind, key):
    """What a Family response does to ``old``, the open change before it.

    ``new`` is the response's own row for the field (None when it made
    none), ``held`` whether its answer, canonically ``key``, equals the
    value ParishSoft has. ``None`` leaves ``old`` as it is: nothing is open,
    or the answer is ``old``'s own and ParishSoft already holds it, so rule
    1 (unchanged) keeps its stage and decision until its review and step
    (strict phase order, #1895; it was resolved upstream, Confirmed at
    once, before). Otherwise a new row supersedes it, and an answer set
    back to ParishSoft's value withdraws it (cancelled); the census guard
    (frozen file 0160) refuses any other outcome.
    """
    if old is None or old.execution in CLOSED_EXECUTIONS:
        return None
    if new is None and held and canonical_value(kind, old.submitted_value) == key:
        return None
    return "superseded" if new else "cancelled"


def _answered_fields(submission, validated):
    """Each census field ``submission`` answers, with its answer, in form order.

    The same fields ``derive_proposals`` reads: the Family's household
    fields, each Member field the Member's answer gives (a terminal answer
    omits the ordinary ones; a deceased Member's unknown death date is no
    answer), and each New Member.
    """
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
        if field.entity == "family":
            yield field, submission.answers["family"][field.field]
        elif field.entity == "proposed_member":
            yield field, submission.answers["proposed_members"][field.identity]
        else:
            answer = submission.answers["members"][str(field.identity)]
            if field.field not in answer or (
                field.field == "death_date" and answer[field.field] is None
            ):
                continue
            yield field, answer[field.field]


def _derive_reviewed(submission, validated):
    """Census work for a response whose form was drawn as the reviewed form (#1705).

    Each item is compared with what the form showed for it (its shown
    item), not with the Family's earlier answer (review pipeline spec,
    "Family resubmission"):

    1. left as shown: the change in force keeps its stage and decision,
       even one recorded after the form was drawn, and no row is made (an
       Ignored change shown as the baseline stays Ignored);
    2. changed again: a new change To review, compared with the baseline at
       this submit; an open earlier change is replaced, an entered or
       updated one stays in history for the refresh to judge;
    3. set back to ParishSoft's value: no row, and an open earlier change is
       withdrawn, also when ParishSoft already holds its answer (#1895);
    4. new: a change To review.

    A field whose form showed a change the parish added (#2008) is left as
    shown when the answer is the added value: no row, and the addition and
    any earlier change of the field (an Ignored one it was added in place
    of) stay as they are. Any other answer is always a new change To
    review (rule 2), even ParishSoft's value or the Ignored change's own:
    it replaces an open earlier change, and the addition is never touched.
    The submission check and the derived guard (frozen file 0170) admit and
    require the same.

    No decision is ever carried to a new row. A field the form did not
    present is left as before the reviewed form: an earlier open change of
    an answered Member is withdrawn by the response's own completeness, and
    a hidden Ignored New Member or Ignored request stays Ignored (rule 1 by
    absence). The SQL completeness check (frozen file 0153) admits each
    absence only where ``reviewed.left_as_shown`` holds.
    """
    from parishkit.stewardship.pipeline import Shown

    from .reviewed import (
        CENSUS,
        census_additions,
        census_key,
        census_rows,
        entries_of,
        left_as_shown,
        parish_left_as_shown,
    )

    previous = proposal_index(validated.prior_submission)
    resolved_semantics = {
        key
        for (entity, key, name), row in previous.items()
        if entity == "member"
        and name == "deceased_status"
        and row.execution in RESOLVED_EXECUTIONS
    }
    census = entries_of(submission.shown_items, CENSUS)
    # A field may have two entries (#2008): a change the parish added and
    # the change beside it (the Ignored one it was added in place of).
    entries = {
        census_key(entry): entry for entry in census if entry["shown"] != Shown.PARISH
    }
    parish = {
        census_key(entry): entry for entry in census if entry["shown"] == Shown.PARISH
    }
    rows, reviews = census_rows(list(entries.values()))
    additions = census_additions(list(parish.values()))
    # The baseline the form showed an Ignored value from: its own snapshot.
    shown_sources = {
        (field.entity, str(field.identity), field.field): field.source
        for field in validated.reviewed.fields
    }
    created = []
    for field, submitted in _answered_fields(submission, validated):
        identity = (field.entity, str(field.identity), field.field)
        old = previous.pop(identity, None)
        added = parish.get(identity)
        addition = additions.get(added["item"]) if added else None
        if addition is not None and parish_left_as_shown(added, addition, submitted):
            continue
        entry = entries.get(identity)
        shown = rows.get(entry["item"]) if entry else None
        if shown is not None and shown.execution not in {"cancelled", "superseded"}:
            review = reviews.get(entry["decision"] or "")
            source = shown_sources.get(identity, KnownValue(False))
            # Not under a drawn addition: the form showed the added value,
            # not the change beside it, so the Ignored change's own value
            # is an edit of the added one too.
            if addition is None and left_as_shown(
                entry, shown, review, source, submitted
            ):
                continue
            old = shown
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
        # An edited addition is always a change To review, even back to
        # ParishSoft's value (#2008, the Administrator's decision 2): Staff
        # see the Family turned the added value down. The derived guard
        # (frozen file 0170) admits that row, and the submission check
        # requires it.
        if not unchanged or addition is not None:
            merge = merge_value(
                field.kind, field.source, PriorChange(field.source, submitted)
            )
            new = ProposedChange.objects.create(
                submission=submission,
                entity_kind=field.entity,
                entity_key=str(field.identity),
                field=field.field,
                baseline_available=field.source.available,
                baseline_value=field.source.value,
                submitted_value=submitted,
                current_available=field.source.available,
                current_value=field.source.value,
                current_source_id=validated.validation_snapshot_id,
                handling=proposal_handling(field.entity, field.field, submitted).value,
                execution="conflict"
                if merge.state is MergeState.CONFLICT
                else "pending",
                actor_id=submission.family_id,
            )
            created.append(new)
        if old is not None and old.execution not in CLOSED_EXECUTIONS:
            # Rule 3 withdraws the change, even when ParishSoft already
            # holds its earlier answer: a Family response never confirms
            # one (strict phase order, #1895).
            ProposedChange.objects.filter(pk=old.pk).update(
                execution="superseded" if new else "cancelled",
                superseded_by=new,
                version=F("version") + 1,
            )
    hidden = {
        identity for identity, entry in entries.items() if entry["shown"] == "baseline"
    }
    _withdraw_unanswered(submission, previous, hidden, resolved_semantics)
    return created


def _withdraw_unanswered(submission, previous, hidden, resolved_semantics):
    """The final pass of ``_derive_reviewed`` over earlier changes not answered.

    As before the reviewed form, an unanswered ordinary change is withdrawn,
    and a terminal request or death date is kept where the Member is no
    longer presented. ``hidden`` are the items the form showed as the
    baseline by leaving them out (an Ignored New Member, an Ignored move
    or death shown as ParishSoft has the Member): their absence is rule 1,
    so they stay Ignored. ``resolved_semantics`` are the Members whose
    deceased request is already resolved, whose separate death date work
    an answer without a date keeps.
    """
    for identity, old in previous.items():
        if identity in hidden or old.execution in CLOSED_EXECUTIONS:
            continue
        if old.entity_kind == "member" and old.field in TERMINAL_FIELDS:
            answer = submission.answers["members"].get(old.entity_key)
            if answer is None or (
                old.field == "death_date"
                and old.entity_key in resolved_semantics
                and answer.get("death_date") is None
            ):
                continue
        ProposedChange.objects.filter(pk=old.pk).update(
            execution="cancelled", version=F("version") + 1
        )
