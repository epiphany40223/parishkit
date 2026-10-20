"""Staff and Admin resolution of By-hand census changes (#528, step 3).

A census change that publication cannot carry into ParishSoft is entered by
hand. Staff and Administrators record that here (**Entered in ParishSoft**),
or that it will not be entered (**Ignore**), each with an optional note; an
Administrator may reopen an ignored change. Each action is one immutable
``ProposalResolution`` row (who, when, the note) plus the proposal's
resulting state, written under the shared work order. SQL independently
checks the actor, the campaign, the version, the starting state and the
pairing with the proposal and the audit event
(``schema/migrations/0060_census_resolution.sql``), so a forged or stale
request is refused even if this code were bypassed.

Since the review pipeline (#1683) the Census changes worklist records only
**Entered by hand** here (on a To review By hand change it accepts and marks
in one step); its Ignore and Back to review are reviews
(``census_review``). The ``ignored`` and ``reopened`` actions remain for
``pk-admin census resolve``.
"""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version

from .models import (
    RESOLUTION_ACTIONS,
    CensusAddition,
    ProposalResolution,
    ProposedChange,
)

MAX_NOTE = 2000


class KeyRebound(ValueError):
    """A request key already recorded a different review or resolution.

    Nothing was saved. It happens only when a page's answer was lost and the
    same drawn form (or bulk key) is sent again with other choices, so the
    page offers a reload rather than a generic refusal (a 409).
    """


class ReopenCovered(ValueError):
    """An ignored change an open parish addition covers cannot be reopened.

    The addition stands in for it (#1710): reopening it would leave two
    open changes of one field. Nothing was saved; withdraw the addition
    first.
    """


def covered_by_addition(proposal):
    """Whether an open parish addition covers ``proposal``'s field (#1710).

    The same Family, Member (or Family) and field, with no later step.
    The callers hold the work order, so no Add can slip in between this
    check and their write.
    """
    return CensusAddition.objects.filter(
        family_id=proposal.submission.family_id,
        entity_kind=proposal.entity_kind,
        entity_key=proposal.entity_key,
        field=proposal.field,
        resolution__isnull=True,
    ).exists()


OPEN_EXECUTIONS = frozenset({"pending", "conflict"})


def by_hand(proposal):
    """Whether a person, not publication, carries this change into ParishSoft.

    Every change the handling registry does not mark API-writable, and every
    Family field: no verified ParishSoft address read exists yet. The same
    rule is the worklist's By hand (``reports.census_changes.automatic``).
    """
    return proposal.handling != "api" or proposal.entity_kind == "family"


def allowed(proposal, action):
    """Whether ``action`` may start from the proposal's current state."""
    if proposal.execution not in OPEN_EXECUTIONS or proposal.handling == "report-only":
        return False
    if action == "reopened":
        return proposal.decision == "ignored"
    return by_hand(proposal) and proposal.decision != "ignored"


def capability(action):
    """Reopening is the Administrator's; the tick and Ignore are Staff's too."""
    return (
        Capability.PUBLISH_CENSUS if action == "reopened" else Capability.MANUAL_CENSUS
    )


def _valid(action, note):
    """Whether ``action`` and ``note`` make a resolution; a reopen says why."""
    return (
        action in RESOLUTION_ACTIONS
        and isinstance(note, str)
        and len(note) <= MAX_NOTE
        # Undoing an Ignore says why; SQL requires the same.
        and not (action == "reopened" and not note.strip())
    )


def record_resolution(
    proposal, actor_id, expected_version, request_key, action, note, parish
):
    """Record one locked proposal's resolution, its result and its audit event.

    The caller holds the work order and the proposal's row lock, has
    checked the actor's capability and admitted the campaign; ``parish``
    is the active parish's id, which the audit event names. The same
    ``request_key`` with the same intent returns the first resolution
    unchanged (a repeated submission); with a different intent it is
    refused. A proposal changed since the page was drawn is refused by
    ``check_version``, so the person sees its current state first. The
    review worklist's bulk Entered by hand records each change through here
    (``census_review.decide_census_changes``, #1683).
    """
    intent = dict(
        proposal_id=proposal.pk,
        expected_version=expected_version,
        action=action,
        note=note,
    )
    previous = ProposalResolution.objects.filter(
        actor_id=actor_id, request_key=request_key
    ).first()
    if previous is not None:
        if any(getattr(previous, key) != value for key, value in intent.items()):
            raise KeyRebound("This request key is already bound.")
        return previous
    check_version(proposal, expected_version)
    if not allowed(proposal, action):
        raise ValueError("This census change cannot be resolved that way now.")
    if action == "reopened" and covered_by_addition(proposal):
        raise ReopenCovered("An open addition covers this census change.")
    resolution = ProposalResolution.objects.create(
        actor_id=actor_id, request_key=request_key, **intent
    )
    change = (
        {"execution": "resolved_external"}
        if action == "entered"
        else {"decision": "ignored" if action == "ignored" else "unreviewed"}
    )
    ProposedChange.objects.filter(pk=proposal.pk).update(
        version=F("version") + 1, **change
    )
    # The audit names the resolution and the versions, never the note.
    record_action(
        Action.CENSUS_CHANGE_UPDATED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=resolution.pk,
        parish_id=parish,
        campaign_id=proposal.submission.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": expected_version,
            "after_version": expected_version + 1,
        },
    )
    return resolution


def resolve_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, action, note
):
    """Record one resolution and its result, replay-safe, under the work order.

    See ``record_resolution`` for replays and stale versions.
    """
    if (
        any(
            not isinstance(value, UUID)
            for value in (actor_id, proposal_id, request_key)
        )
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or not _valid(action, note)
    ):
        raise ValueError("Invalid census change resolution.")
    bounded_text(note)
    with work_transaction():
        if not allows(current_principal(store, actor_id), capability(action)):
            raise PermissionError("Census change resolution is unavailable.")
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        return record_resolution(
            proposal,
            actor_id,
            expected_version,
            request_key,
            action,
            note,
            system.active_configuration.parish.pk,
        )
