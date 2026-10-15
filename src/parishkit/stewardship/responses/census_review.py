"""An Administrator's review of Automatic census changes (#528, ADM-09).

An Automatic change (a Member field the handling registry marks
API-writable) reaches ParishSoft only through publication, and publication
plans only changes an Administrator approved (``responses/publication.py``).
Here an Administrator records **Approve**, **Ignore** or **Back to To
review** on one change, with an optional note. Each is one immutable
``ProposalReview`` row (who, when, the note and the resulting review state)
plus the proposal's new decision, written under the shared work order. SQL
independently checks the actor, the campaign, the version, the starting
state and the pairing with the proposal and the audit event
(``schema/migrations/0096_proposal_review.sql``), so a forged or stale
request is refused even if this code were bypassed.

Every review advances the proposal's version, so a publication plan that
included the change no longer matches once it is reviewed again
(``publication_plans.matches_confirmed`` and the confirmation guard).
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

from .census_resolution import MAX_NOTE, by_hand
from .models import REVIEW_DECISIONS, ProposalReview, ProposedChange

# A review may start only while the change is open: never while it is queued
# for publication (the publish worker owns it then) or once it is final.
REVIEWABLE = frozenset({"pending", "conflict", "failed"})


def reviewable(proposal, decision):
    """Whether an Administrator may give the proposal ``decision`` now.

    Only an Automatic change (the complement of ``by_hand``, without the
    report-only rows the worklist leaves out), only while it is open, and
    only to a decision different from its current one. Approving waits
    while ParishSoft is in conflict: the planner blocks an approved conflict
    anyway, and resolving it comes first.
    """
    if (
        by_hand(proposal)
        or proposal.execution not in REVIEWABLE
        or decision not in REVIEW_DECISIONS
        or decision == proposal.decision
    ):
        return False
    return decision != "approved" or proposal.execution != "conflict"


def review_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, decision, note
):
    """Record one review and its result, replay-safe, under the work order.

    The same ``request_key`` with the same intent returns the first review
    unchanged (a repeated submission); with a different intent it is
    refused. A proposal changed since the page was drawn is refused by
    ``check_version``, so the Administrator sees its current state first.
    """
    if (
        any(
            not isinstance(value, UUID)
            for value in (actor_id, proposal_id, request_key)
        )
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or decision not in REVIEW_DECISIONS
        or not isinstance(note, str)
        or len(note) > MAX_NOTE
    ):
        raise ValueError("Invalid census change review.")
    bounded_text(note)
    intent = dict(
        proposal_id=proposal_id,
        expected_version=expected_version,
        decision=decision,
        note=note,
    )
    with work_transaction():
        if not allows(current_principal(store, actor_id), Capability.PUBLISH_CENSUS):
            raise PermissionError("Census change review is unavailable.")
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        previous = ProposalReview.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if any(getattr(previous, key) != value for key, value in intent.items()):
                raise ValueError("This request key is already bound.")
            return previous
        check_version(proposal, expected_version)
        if not reviewable(proposal, decision):
            raise ValueError("This census change cannot be reviewed that way now.")
        # The row records the whole resulting review state; there are no
        # edits yet, so the edit fields stay as the proposal has them.
        review = ProposalReview.objects.create(
            actor_id=actor_id,
            request_key=request_key,
            admin_value_set=proposal.admin_value_set,
            admin_value=proposal.admin_value,
            **intent,
        )
        ProposedChange.objects.filter(pk=proposal_id).update(
            version=F("version") + 1, decision=decision
        )
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        # The audit names the review and the versions, never the note.
        record_action(
            Action.CENSUS_CHANGE_UPDATED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor_id,
            subject_id=review.pk,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=proposal.submission.campaign_id,
            context={
                "outcome": Outcome.CHANGED,
                "before_version": expected_version,
                "after_version": expected_version + 1,
            },
        )
        return review
