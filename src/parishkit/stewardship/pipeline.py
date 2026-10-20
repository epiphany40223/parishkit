"""The shared review pipeline: its words, stages, role actions and refresh rule.

Every Family-submitted stream (census changes, Ministry requests, pledges,
cannot-participate answers and Additional information) passes through the
same stages ([review pipeline](docs/specs/stewardship/review-pipeline/spec.md)).
A stage is never stored: each stream keeps its own state, and one function
per stream here derives the stage from it, so the pipeline holds no second
copy that could drift from the stream's tables and their SQL guards.

This module is pure (no database, no Django) so each mapping is unit tested
as a table. It changes nothing a page shows or a guard enforces: the slices
that move each stream to the shared worklist read it (#1680).

- ``*_placement`` map one stream's stored state to a ``Placement``.
- ``ROLE_ACTIONS`` is the spec's role-specific actions table as data;
  ``allowed_actions`` and ``may_add`` combine it with what a stage offers.
- ``refresh_outcome`` is the R/A/C baseline refresh comparison.
- ``CENSUS_CONFIRMABLE`` says which census fields a ParishSoft read returns.

The vocabulary is display only: stored values (``approved``, audit event
types) keep their names.
"""

import operator
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Stream(StrEnum):
    """The Family-submitted streams the pipeline covers."""

    CENSUS = "census"
    MINISTRY = "ministry"
    FINANCIAL = "financial"
    CANNOT_PARTICIPATE = "cannot_participate"
    ADDITIONAL_INFORMATION = "additional_information"


class Stage(StrEnum):
    """Where one item is; its label is the shared word for it."""

    TO_REVIEW = "to_review"
    CONFLICT = "conflict"
    IGNORED = "ignored"
    READY_TO_UPDATE = "ready_to_update"
    UPDATING = "updating"
    UPDATED = "updated"
    TO_ENTER_BY_HAND = "to_enter_by_hand"
    ENTERED_BY_HAND = "entered_by_hand"
    ENTERED_INCORRECTLY = "entered_incorrectly"
    TO_HANDLE = "to_handle"
    HANDLED = "handled"
    CONFIRMED = "confirmed"
    # Superseded, cancelled or withdrawn: kept for the item's history only.
    HISTORY = "history"

    @property
    def label(self):
        """The stage's word on every page, export and command."""
        return STAGE_LABELS[self]


STAGE_LABELS = MappingProxyType(
    {
        Stage.TO_REVIEW: "To review",
        Stage.CONFLICT: "Conflict",
        Stage.IGNORED: "Ignored",
        Stage.READY_TO_UPDATE: "Ready to update",
        Stage.UPDATING: "Updating",
        Stage.UPDATED: "Updated in ParishSoft",
        Stage.TO_ENTER_BY_HAND: "To enter by hand",
        Stage.ENTERED_BY_HAND: "Entered by hand",
        Stage.ENTERED_INCORRECTLY: "Entered incorrectly",
        Stage.TO_HANDLE: "To handle",
        Stage.HANDLED: "Handled",
        Stage.CONFIRMED: "Confirmed",
        Stage.HISTORY: "History",
    }
)


class Decision(StrEnum):
    """A review decision in force on an item."""

    ACCEPTED = "accepted"
    ACCEPTED_EDITED = "accepted_edited"
    IGNORED = "ignored"
    ADDED = "added"

    @property
    def label(self):
        """The decision's shared word."""
        return DECISION_LABELS[self]


DECISION_LABELS = MappingProxyType(
    {
        Decision.ACCEPTED: "Accepted",
        Decision.ACCEPTED_EDITED: "Accepted (edited)",
        Decision.IGNORED: "Ignored",
        Decision.ADDED: "Added by the parish",
    }
)

# The other shared words of the spec's vocabulary table. Stage and decision
# words are above; together they are every term the spec lists (a test
# checks the spec's table against them).
TERMS = MappingProxyType(
    {
        "baseline": "Baseline",
        "family_answer": "Family's answer",
        "reviewer_value": "Reviewer's value",
        "not_seen": "Not seen in ParishSoft",
        "parishsoft_now": "ParishSoft now",
    }
)


class Action(StrEnum):
    """Something a reader may do to an item."""

    ACCEPT = "accept"
    EDIT = "edit"
    IGNORE = "ignore"
    BACK_TO_REVIEW = "back_to_review"
    ADD = "add"
    ENTERED_BY_HAND = "entered_by_hand"
    HANDLED = "handled"
    START_UPDATE = "start_update"


@dataclass(frozen=True, slots=True)
class Placement:
    """One item's stage and the decision behind it.

    ``decision`` is the decision in force (None while none is, and for an
    item ParishSoft confirmed without review). ``held_decision`` is an
    earlier decision a Conflict keeps visible but not in force. ``outcome``
    is a Ministry request's stored outcome, kept as the label of its
    decision ("No response" still reads as such; ``outcome_label`` in
    ``reports.ministries`` words it). ``failed`` marks a census change
    whose last update attempt failed.
    """

    stage: Stage
    decision: Decision | None = None
    held_decision: Decision | None = None
    outcome: str | None = None
    failed: bool = False


# --- Census stream ---------------------------------------------------------

CENSUS_DECISIONS = frozenset({"unreviewed", "approved", "ignored"})
CENSUS_EXECUTIONS = frozenset(
    {
        "pending",
        "conflict",
        "queued",
        "published",
        "resolved_upstream",
        "resolved_external",
        "failed",
        "superseded",
        "cancelled",
    }
)
CENSUS_HANDLINGS = frozenset({"api", "manual", "report-only"})
CENSUS_ENTITIES = frozenset({"family", "member", "proposed_member"})
# Decision x execution pairs the SQL guards never store: migration 0060
# refuses Entered on an ignored change, and only an approved change is
# queued or published. Mapping them would guess a decision the row does
# not hold, so they are refused like any unknown state.
CENSUS_IMPOSSIBLE = frozenset(
    {
        ("ignored", "resolved_external"),
        ("ignored", "queued"),
        ("ignored", "published"),
        ("unreviewed", "queued"),
        ("unreviewed", "published"),
    }
)
# Executions that end an item's census life whatever its decision.
_CENSUS_FINAL = MappingProxyType(
    {
        "superseded": Stage.HISTORY,
        "cancelled": Stage.HISTORY,
        "resolved_upstream": Stage.CONFIRMED,
        "resolved_external": Stage.ENTERED_BY_HAND,
        "published": Stage.UPDATED,
        "queued": Stage.UPDATING,
    }
)


def census_by_hand(handling, entity_kind):
    """Whether a person, not the update run, carries a census change.

    The same rule as ``responses.census_resolution.by_hand`` (a test keeps
    them equal): every change the handling registry does not mark
    API-writable, and every Family field, since no verified ParishSoft
    address read exists yet.
    """
    return handling != "api" or entity_kind == "family"


def census_placement(*, decision, execution, handling, entity_kind, admin_value_set):
    """A ``ProposedChange``'s stage from its decision × execution.

    Returns None for a ``report-only`` change (pledges, share methods,
    Ministry joins): those belong to the financial and Ministry streams,
    and the census worklist leaves them out today. The order matters: a
    final or in-flight execution wins over the decision; then Ignored;
    then Conflict, keeping an approval visible but not in force; then the
    open decision. ``unreviewed`` + ``resolved_external`` is Entered by
    hand that accepted in one step (decision 2); ``resolved_upstream``
    with no approval is Confirmed with no reviewer ("Already in
    ParishSoft"). A pair in ``CENSUS_IMPOSSIBLE`` raises, as an unknown
    state does: its decision would be a guess.
    """
    if (
        (decision, execution) in CENSUS_IMPOSSIBLE
        or decision not in CENSUS_DECISIONS
        or execution not in CENSUS_EXECUTIONS
        or handling not in CENSUS_HANDLINGS
        or entity_kind not in CENSUS_ENTITIES
        or type(admin_value_set) is not bool
    ):
        raise ValueError("Unknown census change state.")
    if handling == "report-only":
        return None
    accepted = Decision.ACCEPTED_EDITED if admin_value_set else Decision.ACCEPTED
    failed = execution == "failed"
    if execution in _CENSUS_FINAL:
        stage = _CENSUS_FINAL[execution]
        if stage is Stage.HISTORY:
            return Placement(stage)
        if stage is Stage.CONFIRMED and decision != "approved":
            return Placement(stage)
        return Placement(stage, accepted)
    if decision == "ignored":
        return Placement(Stage.IGNORED, Decision.IGNORED)
    if execution == "conflict":
        held = accepted if decision == "approved" else None
        return Placement(Stage.CONFLICT, held_decision=held)
    if decision == "unreviewed":
        # Reconciliation sends a failed approval back as pending or
        # conflict, so unreviewed + failed is not expected; shown To review
        # with the failure rather than refused.
        return Placement(Stage.TO_REVIEW, failed=failed)
    # Approved, pending or failed. Only the update run can fail a change,
    # so a failure is shown Ready to update again (the spec's table).
    if failed or not census_by_hand(handling, entity_kind):
        return Placement(Stage.READY_TO_UPDATE, accepted, failed=failed)
    return Placement(Stage.TO_ENTER_BY_HAND, accepted)


# --- Ministry stream -------------------------------------------------------

MINISTRY_OPEN_STATES = frozenset({"new", "assigned", "in_progress"})
MINISTRY_STATES = MINISTRY_OPEN_STATES | {
    "resolved",
    "closed_no_response",
    "cancelled",
    "superseded",
}
MINISTRY_ROSTER_OUTCOMES = frozenset({"joined", "leave_confirmed"})
MINISTRY_IGNORED_OUTCOMES = frozenset({"declined", "duplicate", "other"})


def ministry_placement(*, state, outcome, source_resolved, roster_entered):
    """A ``MinistryRequest``'s stage from its workflow, outcome and roster tick.

    ``source_resolved`` is whether refresh reconciliation resolved it (it
    has a resolution source: the roster already showed the change), and
    ``roster_entered`` is its latest Entered in ParishSoft tick. A request
    resolved by a person as joined or left is To enter by hand until
    ticked; declined, duplicate, other and closed with no response are
    Ignored, keeping the outcome as the decision's label.
    """
    if (
        state not in MINISTRY_STATES
        or type(source_resolved) is not bool
        or type(roster_entered) is not bool
    ):
        raise ValueError("Unknown Ministry request state.")
    if state in MINISTRY_OPEN_STATES:
        return Placement(Stage.TO_REVIEW)
    if state in ("cancelled", "superseded"):
        return Placement(Stage.HISTORY)
    if state == "closed_no_response" and outcome == "no_response":
        return Placement(Stage.IGNORED, Decision.IGNORED, outcome=outcome)
    if state == "resolved" and outcome in MINISTRY_IGNORED_OUTCOMES:
        return Placement(Stage.IGNORED, Decision.IGNORED, outcome=outcome)
    if state == "resolved" and outcome in MINISTRY_ROSTER_OUTCOMES:
        if source_resolved:
            return Placement(Stage.CONFIRMED, outcome=outcome)
        stage = Stage.ENTERED_BY_HAND if roster_entered else Stage.TO_ENTER_BY_HAND
        return Placement(stage, Decision.ACCEPTED, outcome=outcome)
    raise ValueError("Unknown Ministry request outcome.")


# --- Additional information stream ----------------------------------------

INFORMATION_DISPOSITIONS = frozenset({"current_actionable", "superseded", "withdrawn"})


def information_placement(
    *, disposition, follow_up_needed, followed_up, staff_ignored=False
):
    """An ``AdditionalInformationItem``'s stage from its disposition and follow-up.

    ``followed_up`` is whether ``followed_up_at`` is set. ``follow_up_needed``
    defaults to false, so "Not needed" cannot be told from "never looked
    at": ``staff_ignored`` is whether the latest Staff revision ignored the
    item (``responses.information.staff_ignored``: its review decision, or,
    for a revision saved before migration 0126, follow-up left not needed,
    #1685). An item no Staff member has saved is To review, never silently
    Ignored.
    """
    if (
        disposition not in INFORMATION_DISPOSITIONS
        or type(follow_up_needed) is not bool
        or type(followed_up) is not bool
        or type(staff_ignored) is not bool
    ):
        raise ValueError("Unknown Additional information state.")
    if disposition != "current_actionable":
        return Placement(Stage.HISTORY)
    if followed_up:
        return Placement(Stage.HANDLED, Decision.ACCEPTED)
    if follow_up_needed:
        return Placement(Stage.TO_HANDLE, Decision.ACCEPTED)
    if staff_ignored:
        return Placement(Stage.IGNORED, Decision.IGNORED)
    return Placement(Stage.TO_REVIEW)


# --- Financial and cannot participate -------------------------------------


def financial_placement():
    """A current pledge answer: To review, since no decision is stored yet (#1686)."""
    return Placement(Stage.TO_REVIEW)


LIMITATION_DECISIONS = frozenset({"accepted", "ignored", "none", "handled"})


def cannot_participate_placement(*, decision=None, in_force=False):
    """A current cannot-participate or cannot-attend answer's stage (#1685).

    ``decision`` is the answer's latest review decision (None before any)
    and ``in_force`` whether it still holds: every later live response gave
    the same answer. None in force, or Back to review (``none``), is To
    review; Accepted is To handle, Handled is Handled (accepted, then
    marked), and Ignored is Ignored.
    """
    if (decision is not None and decision not in LIMITATION_DECISIONS) or type(
        in_force
    ) is not bool:
        raise ValueError("Unknown cannot participate state.")
    if not in_force or decision in (None, "none"):
        return Placement(Stage.TO_REVIEW)
    if decision == "ignored":
        return Placement(Stage.IGNORED, Decision.IGNORED)
    stage = Stage.HANDLED if decision == "handled" else Stage.TO_HANDLE
    return Placement(stage, Decision.ACCEPTED)


# --- Role-specific actions -------------------------------------------------

ROLES = ("ministry_leader", "staff", "administrator")
_REVIEW = frozenset({Action.ACCEPT, Action.EDIT, Action.IGNORE, Action.BACK_TO_REVIEW})
_STAFF_CENSUS = _REVIEW | {Action.ADD, Action.ENTERED_BY_HAND}
_STAFF_MINISTRY = _REVIEW | {Action.ADD, Action.ENTERED_BY_HAND}
_STAFF_FINANCIAL = _REVIEW | {Action.ENTERED_BY_HAND}
# Cannot participate and Additional information record what the Family
# said: no Edit and no Add.
_STAFF_HANDLED = frozenset(
    {Action.ACCEPT, Action.IGNORE, Action.BACK_TO_REVIEW, Action.HANDLED}
)
# The spec's role-specific actions table, as data (a test mirrors it). A
# leader's Ministry actions apply only to Ministries they lead
# (LEADER_SCOPED); leaders see no other stream.
ROLE_ACTIONS = MappingProxyType(
    {
        Stream.CENSUS: MappingProxyType(
            {
                "ministry_leader": frozenset(),
                "staff": _STAFF_CENSUS,
                "administrator": _STAFF_CENSUS | {Action.START_UPDATE},
            }
        ),
        Stream.MINISTRY: MappingProxyType(
            {
                "ministry_leader": _REVIEW,
                "staff": _STAFF_MINISTRY,
                "administrator": _STAFF_MINISTRY,
            }
        ),
        Stream.FINANCIAL: MappingProxyType(
            {
                "ministry_leader": frozenset(),
                "staff": _STAFF_FINANCIAL,
                "administrator": _STAFF_FINANCIAL,
            }
        ),
        Stream.CANNOT_PARTICIPATE: MappingProxyType(
            {
                "ministry_leader": frozenset(),
                "staff": _STAFF_HANDLED,
                "administrator": _STAFF_HANDLED,
            }
        ),
        Stream.ADDITIONAL_INFORMATION: MappingProxyType(
            {
                "ministry_leader": frozenset(),
                "staff": _STAFF_HANDLED,
                "administrator": _STAFF_HANDLED,
            }
        ),
    }
)
LEADER_SCOPED = frozenset({Stream.MINISTRY})

# What each stage offers before the reader's role is considered. Edit,
# Accept and Ignore decide an undecided item (a Conflict is decided again
# against ParishSoft's new value); Back to review undoes a decision before
# its update starts. Entered by hand on a To review census row accepts and
# marks in one step (decision 2). Start update is the run over Ready items.
_STAGE_ACTIONS = MappingProxyType(
    {
        Stage.TO_REVIEW: frozenset({Action.ACCEPT, Action.EDIT, Action.IGNORE}),
        Stage.CONFLICT: frozenset({Action.ACCEPT, Action.EDIT, Action.IGNORE}),
        Stage.IGNORED: frozenset({Action.BACK_TO_REVIEW}),
        Stage.READY_TO_UPDATE: frozenset({Action.BACK_TO_REVIEW, Action.START_UPDATE}),
        Stage.TO_ENTER_BY_HAND: frozenset(
            {Action.BACK_TO_REVIEW, Action.ENTERED_BY_HAND}
        ),
        Stage.ENTERED_INCORRECTLY: frozenset(
            {Action.BACK_TO_REVIEW, Action.ENTERED_BY_HAND}
        ),
        Stage.TO_HANDLE: frozenset({Action.BACK_TO_REVIEW, Action.HANDLED}),
    }
)


def role_actions(stream, roles, *, leads=False):
    """Every action ``roles`` may take on ``stream``, whatever the stage.

    ``roles`` is a principal's role set (an Administrator holds all three).
    ``leads`` is whether the reader leads the item's Ministry; a leader's
    actions on a leader-scoped stream need it.
    """
    stream = Stream(stream)
    actions = set()
    for role in roles:
        if role not in ROLES:
            raise ValueError("Unknown role.")
        if role == "ministry_leader" and stream in LEADER_SCOPED and not leads:
            continue
        actions |= ROLE_ACTIONS[stream][role]
    return frozenset(actions)


def stage_actions(stream, stage, *, by_hand=False):
    """The actions an item at ``stage`` of ``stream`` offers to someone allowed.

    ``by_hand`` is ``census_by_hand`` for a census change: a To review By
    hand change also offers Entered by hand, which accepts and marks it in
    one step (decision 2).
    """
    stage = Stage(stage)
    actions = _STAGE_ACTIONS.get(stage, frozenset())
    if Stream(stream) is Stream.CENSUS and stage is Stage.TO_REVIEW and by_hand:
        actions |= {Action.ENTERED_BY_HAND}
    return actions


def allowed_actions(stream, stage, roles, *, leads=False, by_hand=False):
    """The row actions one reader sees on one item: stage ∩ role.

    Actions a role does not allow are not shown at all (not disabled), so
    a leader and Staff see different icon sets on the same table. The
    server and database still enforce each action where the stream's rules
    are enforced today; this only decides what is offered.
    """
    return stage_actions(stream, stage, by_hand=by_hand) & role_actions(
        stream, roles, leads=leads
    )


def may_add(stream, roles):
    """Whether the reader gets the Add button on ``stream``'s worklist."""
    return Action.ADD in role_actions(stream, roles)


# --- Confirmation by a refresh ---------------------------------------------

# Whether some ParishSoft read returns each census registry field, so a
# refresh can confirm an entry (spec: What is confirmable). Keyed like the
# handling registry (``responses.capabilities``); a test keeps the keys
# equal. Family addresses wait for #1688's smoke test to learn which block
# ParishSoft reports as primary; pledge frequency waits for its own check
# (#1704). The email opt-out is the Family's sendNoMail flag (Administrator,
# 2026-10-18, #1680): not writable, but returned by the Family search.
_CONFIRMABLE_MEMBER = (
    "first_name",
    "middle_name",
    "last_name",
    "nickname",
    "maiden_name",
    "birth_date",
    "language",
    "gender",
    "email",
    "home_phone",
    "mobile_phone",
    "work_phone",
    "prefix",
    "suffix",
    "marital_status",
    "death_date",
    "deceased_status",
    "moved_household",
    "ministry_join",
    "ministry_leave",
)
CENSUS_CONFIRMABLE = MappingProxyType(
    {
        ("family", "home_address"): False,
        ("family", "mailing_address"): False,
        ("family", "email_opt_out"): True,
        ("family", "annual_pledge"): True,
        ("family", "frequency"): False,
        ("family", "share_methods"): False,
        **{("member", field): True for field in _CONFIRMABLE_MEMBER},
        ("proposed_member", "new_member"): True,
        ("proposed_member", "ministry_join"): True,
    }
)


class RefreshOutcome(StrEnum):
    """What one promoted refresh does to one open item."""

    CONFIRMED = "confirmed"
    UNCHANGED = "unchanged"
    # Unchanged, and an Entered by hand item has now been found at its old
    # value by enough full refreshes: flag Not seen in ParishSoft.
    NOT_SEEN = "not_seen"
    ENTERED_INCORRECTLY = "entered_incorrectly"
    CONFLICT = "conflict"
    # An Ignored item whose Family answer ParishSoft now holds: noted for
    # the reviewer, the item stays Ignored.
    IGNORED_NOW_MATCHES = "ignored_now_matches"
    # The Member or Family left the baseline: the stage stays, with the
    # Not in ParishSoft badge.
    ENTITY_GONE = "entity_gone"


# Stages a refresh re-evaluates. History, Confirmed and the stages of data
# ParishSoft does not hold (To handle, Handled) have nothing to compare.
REEVALUATED_STAGES = frozenset(
    {
        Stage.TO_REVIEW,
        Stage.CONFLICT,
        Stage.IGNORED,
        Stage.READY_TO_UPDATE,
        Stage.UPDATING,
        Stage.UPDATED,
        Stage.TO_ENTER_BY_HAND,
        Stage.ENTERED_BY_HAND,
        Stage.ENTERED_INCORRECTLY,
    }
)
_ENTERED = frozenset({Stage.ENTERED_BY_HAND, Stage.ENTERED_INCORRECTLY})
_UNSET = object()


def refresh_outcome(
    stage,
    *,
    refreshed,
    answered_against,
    carried_forward,
    marked_against=_UNSET,
    present=True,
    full_refreshes_unchanged=0,
    not_seen_after=2,
    same=operator.eq,
):
    """The R/A/C comparison of the spec's baseline refresh re-evaluation.

    ``refreshed`` is R (ParishSoft's value now), ``answered_against`` A (the
    baseline the item was answered against) and ``carried_forward`` C (the
    reviewer's value, else the Family's answer). ``marked_against`` is the
    baseline recorded when the item was marked Entered by hand; it is A when
    not given (marks made before #1689 recorded none). ``same`` is the
    stream's canonical comparison, never a raw string compare.
    ``full_refreshes_unchanged`` counts the full refreshes, this one
    included, that found an Entered by hand item still at its old value.

    The rules, in order: an entity gone keeps its stage (rule 4, checked
    first since R does not exist then); R = C confirms (an Ignored item is
    only noted); R = A is unchanged, flagged Not seen after enough full
    refreshes; R differing from both is Entered incorrectly for a marked
    item whose R also differs from its mark, and Conflict for any other
    open item. For a marked item "unchanged" compares R with the mark, so
    an item re-marked over an incorrect value is not reported again until
    ParishSoft changes. An Ignored item ParishSoft moved to a third value
    stays Ignored (no change is wanted), as the census page shows an
    ignored change in conflict today. An Updated in ParishSoft item found
    back at A is a Conflict: the write was undone. A queued item's
    Conflict meets the plan at the run's own preflight; the caller leaves
    the plan alone.
    """
    stage = Stage(stage)
    if stage not in REEVALUATED_STAGES:
        raise ValueError("A refresh does not re-evaluate this stage.")
    if not present:
        return RefreshOutcome.ENTITY_GONE
    if same(refreshed, carried_forward):
        if stage is Stage.IGNORED:
            return RefreshOutcome.IGNORED_NOW_MATCHES
        return RefreshOutcome.CONFIRMED
    if stage in _ENTERED:
        mark = answered_against if marked_against is _UNSET else marked_against
        if not same(refreshed, mark):
            return RefreshOutcome.ENTERED_INCORRECTLY
        if (
            stage is Stage.ENTERED_BY_HAND
            and full_refreshes_unchanged >= not_seen_after
        ):
            return RefreshOutcome.NOT_SEEN
        return RefreshOutcome.UNCHANGED
    if stage is Stage.IGNORED:
        return RefreshOutcome.UNCHANGED
    if same(refreshed, answered_against) and stage is not Stage.UPDATED:
        return RefreshOutcome.UNCHANGED
    return RefreshOutcome.CONFLICT
