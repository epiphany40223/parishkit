"""Family Ministry request derivation and effective visible-choice projection."""

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.workflows.models import MinistryRequest

from .effective import RESOLVED_EXECUTIONS, census_submission, proposal_index

ACTIONABLE_STATES = frozenset({"new", "assigned", "in_progress"})


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
):
    """Expose only currently offered labels and unresolved authorized choices."""
    require_work_order()
    if inputs is None:
        return None
    previous = request_index(prior)
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
    for group, entity in (
        ("members", "member"),
        ("proposed_members", "proposed_member"),
    ):
        for key, choices in submission.answers["ministries"][group].items():
            for action, ministries in choices.items():
                for ministry in ministries:
                    identity = (entity, key, ministry)
                    old = previous.pop(identity, None)
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
