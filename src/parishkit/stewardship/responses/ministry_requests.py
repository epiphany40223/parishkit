"""Family Ministry request derivation and effective visible-choice projection."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.workflows.models import MinistryRequest

from .effective import RESOLVED_EXECUTIONS, census_submission, proposal_index

ACTIONABLE_STATES = frozenset({"new", "assigned", "in_progress"})
# A request with a decision: a person or a refresh resolved it, or it closed
# with no response. A Family asking for it again leaves it as it is (#1684).
DECIDED_STATES = frozenset({"resolved", "closed_no_response"})


def request_index(submission):
    """Latest per-key history in one Family/mode/epoch, including closed outcomes.

    Hidden Ministries can retain intent on an older response. Filtering state
    before selecting the latest record would resurrect cancelled predecessors.
    """
    require_work_order()
    if submission is None:
        return {}
    rows = (
        MinistryRequest.objects.filter(
            submission__family_id=submission.family_id,
            submission__campaign_id=submission.campaign_id,
            submission__mode=submission.mode,
            submission__rehearsal_epoch_id=submission.rehearsal_epoch_id,
            submission__family_version__lte=submission.family_version,
        )
        .order_by(
            "entity_kind", "entity_key", "ministry_duid", "-submission__family_version"
        )
        .distinct("entity_kind", "entity_key", "ministry_duid")
    )
    return {(row.entity_kind, row.entity_key, row.ministry_duid): row for row in rows}


def ministry_presentation(
    inputs,
    prior,
    *,
    terminal_members,
    proposed_members,
    unavailable_members=frozenset(),
    shown=None,
):
    """Expose only currently offered labels and unresolved authorized choices.

    ``shown`` is a reviewed form's Ministry shown items (#1705), or None for
    every other form. A reviewed form draws each choice its shown items
    name (a request To review, accepted, edited or added by the parish, on
    the Ministry it carries forward); before the reviewed form only open
    requests are drawn, with the Family's own Ministry.
    """
    require_work_order()
    if inputs is None:
        return None
    if shown is not None:
        from .reviewed import ministry_draw, ministry_rows

        rows, revisions = ministry_rows(shown)
        drawn = ministry_draw(shown, rows, revisions)
    previous = request_index(prior) if shown is None else {}
    result = {
        "options": [
            {"id": option.duid, "name": option.name} for option in inputs.options
        ],
        "members": {},
        "proposed_members": {},
    }
    offered = frozenset(option.duid for option in inputs.options)
    for entity, key, current in [
        ("member", str(key), frozenset(values)) for key, values in inputs.memberships
    ] + [("proposed_member", key, frozenset()) for key in sorted(proposed_members)]:
        if entity == "member" and key in unavailable_members:
            continue
        choices = {"join": [], "leave": []} if entity == "member" else {"join": []}
        if shown is not None:
            terminal = entity == "member" and key in terminal_members
            for action, ministries in drawn.get((entity, key), {}).items():
                if not terminal and action in choices:
                    choices[action] = sorted(ministries)
        for ministry in sorted(offered):
            old = previous.get((entity, key, ministry))
            if (
                old is None
                or old.state not in ACTIONABLE_STATES
                or (entity == "member" and key in terminal_members)
            ):
                continue
            # Already-current requested roster state is no longer a pending UI
            # choice, even if an owning reconciliation has not yet marked it.
            if (old.action == "join") != (ministry in current):
                choices[old.action].append(ministry)
        result["members" if entity == "member" else "proposed_members"][key] = {
            "current": sorted(current),
            **choices,
        }
    return result


def derive_ministry_requests(submission, validated):
    """Derive complete visible intent without treating hidden omission as refusal.

    Same-intent work keeps its workflow state and links its predecessor. Source
    scope loss does not withdraw old requests. A confirmed terminal choice or
    explicit removal of a proposed Member does withdraw their visible choices;
    local activity/catalog hiding still preserves historical work.
    """
    require_work_order()
    inputs = validated.current.ministries
    if inputs is None:
        return []
    previous = request_index(validated.prior_submission)
    created = []
    # A reviewed form's shown requests (#1705) are judged first; the choices
    # they account for are left out of the derivation below.
    answers = submission.answers["ministries"]
    if submission.reviewed_view:
        answers = _derive_reviewed(submission, previous, created)
    for group, entity in (
        ("members", "member"),
        ("proposed_members", "proposed_member"),
    ):
        for key, choices in answers[group].items():
            for action, ministries in choices.items():
                for ministry in ministries:
                    identity = (entity, key, ministry)
                    old = previous.pop(identity, None)
                    if old and old.state in DECIDED_STATES and old.action == action:
                        # The Family asked again for a request already
                        # decided (accepted, ignored or confirmed): it keeps
                        # its decision and no second request is made, and its
                        # worklist row says so (review pipeline, "Resubmission
                        # while the setting is off", rule 1; #1684). The
                        # submission's completeness guard admits the decided
                        # request in this choice's place.
                        continue
                    same = bool(
                        old and old.state in ACTIONABLE_STATES and old.action == action
                    )
                    new = MinistryRequest.objects.create(
                        submission=submission,
                        entity_kind=entity,
                        entity_key=key,
                        ministry_duid=ministry,
                        action=action,
                        state=old.state if same else "new",
                        # Staff work survives a same-intent resubmission; its
                        # notes and contacts stay with the superseded history.
                        assignee_id=old.assignee_id if same else None,
                        actor_id=submission.family_id,
                    )
                    created.append(new)
                    if old and old.state in ACTIONABLE_STATES:
                        MinistryRequest.objects.filter(pk=old.pk).update(
                            state="superseded",
                            superseded_by=new,
                            version=F("version") + 1,
                        )
    visible = frozenset(option.duid for option in inputs.options)
    members = frozenset(str(key) for key, _ in inputs.memberships)
    census_prior = (
        census_submission(validated.prior_submission)
        if "census" in validated.current.modules
        else None
    )
    # Census derivation runs first and may already have cancelled/superseded an
    # explicitly removed UUID. Its immutable preceding answer proves it was in
    # scope; completed manual work does not, because that person is no longer
    # presented. Mirror this proof in the SQL replacement/completeness guards.
    proposed = {
        key
        for (entity, key, field), row in proposal_index(census_prior).items()
        if entity == "proposed_member"
        and field == "new_member"
        and key in census_prior.answers["proposed_members"]
        and row.execution not in RESOLVED_EXECUTIONS
    }
    for old in previous.values():
        if old.state not in ACTIONABLE_STATES or old.ministry_duid not in visible:
            continue
        if old.entity_kind == "member" and old.entity_key not in members:
            continue
        # Proposed identities are editable only with census. Module omission is
        # not an instruction to discard an older local Member's workflow.
        if old.entity_kind == "proposed_member" and old.entity_key not in proposed:
            continue
        MinistryRequest.objects.filter(pk=old.pk).update(
            state="cancelled", version=F("version") + 1
        )
    return created


def _create(submission, entity, key, ministry, action):
    """A new request To review for one of ``submission``'s choices."""
    return MinistryRequest.objects.create(
        submission=submission,
        entity_kind=entity,
        entity_key=key,
        ministry_duid=ministry,
        action=action,
        state="new",
        actor_id=submission.family_id,
    )


def _close(request, state, successor=None):
    """Withdraw (``cancelled``) or replace (``superseded``) a request in force.

    A decided request keeps no outcome or Edit once closed, as the table
    requires; its guard clears ``resolved_at`` (frozen file 0154), and its
    revisions keep the decision's history.
    """
    MinistryRequest.objects.filter(pk=request.pk).update(
        state=state,
        superseded_by=successor,
        outcome=None,
        reviewed_ministry_duid=None,
        version=F("version") + 1,
    )


def _derive_reviewed(submission, previous, created):
    """Judge a reviewed form's shown requests; return the choices left to derive.

    Each request the form showed (``reviewed.ministry_entries``) is compared
    with what the form showed for it (review pipeline spec, "Family
    resubmission"): its action on the Ministry it carries forward, chosen,
    or for an Ignored one "not selected" or "Continue in this ministry".

    1. Left as shown: the request keeps its stage and decision; no row.
    2. Changed again: re-choosing an Ignored request, or choosing the
       Family's own Ministry while an edit into another is in force, reopens
       it as a new request To review that replaces it. A carried-forward
       Ministry still chosen beside it is a request of its own (rule 4). A
       request already Entered in ParishSoft is not replaced: it stays for
       the refresh to judge, beside the new one.
    3. Withdrawn: unticking a chosen request (or choosing to continue)
       withdraws it, whatever its stage, except one Entered in ParishSoft,
       which the refresh judges, and one the parish added, which keeps its
       decision until Staff decide. Both rows say the Family undid it
       (``reports.ministry_worklist``).

    A Member the response does not answer (a terminal request, a Member
    out of scope) is not presented, so their requests are left to the
    derivation that follows. ``previous`` loses each request judged here,
    so the final withdrawal pass leaves it alone; ``created`` gains each new
    request. The SQL completeness check (frozen file 0154) admits a choice
    with no row only where its shown request is still in force.
    """
    from .reviewed import (
        MINISTRY,
        _entered,
        entries_of,
        ministry_key,
        ministry_rows,
        ministry_shown,
    )

    entries = entries_of(submission.shown_items, MINISTRY)
    rows, revisions = ministry_rows(entries)
    entered = _entered(rows)
    remaining = {
        group: {
            key: {action: set(ids) for action, ids in choices.items()}
            for key, choices in members.items()
        }
        for group, members in submission.answers["ministries"].items()
    }
    for entry in entries:
        request = rows.get(entry["item"])
        group = "members" if entry["entity"] == "member" else "proposed_members"
        choices = remaining[group].get(entry["key"])
        if request is None or request.state in {"cancelled", "superseded"}:
            continue
        if choices is None:
            continue
        previous.pop(ministry_key(entry), None)
        selected = choices.get(entry["action"], set())
        own = request.ministry_duid
        shown = ministry_shown(entry, request, revisions.get(entry["decision"] or ""))
        if (shown is None or shown != own) and own in selected:
            # Rule 2: an Ignored request chosen again, or the Family's own
            # Ministry chosen while an edit into another is in force.
            selected.discard(own)
            new = _create(
                submission, entry["entity"], entry["key"], own, request.action
            )
            created.append(new)
            # One Staff entered in ParishSoft stays in the history for the
            # refresh to judge; only the new request is To review.
            if str(request.pk) not in entered:
                _close(request, "superseded", new)
        elif shown is None:
            continue
        elif shown in selected:
            selected.discard(shown)
        elif not request.parish_added and str(request.pk) not in entered:
            _close(request, "cancelled")
    return {
        group: {
            key: {action: sorted(ids) for action, ids in choices.items()}
            for key, choices in members.items()
        }
        for group, members in remaining.items()
    }
