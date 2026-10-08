"""Staff and Admin resolution of By-hand census changes (#528, step 3).

A census change that publication cannot carry into ParishSoft is entered by
hand. Staff and Administrators record that here (**Entered in ParishSoft**),
or that it will not be entered (**Ignore**), each with an optional note; an
Administrator may reopen an ignored change. Each action is one immutable
``ProposalResolution`` row (who, when, the note) plus the proposal's
resulting state, written under the shared work order. SQL independently
checks the actor, the campaign, the version, the starting state and the
pairing with the proposal and the audit event
(``schema/migrations/0026_census_resolution.sql``), so a forged or stale
request is refused even if this code were bypassed.
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

from .models import RESOLUTION_ACTIONS, ProposalResolution, ProposedChange

MAX_NOTE = 2000
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


def resolve_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, action, note
):
    """Record one resolution and its result, replay-safe, under the work order.

    The same ``request_key`` with the same intent returns the first
    resolution unchanged (a repeated submission); with a different intent it
    is refused. A proposal changed since the page was drawn is refused by
    ``check_version``, so the person sees its current state first.
    """
    if (
        any(
            not isinstance(value, UUID)
            for value in (actor_id, proposal_id, request_key)
        )
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or action not in RESOLUTION_ACTIONS
        or not isinstance(note, str)
        or len(note) > MAX_NOTE
        # Undoing an Ignore says why; SQL requires the same.
        or (action == "reopened" and not note.strip())
    ):
        raise ValueError("Invalid census change resolution.")
    bounded_text(note)
    intent = dict(
        proposal_id=proposal_id,
        expected_version=expected_version,
        action=action,
        note=note,
    )
    with work_transaction():
        if not allows(current_principal(store, actor_id), capability(action)):
            raise PermissionError("Census change resolution is unavailable.")
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        previous = ProposalResolution.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if any(getattr(previous, key) != value for key, value in intent.items()):
                raise ValueError("This request key is already bound.")
            return previous
        check_version(proposal, expected_version)
        if not allowed(proposal, action):
            raise ValueError("This census change cannot be resolved that way now.")
        resolution = ProposalResolution.objects.create(
            actor_id=actor_id, request_key=request_key, **intent
        )
        change = (
            {"execution": "resolved_external"}
            if action == "entered"
            else {"decision": "ignored" if action == "ignored" else "unreviewed"}
        )
        ProposedChange.objects.filter(pk=proposal_id).update(
            version=F("version") + 1, **change
        )
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        # The audit names the resolution and the versions, never the note.
        record_action(
            Action.CENSUS_CHANGE_UPDATED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=resolution.pk,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=proposal.submission.campaign_id,
            context={
                "outcome": Outcome.CHANGED,
                "before_version": expected_version,
                "after_version": expected_version + 1,
            },
        )
        return resolution
