"""Staff review decisions on cannot-participate and cannot-attend answers (#1685).

Nothing is stored when a Family submits: an answer is the Family's effective
live response, as the Limitations report reads it. Each worklist decision
(Accept, Ignore, Back to review, Handled) is one immutable
``LimitationReview`` row naming the answer and the response it was decided
on, and stays in force while every later live response gives the same
answer (``stewardship_limitation_in_force_v1``,
schema/migrations/0126_information_limitation_review.sql). An answer's
version is the number of decisions recorded for it.

An answer is named on the page by ``answer_id``: its kind's letter, the
Family DUID and, for a Member, the answer's key (a Member DUID, or a
proposed Member's local id), short enough for the worklist's item ids. The
database's guard checks the same authority, response, version and step
again.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.pipeline import Action as Decide
from parishkit.stewardship.pipeline import (
    Stage,
    Stream,
    cannot_participate_placement,
    undo_needs_note,
    undo_stage,
)
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.web.content import bounded_text

from .census_review import KeyRebound, bulk_key_for
from .information import MAX_BULK, MAX_NOTE, ReopenNeedsNote, StaleItem, UndoNeedsNote
from .models import LimitationReview, Submission

# What each worklist decision records and the stages it is offered at
# (review pipeline spec, "Role-specific actions"). Back to review's record
# depends on the stage it leaves (``recorded``).
DECISIONS = {
    Decide.ACCEPT: ("accepted", {Stage.TO_REVIEW}),
    Decide.IGNORE: ("ignored", {Stage.TO_REVIEW}),
    Decide.BACK_TO_REVIEW: ("none", {Stage.TO_HANDLE, Stage.IGNORED, Stage.HANDLED}),
    Decide.HANDLED: ("handled", {Stage.TO_HANDLE}),
}
# What Back to review records for the stage it returns an answer to
# (``pipeline.undo_stage``): a decision's undo goes to To review; Handled's
# undo (#1840) removes only the mark, so the answer is To handle again,
# still accepted.
UNDO_DECISIONS = {Stage.TO_REVIEW: "none", Stage.TO_HANDLE: "accepted"}
KIND_LETTERS = {"member": "m", "proposed_member": "p", "family": "f"}
LETTER_KINDS = {letter: kind for kind, letter in KIND_LETTERS.items()}
# A Member DUID, or a proposed Member's local id (canonical UUID text).
ANSWER_ID = re.compile(
    r"(?:(?P<member>m)(?P<duid>[1-9][0-9]{0,17})-(?P<mkey>[1-9][0-9]{0,17})"
    r"|(?P<proposed>p)(?P<pduid>[1-9][0-9]{0,17})-"
    r"(?P<pkey>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"|(?P<family>f)(?P<fduid>[1-9][0-9]{0,17}))"
)


@dataclass(frozen=True, slots=True)
class Answer:
    """One answer the worklist names: its Family DUID, kind and Member key."""

    family_duid: int
    kind: str
    member_key: str = ""

    @property
    def id(self):
        """The answer's worklist item id (``answer_id``'s inverse)."""
        suffix = f"-{self.member_key}" if self.member_key else ""
        return f"{KIND_LETTERS[self.kind]}{self.family_duid}{suffix}"


def answer_id(family_duid, kind, member_key=""):
    """The worklist item id of one answer."""
    return Answer(int(family_duid), kind, member_key).id


def parse_answer(text):
    """The ``Answer`` an item id names; anything else is a ValueError."""
    found = ANSWER_ID.fullmatch(text) if isinstance(text, str) else None
    if found is None:
        raise ValueError("Invalid limitation item.")
    if found["member"]:
        return Answer(int(found["duid"]), "member", found["mkey"])
    if found["proposed"]:
        return Answer(int(found["pduid"]), "proposed_member", found["pkey"])
    return Answer(int(found["fduid"]), "family")


def answer_given(answers, kind, member_key):
    """Whether a response's answers give one answer (as SQL's answer check reads)."""
    if kind == "family":
        return answers.get("cannot_attend") is True
    group = "members" if kind == "member" else "proposed_members"
    entry = ((answers.get("service") or {}).get(group) or {}).get(member_key) or {}
    return entry.get("cannot_serve") is True


def limitation_stage(review):
    """An answer's placement from its latest review row (None before any)."""
    if review is None:
        return cannot_participate_placement()
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_limitation_in_force_v1(%s)", [review.pk])
        (in_force,) = cursor.fetchone()
    return cannot_participate_placement(decision=review.decision, in_force=in_force)


def recorded(action, placement):
    """The decision ``action`` records on an answer at ``placement``.

    Back to review records the stage ``pipeline.undo_stage`` returns the
    answer to (``UNDO_DECISIONS``); every other action one fixed decision.
    """
    if action is Decide.BACK_TO_REVIEW:
        return UNDO_DECISIONS[undo_stage(Stream.CANNOT_PARTICIPATE, placement)]
    return DECISIONS[action][0]


def records(action):
    """Every row ``action`` can store, as the fields a repeat must match.

    A replayed request key is checked against these, since the answer has
    moved on since its first post. Handled's undo stores ``accepted``, as
    Accept does; the row's version and answer still have to match, so a key
    replayed for another answer or version is still refused.
    """
    if action is Decide.BACK_TO_REVIEW:
        return [{"decision": decision} for decision in UNDO_DECISIONS.values()]
    return [{"decision": DECISIONS[action][0]}]


def authorize_limitations(store, actor_id):
    """Reload current policy: Staff and Administrators, never Ministry leaders."""
    principal = current_principal(store, actor_id)
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The Limitations report is unavailable.")
    return principal


def _latest(family, answer):
    """The answer's newest review row, or None."""
    return (
        LimitationReview.objects.filter(
            family_id=family.pk, kind=answer.kind, member_key=answer.member_key
        )
        .order_by("-expected_version")
        .first()
    )


def _decide_one(family, answer, actor_id, action, version, key, note, parish):
    """Record one worklist decision on one answer of a locked Family.

    A repeated post returns its first row when it recorded this same
    decision, and is ``KeyRebound`` otherwise. An answer the Family's
    current response no longer gives, or whose version or stage moved since
    the page was drawn, is ``StaleItem``.
    """
    previous = LimitationReview.objects.filter(
        actor_id=actor_id, request_key=key
    ).first()
    if previous is not None:
        if (
            (previous.family_id, previous.kind, previous.member_key)
            != (family.pk, answer.kind, answer.member_key)
            or previous.expected_version != version
            or {"decision": previous.decision} not in records(action)
        ):
            raise KeyRebound("This request key is already bound.")
        return previous
    response = Submission.objects.filter(
        pk=family.effective_submission_id, mode="live"
    ).first()
    if response is None or not answer_given(
        response.answers, answer.kind, answer.member_key
    ):
        raise StaleItem(answer.id)
    latest = _latest(family, answer)
    current = 0 if latest is None else latest.expected_version + 1
    placement = limitation_stage(latest)
    stage = placement.stage
    if current != version or stage not in DECISIONS[action][1]:
        raise StaleItem(answer.id)
    if (
        action is Decide.BACK_TO_REVIEW
        and undo_needs_note(Stream.CANNOT_PARTICIPATE, stage)
        and not note.strip()
    ):
        # Reopening an ignored answer, or undoing Handled, needs a reason
        # (review pipeline spec, "Reviewed stage", "Undoing a terminal
        # stage"); the dialog asks for it first.
        if stage is Stage.HANDLED:
            raise UndoNeedsNote("Undoing a handled answer needs a note.")
        raise ReopenNeedsNote("Reopening an ignored answer needs a note.")
    decision = recorded(action, placement)
    review = LimitationReview.objects.create(
        actor_id=actor_id,
        family_id=family.pk,
        submission_id=response.pk,
        kind=answer.kind,
        member_key=answer.member_key,
        expected_version=version,
        request_key=key,
        decision=decision,
        note=note,
    )
    record_action(
        Action.LIMITATION_REVIEWED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=review.pk,
        parish_id=parish,
        campaign_id=family.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": version,
            "after_version": version + 1,
        },
    )
    return review


def decide_limitations(
    store, actor_id, campaign_id, action, versions, *, note, request_key, bulk=False
):
    """Record one worklist decision on one or several answers, all or none.

    ``versions`` maps each answer's item id (``answer_id``) to the version
    its row showed; ``request_key`` is the row's key, or for ``bulk`` the
    page's bulk key, from which each answer's own key is derived, so a
    repeated post records nothing more. Every answer's Family must be one of
    ``campaign_id``'s; the Families are locked in primary-key order in one
    work transaction (as their submissions lock them), and each answer is
    recorded as its own icon would record it. An answer someone (another
    reviewer, or the Family answering again) changed first raises
    ``StaleItem``, a rebound key ``KeyRebound``; nothing is saved then.
    Returns the rows.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(request_key, UUID)
        or action not in DECISIONS
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or (not bulk and len(versions) != 1)
        or any(
            type(version) is not int or not 0 <= version < 2**63 - 1
            for version in versions.values()
        )
        or not isinstance(note, str)
        or len(note) > MAX_NOTE
    ):
        raise ValueError("Invalid limitation decision.")
    bounded_text(note)
    answers = {parse_answer(item): version for item, version in versions.items()}
    with work_transaction():
        authorize_limitations(store, actor_id)
        families = {
            family.family_duid: family
            for family in FamilyCampaign.objects.select_for_update(no_key=True)
            .only("id", "campaign_id", "family_duid", "effective_submission_id")
            .filter(
                campaign_id=campaign_id,
                family_duid__in={answer.family_duid for answer in answers},
            )
            .order_by("pk")
        }
        if any(answer.family_duid not in families for answer in answers):
            raise PermissionError("A chosen answer is unavailable.")
        admit_campaign(campaign_id, mutating=True)
        parish = (
            SystemConfiguration.objects.select_related("active_configuration__parish")
            .get()
            .active_configuration.parish.pk
        )
        return [
            _decide_one(
                families[answer.family_duid],
                answer,
                actor_id,
                action,
                version,
                bulk_key_for(request_key, answer.id) if bulk else request_key,
                note,
                parish,
            )
            for answer, version in sorted(
                answers.items(), key=lambda pair: (pair[0].family_duid, pair[0].id)
            )
        ]
