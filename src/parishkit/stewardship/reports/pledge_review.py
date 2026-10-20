"""The financial stream on the review pipeline: Pledges (#1686).

Each Family's current pledge answer is one item ([review pipeline spec,
"Financial stream"](docs/specs/stewardship/review-pipeline/spec.md)). Its
history is ``PledgeReview`` (one immutable row per decision), and its stage
is derived from its latest row and its latest refresh check by the SQL
selection ``stewardship_pledge_stages_v1`` (frozen migration 0135,
provisional number, over 0127's ``stewardship_pledge_review_states_v1``),
which the page, the counts and the row guard all read, so none of them can
disagree about whether a Family's new answer reopened its pledge or a
refresh Confirmed it or found a Conflict.

- ``read_items`` reads that selection with each item's latest row;
  ``placement`` maps an item onto the pipeline (``financial_placement``),
  with its latest refresh check (``pledge_refresh``, #1704, #1747), which
  may make it Confirmed, a Conflict or Entered incorrectly.
- ``edited_value`` checks an Edit's annual pledge and frequency as the
  Family form checks them.
- ``PledgeDecisions`` is the stream's ``DecisionService``: Accept, Edit,
  Ignore, Back to review and Entered by hand, one item or in bulk (all or
  none), in the writers' order, each with its audit event.

What a reader is offered never decides what is allowed: the service checks
the role, the version and the stage again under the lock, and the guard
checks them once more in SQL.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid5

from django.db import IntegrityError, connection

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.audit.schemas import Action as AuditAction
from parishkit.stewardship.audit.schemas import ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import bound_correlation, correlation
from parishkit.stewardship.pipeline import (
    ROLES,
    Action,
    Stage,
    Stream,
    allowed_actions,
    financial_placement,
    undo_needs_note,
    undo_stage,
)
from parishkit.stewardship.responses.financial import (
    FREQUENCIES,
    pledge_amount,
    pledge_error,
)
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.web.review_worklist import WORDS, StaleDecision

from .export_services import admit_campaign
from .pledge_models import PledgeReview
from .pledge_refresh import marked_pledge

STATES = (
    "SELECT family_id, family_duid, submission_id, sequence, stage, base_stage, "
    "review_id, in_force, check_outcome, observed, checks "
    "FROM stewardship_pledge_stages_v1(%s, %s)"
)
# What each worklist action records: the stored action, and the decision
# the row leaves in force (Entered by hand keeps the one it marks, and so
# does Back to review when it undoes a terminal step, #1840).
RECORDED = {
    Action.ACCEPT: ("accept", "accepted"),
    Action.EDIT: ("edit", "accepted_edited"),
    Action.IGNORE: ("ignore", "ignored"),
    Action.BACK_TO_REVIEW: ("back_to_review", None),
    Action.ENTERED_BY_HAND: ("entered_by_hand", None),
}
ITEMS = "pledges"
# PostgreSQL's SQLSTATE for a unique violation: a racing decision.
UNIQUE_VIOLATION = "23505"
MAX_NOTE = 2000
# An Edit that carries forward exactly the Family's answer is recorded as
# what it is: an Accept.
EDIT_MATCHED = "accepted: the value matched the Family's answer."


@dataclass(frozen=True, slots=True)
class Item:
    """One Family's pledge item, as the shared selection reads it.

    ``submission_id`` (the Family's current live response) is the item's id
    on the page and ``sequence`` its latest row's (0 with none); its version
    on the page is ``version_of(item)``. ``review`` is that latest row,
    ``in_force`` whether the Family's pledge answer is unchanged since it,
    and ``stage`` the selection's own stage, which ``placement`` must agree
    with. ``base_stage`` is the stage its decision rows alone give (0127's),
    ``check`` the outcome of its latest refresh check that counts (None
    without one), ``observed`` ParishSoft's amount that check found, and
    ``checks`` how many checks the Family's pledge has (#1747).
    """

    family_id: UUID
    family_duid: int
    submission_id: UUID
    sequence: int
    stage: str
    review: PledgeReview | None
    in_force: bool
    base_stage: str = ""
    check: str | None = None
    observed: Decimal | None = None
    checks: int = 0


def version_of(item):
    """The item's version on the page: its sequence plus its checks.

    A decision is made against what the reader saw. Both parts only grow, so
    the sum changes with every decision and with every refresh check (a
    Conflict found, or ParishSoft's amount moving under one, #1747): a
    decision posted over a check the reader never saw is stale, never
    applied to a Conflict nobody looked at.
    """
    return item.sequence + item.checks


def read_items(campaign_id, family_id=None):
    """Every pledge item of the campaign (or one Family's), by submission id.

    One statement for the selection and one for the items' latest rows.
    """
    with connection.cursor() as cursor:
        cursor.execute(STATES, [campaign_id, family_id])
        found = cursor.fetchall()
    reviews = PledgeReview.objects.in_bulk([row[6] for row in found if row[6]])
    return {
        row[2]: Item(
            family_id=row[0],
            family_duid=row[1],
            submission_id=row[2],
            sequence=row[3],
            stage=row[4],
            base_stage=row[5],
            review=reviews.get(row[6]),
            in_force=row[7],
            check=row[8],
            observed=row[9],
            checks=row[10],
        )
        for row in found
    }


def placement(item):
    """The item's stage and decision on the pipeline.

    The latest row's decision counts only while it is in force: a pledge
    the Family changed since is To review again. Its latest refresh check
    that counts may move it on (Confirmed, a Conflict, Entered
    incorrectly). The shared selection derives the same stage in SQL; a
    disagreement is a defect, raised as a ValueError rather than shown.
    """
    review = item.review if item.in_force else None
    found = financial_placement(
        decision=review.decision if review else None,
        entered=review.entered if review else False,
        check=item.check,
    )
    if found.stage.value != item.stage:
        raise ValueError("The pledge review stage disagrees with its selection.")
    return found


def reader_roles(principal):
    """The pipeline roles a principal holds (an Administrator holds all three)."""
    return frozenset(principal.roles) & frozenset(ROLES)


# Actions a stage offers that the stream withholds: Entered incorrectly's
# Entered by hand again comes with #1737; until then a refresh confirms the
# pledge once ParishSoft holds the reviewer's amount. Its Back to review
# undoes the mark like any terminal step's (#1840).
WITHHELD = {Stage.ENTERED_INCORRECTLY: frozenset({Action.ENTERED_BY_HAND})}
# Why a Back to review needing a note was refused without one.
REOPEN_NEEDS_NOTE = "Reopening an ignored pledge needs a note."
UNDO_NEEDS_NOTE = "Undoing a pledge's mark or confirmation needs a note."


def offered(stage, roles):
    """The actions a pledge at ``stage`` offers readers with ``roles``."""
    return allowed_actions(Stream.FINANCIAL, stage, roles) - WITHHELD.get(
        stage, frozenset()
    )


class EditRefusal(ValueError):
    """An Edit value the reader can correct, worded for its field.

    ``fields`` names the editor's fields it is shown at (``value_amount``,
    ``value_frequency``); the view answers 400 with the message there.
    """

    def __init__(self, fields, message):
        """Keep the fields to mark and the static message; never a value."""
        super().__init__(message)
        self.fields = tuple(fields)
        self.message = message


FREQUENCY_HINT = "Choose how often the Family gives: a pledge above zero needs one."


def edited_value(fields):
    """An Edit's annual pledge and frequency, checked as the Family form checks them.

    ``fields`` are the editor's posted fields (lists of values). Returns
    ``(Decimal amount, frequency or None)``; a pledge above zero needs a
    frequency, and a zero pledge may have none. Anything else raises
    ``EditRefusal`` at its field; a missing or repeated field is a plain
    ValueError (a malformed form).
    """
    if set(fields) != {"value_amount", "value_frequency"} or any(
        len(values) != 1 for values in fields.values()
    ):
        raise ValueError("A pledge Edit needs its amount and frequency once.")
    try:
        amount = pledge_amount(fields["value_amount"][0])
    except ValueError:
        # The Family form's own words: a blank amount, the limit, or the
        # format (responses.financial.pledge_error).
        message = pledge_error(fields["value_amount"][0])
        raise EditRefusal(("value_amount",), message) from None
    frequency = fields["value_frequency"][0]
    if frequency not in ("", *FREQUENCIES):
        raise ValueError("Unknown pledge frequency.")
    if amount.cents > 0 and not frequency:
        raise EditRefusal(("value_frequency",), FREQUENCY_HINT)
    return amount.decimal, frequency or None


def family_value(submission):
    """The Family's own annual pledge and frequency, as an Edit compares them."""
    financial = submission.answers.get("financial") or {}
    amount = submission.annual_pledge
    return (
        Decimal("0.00") if amount is None else Decimal(amount),
        financial.get("frequency") or None,
    )


def bulk_key_for(key, item_id):
    """One item's request key within a bulk decision: stable, so a replay is one."""
    return uuid5(key, str(item_id))


class PledgeDecisions:
    """The financial stream's ``DecisionService`` for one campaign's worklist.

    ``store`` is the policy store the actor is reloaded from; ``namer``
    names an item (a submission id) as its row does, or returns None.
    """

    def __init__(self, store, campaign_id, *, namer=None):
        """Bind the policy store, the campaign and the notice's namer."""
        self.store = store
        self.campaign_id = campaign_id
        self.namer = namer or (lambda item_id: None)

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows.

        All or none: an item someone else changed first (another decision,
        or the Family answering again) is a ``StaleDecision`` naming it, and
        nothing is recorded. A reader who may not see pledges, or an item of
        another campaign, is a PermissionError; an action the item's stage
        does not offer, or a malformed field, is a ValueError (an Edit's
        correctable value an ``EditRefusal``).
        """
        if decision.action not in RECORDED:
            raise ValueError("Pledges take no such decision.")
        if len(decision.note) > MAX_NOTE:
            raise ValueError("A decision note is too long.")
        value = (
            edited_value(decision.fields) if decision.action is Action.EDIT else None
        )
        ids = {UUID(item): version for item, version in decision.versions.items()}
        if not ids or (decision.action is Action.EDIT and len(ids) != 1):
            raise ValueError("A pledge decision names its items.")
        try:
            with correlation(bound_correlation()), work_transaction():
                return self._decide(principal.identity, decision, ids, value)
        except IntegrityError as error:
            # Only a unique violation is a race: a second decision of the
            # same version passed its checks and the unique sequence refused
            # it. Any other refusal (a guard, a check, the audit check) is a
            # defect, never dressed up as someone else's change.
            if getattr(error.__cause__, "sqlstate", None) != UNIQUE_VIOLATION:
                raise
            raise StaleDecision() from None

    def _decide(self, actor, decision, ids, value):
        """Record the decision on every chosen item under the writers' lock."""
        principal = current_principal(self.store, actor)
        if not allows(principal, Capability.FINANCIAL_DETAIL):
            raise PermissionError("Pledges are unavailable.")
        admit_campaign(self.campaign_id, mutating=True)
        responses = {
            row.pk: row
            for row in Submission.objects.filter(
                pk__in=ids, campaign_id=self.campaign_id, mode="live"
            )
        }
        if len(responses) != len(ids):
            raise PermissionError("A pledge is unavailable.")
        keys = {
            item: bulk_key_for(decision.key, item) if decision.bulk else decision.key
            for item in ids
        }
        if self._replayed(actor, keys):
            return self._notice(decision, list(ids), matched=False)
        # A refresh may have Confirmed a pledge, found a Conflict or found
        # it Entered incorrectly (#1704, #1747); the selection places it as
        # the page and the guard do.
        items = read_items(self.campaign_id)
        roles = reader_roles(principal)
        matched = False
        for item_id, version in ids.items():
            item = items.get(item_id)
            if item is None or version_of(item) != version:
                # The Family answered again (another response is current) or
                # someone decided first.
                raise StaleDecision(self.namer(item_id))
            stage = placement(item).stage
            if decision.action not in offered(stage, roles):
                raise ValueError("That decision is not offered for this pledge.")
            # Reopening an Ignored pledge, and undoing a terminal step
            # (#1840), need a reason; the guard requires it too.
            if (
                decision.action is Action.BACK_TO_REVIEW
                and undo_needs_note(Stream.FINANCIAL, stage)
                and not decision.note
            ):
                raise ValueError(
                    REOPEN_NEEDS_NOTE if stage is Stage.IGNORED else UNDO_NEEDS_NOTE
                )
            action = decision.action
            if action is Action.EDIT and value == family_value(responses[item_id]):
                action, matched = Action.ACCEPT, True
            self._record(actor, item, action, value, keys[item_id], decision.note)
        return self._notice(decision, list(ids), matched=matched)

    @staticmethod
    def _replayed(actor, keys):
        """Whether this exact post was recorded already (a resend of the form).

        A bulk decision is all or none, so finding any of its keys means the
        whole of it was recorded.
        """
        return PledgeReview.objects.filter(
            actor_id=actor, request_key__in=list(keys.values())
        ).exists()

    def _record(self, actor, item, action, value, key, note):
        """Insert one decision row and its audit event; the guard checks both.

        Entered by hand, and Back to review of a terminal step (#1840),
        carry the decision in force forward with its values: the mark keeps
        it, and the undo removes only the step after it, so the pledge goes
        back to To enter by hand (``pipeline.undo_stage``; a Confirmed
        pledge no decision was behind goes back to To review with none).
        """
        stored, decision = RECORDED[action]
        reviewed = (None, None)
        entered = False
        marked = None
        keeps = action is Action.ENTERED_BY_HAND or (
            action is Action.BACK_TO_REVIEW
            and undo_stage(Stream.FINANCIAL, placement(item)) is not Stage.TO_REVIEW
        )
        if action is Action.EDIT:
            reviewed = value
        elif keeps:
            decision = item.review.decision
            reviewed = (
                item.review.reviewed_annual_pledge,
                item.review.reviewed_frequency,
            )
        if action is Action.ENTERED_BY_HAND:
            # The mark records ParishSoft's upcoming-year amount when the
            # current snapshot proves it (#1704), which the refresh then
            # compares with; the guard checks it against the same snapshot.
            entered = True
            marked = marked_pledge(self.campaign_id, item.family_duid)
        review = PledgeReview.objects.create(
            actor_id=actor,
            family_id=item.family_id,
            submission_id=item.submission_id,
            sequence=item.sequence + 1,
            request_key=key,
            action=stored,
            decision=decision,
            reviewed_annual_pledge=reviewed[0],
            reviewed_frequency=reviewed[1],
            entered=entered,
            # The guard sets the answer from the response itself.
            answer={},
            marked_pledge=marked,
            marked_pledge_known=marked is not None,
            note=note,
        )
        # The audit names the row and the versions, never an amount or note.
        record_action(
            AuditAction.PLEDGE_REVIEW_UPDATED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor,
            subject_id=review.pk,
            campaign_id=self.campaign_id,
            current_parish=True,
            context={
                "outcome": Outcome.CHANGED,
                "before_version": item.sequence,
                "after_version": item.sequence + 1,
            },
        )

    def _notice(self, decision, items, *, matched):
        """What the page says once the decision is recorded."""
        done = EDIT_MATCHED if matched else WORDS[decision.action].done
        if len(items) == 1:
            name = self.namer(items[0]) or "The pledge"
            return f"{name} {done}"
        return f"{len(items):,} {ITEMS} {done}"
