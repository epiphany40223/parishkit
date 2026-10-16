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

An Administrator may also give several changes one decision at once
(``review_census_changes``): each is reviewed exactly as one is, all or
none, under one work order.
"""

from uuid import UUID, uuid5

from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version
from parishkit.stewardship.web.tables import PAGE_SIZES

from .census_resolution import MAX_NOTE, by_hand
from .models import REVIEW_DECISIONS, ProposalReview, ProposedChange

# A review may start only while the change is open: never while it is queued
# for publication (the publish worker owns it then) or once it is final.
REVIEWABLE = frozenset({"pending", "conflict", "failed"})
# The most changes one bulk review may cover: the largest page of the list
# (its selection is one page), as for the Ministry follow-up queue's bulk
# outcomes. It also keeps the post well inside DATA_UPLOAD_MAX_NUMBER_FIELDS.
# A page showing All rows offers bulk review only while it holds no more.
MAX_BULK = max(PAGE_SIZES)


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


class KeyRebound(ValueError):
    """A request key already recorded a different review; nothing was saved.

    It happens only when a page's answer was lost and the same drawn form
    (or bulk key) is sent again with other choices, so the page offers a
    reload rather than a generic refusal.
    """


def one_campaign(proposals):
    """The one campaign every proposal of a bulk review belongs to.

    A bulk review is admitted and audited against a single campaign, so a
    batch mixing campaigns (only a crafted post) is refused.
    """
    campaigns = {proposal.submission.campaign_id for proposal in proposals}
    if len(campaigns) != 1:
        raise ValueError("A bulk review covers one campaign.")
    return campaigns.pop()


def _valid_note(note):
    """Refuse a note that is not bounded plain text."""
    if not isinstance(note, str) or len(note) > MAX_NOTE:
        raise ValueError("Invalid census change review.")
    bounded_text(note)


def _record(proposal, actor_id, expected_version, request_key, decision, note, parish):
    """Record one locked proposal's review, its result and its audit event.

    The caller holds the work order and the proposal's row lock, and has
    admitted its campaign. The same ``request_key`` with the same intent
    returns the first review unchanged (a repeated submission); with a
    different intent it is refused. A proposal changed since the page was
    drawn is refused by ``check_version``, before the review rules, so the
    Administrator is told the row changed rather than that it cannot be
    reviewed.
    """
    intent = dict(
        proposal_id=proposal.pk,
        expected_version=expected_version,
        decision=decision,
        note=note,
    )
    previous = ProposalReview.objects.filter(
        actor_id=actor_id, request_key=request_key
    ).first()
    if previous is not None:
        if any(getattr(previous, key) != value for key, value in intent.items()):
            raise KeyRebound("This request key is already bound.")
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
    ProposedChange.objects.filter(pk=proposal.pk).update(
        version=F("version") + 1, decision=decision
    )
    # The audit names the review and the versions, never the note.
    record_action(
        Action.CENSUS_CHANGE_UPDATED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=review.pk,
        parish_id=parish,
        campaign_id=proposal.submission.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": expected_version,
            "after_version": expected_version + 1,
        },
    )
    return review


def _parish_id():
    """The active parish, which every review's audit event names."""
    return (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish.pk
    )


def _authorize(store, actor_id):
    """Only a current Administrator reviews; checked inside the work order."""
    if not allows(current_principal(store, actor_id), Capability.PUBLISH_CENSUS):
        raise PermissionError("Census change review is unavailable.")


def review_census_change(
    store, actor_id, proposal_id, *, expected_version, request_key, decision, note
):
    """Record one review and its result, replay-safe, under the work order.

    See ``_record`` for replays and stale versions.
    """
    if (
        any(
            not isinstance(value, UUID)
            for value in (actor_id, proposal_id, request_key)
        )
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or decision not in REVIEW_DECISIONS
    ):
        raise ValueError("Invalid census change review.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposal = (
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=proposal_id, submission__mode="live")
        )
        admit_campaign(proposal.submission.campaign_id, mutating=True)
        return _record(
            proposal,
            actor_id,
            expected_version,
            request_key,
            decision,
            note,
            _parish_id(),
        )


class BulkStale(Exception):
    """A chosen change was updated since the page was drawn; nothing was saved."""

    def __init__(self, proposal_id):
        super().__init__("A chosen census change was updated by someone else.")
        self.proposal_id = proposal_id


def bulk_key_for(bulk_key, proposal_id):
    """One change's request key within a bulk review, derived from the page's.

    The page draws one key for its bulk bar; each change's review takes its
    own key from it, so posting the same bulk review again replays each
    change's review instead of writing a second one (as the Ministry
    follow-up queue's bulk outcomes do, #1131).
    """
    return uuid5(bulk_key, str(proposal_id))


def review_census_changes(store, actor_id, versions, *, decision, note, bulk_key):
    """Give several changes one decision, all or none (#528, ADM-09).

    ``versions`` maps each proposal id to the version its row showed. Every
    change is reviewed exactly as ``review_census_change`` reviews one (its
    own review row, result and ``census_change_updated`` event, which 0096's
    SQL checks one by one), in one work transaction with every proposal
    locked in primary-key order first, so a bulk review never deadlocks
    with another or with the publish worker, which locks in the same order.
    The chosen changes must all belong to one campaign. One that changed
    since the page was drawn raises ``BulkStale`` naming it; any other
    refusal is a ValueError. Nothing is saved then. Returns the reviews in
    primary-key order.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(bulk_key, UUID)
        or decision not in REVIEW_DECISIONS
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or any(
            not isinstance(key, UUID)
            or type(value) is not int
            or not 1 <= value < 2**63 - 1
            for key, value in versions.items()
        )
    ):
        raise ValueError("Invalid bulk census change review.")
    _valid_note(note)
    with work_transaction():
        _authorize(store, actor_id)
        proposals = list(
            ProposedChange.objects.select_for_update(of=("self",))
            .select_related("submission")
            .filter(pk__in=list(versions), submission__mode="live")
            .order_by("pk")
        )
        if len(proposals) != len(versions):
            raise ProposedChange.DoesNotExist("A chosen census change is unavailable.")
        admit_campaign(one_campaign(proposals), mutating=True)
        parish = _parish_id()
        reviews = []
        for proposal in proposals:
            try:
                reviews.append(
                    _record(
                        proposal,
                        actor_id,
                        versions[proposal.pk],
                        bulk_key_for(bulk_key, proposal.pk),
                        decision,
                        note,
                        parish,
                    )
                )
            except StaleRecordError:
                raise BulkStale(proposal.pk) from None
        return reviews
