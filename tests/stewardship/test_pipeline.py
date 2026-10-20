"""The shared review pipeline's words, stage mappings, role table and refresh rule.

Each table below mirrors a table of the review pipeline spec
(docs/specs/stewardship/review-pipeline/spec.md), so a change to either
shows as a failing row here.
"""

import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from parishkit.stewardship import pipeline
from parishkit.stewardship.pipeline import (
    Action,
    Decision,
    Placement,
    RefreshOutcome,
    Stage,
    Stream,
)
from parishkit.stewardship.responses import capabilities
from parishkit.stewardship.responses.census_resolution import by_hand

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/specs/stewardship/review-pipeline/spec.md"

A, AE, IG = Decision.ACCEPTED, Decision.ACCEPTED_EDITED, Decision.IGNORED
ADD = Decision.ADDED


def spec_vocabulary():
    """The terms in the first column of the spec's Pipeline vocabulary table."""
    text = SPEC.read_text().split("## Pipeline vocabulary\n", 1)[1]
    table = text.split("| Term | Use | Replaces today |\n| --- | --- | --- |\n", 1)[1]
    terms = set()
    for line in table.splitlines():
        if not line.startswith("|"):
            break
        terms.update(part.strip() for part in line.split("|")[1].split(" / "))
    return terms


def test_vocabulary_matches_the_spec_table():
    """Every spec term has exactly one word here, and nothing extra."""
    words = (
        set(pipeline.STAGE_LABELS.values())
        | set(pipeline.DECISION_LABELS.values())
        | set(pipeline.TERMS.values())
    )
    # History is a stage the spec names in its mapping, not a table term;
    # "ParishSoft now" is the column the Baseline row keeps.
    assert spec_vocabulary() == words - {"History", "ParishSoft now"}
    assert Stage.UPDATED.label == "Updated in ParishSoft"
    assert Decision.ACCEPTED_EDITED.label == "Accepted (edited)"


def census(decision, execution, handling="manual", entity="family", edited=False):
    """Map one census state with readable defaults."""
    return pipeline.census_placement(
        decision=decision,
        execution=execution,
        handling=handling,
        entity_kind=entity,
        admin_value_set=edited,
    )


# The spec's "Existing state to stage" census rows, plus the combinations
# the table folds together. (decision, execution, handling, entity, edited).
CENSUS_TABLE = [
    # unreviewed + pending: To review, By hand rows too.
    (("unreviewed", "pending", "api", "member", False), Placement(Stage.TO_REVIEW)),
    (("unreviewed", "pending", "manual", "member", False), Placement(Stage.TO_REVIEW)),
    (("unreviewed", "pending", "api", "family", False), Placement(Stage.TO_REVIEW)),
    # Conflict keeps an approval visible, not in force.
    (("unreviewed", "conflict", "api", "member", False), Placement(Stage.CONFLICT)),
    (
        ("approved", "conflict", "api", "member", False),
        Placement(Stage.CONFLICT, held_decision=A),
    ),
    (
        ("approved", "conflict", "api", "member", True),
        Placement(Stage.CONFLICT, held_decision=AE),
    ),
    # approved + pending: Ready to update (api) / To enter by hand (manual).
    (
        ("approved", "pending", "api", "member", False),
        Placement(Stage.READY_TO_UPDATE, A),
    ),
    (
        ("approved", "pending", "api", "member", True),
        Placement(Stage.READY_TO_UPDATE, AE),
    ),
    (
        ("approved", "pending", "manual", "member", False),
        Placement(Stage.TO_ENTER_BY_HAND, A),
    ),
    (
        ("approved", "pending", "manual", "proposed_member", True),
        Placement(Stage.TO_ENTER_BY_HAND, AE),
    ),
    # An api-registered Family field is still By hand (no address read).
    (
        ("approved", "pending", "api", "family", False),
        Placement(Stage.TO_ENTER_BY_HAND, A),
    ),
    # approved + failed: Ready to update with the failure shown.
    (
        ("approved", "failed", "api", "member", False),
        Placement(Stage.READY_TO_UPDATE, A, failed=True),
    ),
    (
        ("unreviewed", "failed", "api", "member", False),
        Placement(Stage.TO_REVIEW, failed=True),
    ),
    # queued / published.
    (("approved", "queued", "api", "member", False), Placement(Stage.UPDATING, A)),
    (("approved", "published", "api", "member", True), Placement(Stage.UPDATED, AE)),
    # resolved_external: Entered by hand, which on a To review row accepts
    # and marks in one step (decision 2).
    (
        ("unreviewed", "resolved_external", "manual", "family", False),
        Placement(Stage.ENTERED_BY_HAND, A),
    ),
    (
        ("approved", "resolved_external", "manual", "member", True),
        Placement(Stage.ENTERED_BY_HAND, AE),
    ),
    # resolved_upstream: Confirmed; with no reviewer when never approved.
    (
        ("unreviewed", "resolved_upstream", "api", "member", False),
        Placement(Stage.CONFIRMED),
    ),
    (
        ("approved", "resolved_upstream", "api", "member", False),
        Placement(Stage.CONFIRMED, A),
    ),
    # ignored, any open execution.
    (("ignored", "pending", "manual", "family", False), Placement(Stage.IGNORED, IG)),
    (("ignored", "conflict", "api", "member", False), Placement(Stage.IGNORED, IG)),
    (("ignored", "failed", "api", "member", False), Placement(Stage.IGNORED, IG)),
    # superseded, cancelled: history, whatever the decision.
    (("approved", "superseded", "api", "member", True), Placement(Stage.HISTORY)),
    (("ignored", "cancelled", "manual", "family", False), Placement(Stage.HISTORY)),
    (("unreviewed", "cancelled", "api", "member", False), Placement(Stage.HISTORY)),
]


@pytest.mark.parametrize(("state", "expected"), CENSUS_TABLE)
def test_census_state_to_stage(state, expected):
    """Each stored census decision × execution reaches the spec's stage."""
    assert census(*state) == expected


def test_census_covers_every_stored_combination():
    """Every combination the SQL guards admit maps, a report-only change
    is not a census-stream item, and the pairs the guards refuse raise
    rather than guess a decision."""
    impossible = {
        ("ignored", "resolved_external"),
        ("ignored", "queued"),
        ("ignored", "published"),
        ("unreviewed", "queued"),
        ("unreviewed", "published"),
    }
    # Written out independently so the module's set cannot drift silently.
    assert impossible == pipeline.CENSUS_IMPOSSIBLE
    for combo in itertools.product(
        sorted(pipeline.CENSUS_DECISIONS),
        sorted(pipeline.CENSUS_EXECUTIONS),
        sorted(pipeline.CENSUS_HANDLINGS),
        sorted(pipeline.CENSUS_ENTITIES),
        (False, True),
    ):
        if combo[:2] in impossible:
            with pytest.raises(ValueError):
                census(*combo)
            continue
        placement = census(*combo)
        if combo[2] == "report-only":
            assert placement is None
        else:
            assert isinstance(placement, Placement)


@pytest.mark.parametrize(
    "state",
    [
        ("approve", "pending", "api", "member", False),
        ("approved", "done", "api", "member", False),
        ("approved", "pending", "auto", "member", False),
        ("approved", "pending", "api", "household", False),
        ("approved", "pending", "api", "member", 1),
    ],
)
def test_census_refuses_unknown_state(state):
    """An unknown stored value is refused, never guessed."""
    with pytest.raises(ValueError):
        census(*state)


def test_census_by_hand_matches_the_resolution_rule():
    """The pipeline's By hand rule equals census_resolution.by_hand."""

    class Row:
        """A minimal stand-in carrying the two fields by_hand reads."""

        def __init__(self, handling, entity_kind):
            """Hold the fields."""
            self.handling, self.entity_kind = handling, entity_kind

    for handling, entity in itertools.product(
        pipeline.CENSUS_HANDLINGS, pipeline.CENSUS_ENTITIES
    ):
        assert pipeline.census_by_hand(handling, entity) == by_hand(
            Row(handling, entity)
        )


def ministry(state, outcome=None, source=False, ticked=False):
    """Map one Ministry request state."""
    return pipeline.ministry_placement(
        state=state, outcome=outcome, source_resolved=source, roster_entered=ticked
    )


MINISTRY_TABLE = [
    (("new",), Placement(Stage.TO_REVIEW)),
    (("assigned",), Placement(Stage.TO_REVIEW)),
    (("in_progress",), Placement(Stage.TO_REVIEW)),
    (
        ("resolved", "joined"),
        Placement(Stage.TO_ENTER_BY_HAND, A, outcome="joined"),
    ),
    (
        ("resolved", "leave_confirmed"),
        Placement(Stage.TO_ENTER_BY_HAND, A, outcome="leave_confirmed"),
    ),
    (
        ("resolved", "joined", False, True),
        Placement(Stage.ENTERED_BY_HAND, A, outcome="joined"),
    ),
    (
        ("resolved", "leave_confirmed", False, True),
        Placement(Stage.ENTERED_BY_HAND, A, outcome="leave_confirmed"),
    ),
    # Resolved by refresh reconciliation: the roster is current.
    (("resolved", "joined", True), Placement(Stage.CONFIRMED, outcome="joined")),
    (
        ("resolved", "leave_confirmed", True),
        Placement(Stage.CONFIRMED, outcome="leave_confirmed"),
    ),
    # Ignored, keeping the original outcome as the decision's label.
    (("resolved", "declined"), Placement(Stage.IGNORED, IG, outcome="declined")),
    (("resolved", "duplicate"), Placement(Stage.IGNORED, IG, outcome="duplicate")),
    (("resolved", "other"), Placement(Stage.IGNORED, IG, outcome="other")),
    (
        ("closed_no_response", "no_response"),
        Placement(Stage.IGNORED, IG, outcome="no_response"),
    ),
    (("cancelled",), Placement(Stage.HISTORY)),
    (("superseded",), Placement(Stage.HISTORY)),
]


@pytest.mark.parametrize(("state", "expected"), MINISTRY_TABLE)
def test_ministry_state_to_stage(state, expected):
    """Each stored workflow, outcome and tick reaches the spec's stage."""
    assert ministry(*state) == expected


@pytest.mark.parametrize(
    "state",
    [
        ("open",),
        ("resolved", "no_response"),
        ("resolved", None),
        ("closed_no_response", "joined"),
        ("resolved", "joined", None),
    ],
)
def test_ministry_refuses_unknown_state(state):
    """A state the schema does not admit is refused."""
    with pytest.raises(ValueError):
        ministry(*state)


def information(disposition, needed=False, done=False, ignored=False):
    """Map one Additional information state."""
    return pipeline.information_placement(
        disposition=disposition,
        follow_up_needed=needed,
        followed_up=done,
        staff_ignored=ignored,
    )


INFORMATION_TABLE = [
    (("current_actionable", True, False), Placement(Stage.TO_HANDLE, A)),
    (("current_actionable", True, True), Placement(Stage.HANDLED, A)),
    (("current_actionable", False, True), Placement(Stage.HANDLED, A)),
    # The default cannot be told from a Staff choice: To review unless the
    # latest Staff revision ignored it (#1685).
    (("current_actionable", False, False), Placement(Stage.TO_REVIEW)),
    (("current_actionable", False, False, True), Placement(Stage.IGNORED, IG)),
    (("superseded", True, True), Placement(Stage.HISTORY)),
    (("withdrawn",), Placement(Stage.HISTORY)),
]


@pytest.mark.parametrize(("state", "expected"), INFORMATION_TABLE)
def test_information_state_to_stage(state, expected):
    """Each disposition and follow-up pair reaches the spec's stage."""
    assert information(*state) == expected


def test_information_refuses_unknown_state():
    """An unknown disposition or a non-boolean flag is refused."""
    with pytest.raises(ValueError):
        information("current")
    with pytest.raises(ValueError):
        information("current_actionable", needed=None)


def test_live_answer_streams_start_to_review():
    """Pledges and undecided cannot-participate answers are To review."""
    assert pipeline.financial_placement() == Placement(Stage.TO_REVIEW)
    assert pipeline.cannot_participate_placement() == Placement(Stage.TO_REVIEW)


CANNOT_PARTICIPATE_TABLE = [
    ((None, False), Placement(Stage.TO_REVIEW)),
    (("none", True), Placement(Stage.TO_REVIEW)),
    (("accepted", True), Placement(Stage.TO_HANDLE, A)),
    (("handled", True), Placement(Stage.HANDLED, A)),
    (("ignored", True), Placement(Stage.IGNORED, IG)),
    # A decision the Family's later answer broke holds no more (#1685).
    (("accepted", False), Placement(Stage.TO_REVIEW)),
    (("handled", False), Placement(Stage.TO_REVIEW)),
    (("ignored", False), Placement(Stage.TO_REVIEW)),
]


@pytest.mark.parametrize(("state", "expected"), CANNOT_PARTICIPATE_TABLE)
def test_cannot_participate_state_to_stage(state, expected):
    """Each latest review decision, in force or not, reaches the spec's stage."""
    decision, in_force = state
    placement = pipeline.cannot_participate_placement(
        decision=decision, in_force=in_force
    )
    assert placement == expected


@pytest.mark.parametrize(("decision", "in_force"), [("approved", True), (None, 1)])
def test_cannot_participate_refuses_unknown_state(decision, in_force):
    """An unknown decision or a non-boolean in-force flag is refused."""
    with pytest.raises(ValueError):
        pipeline.cannot_participate_placement(decision=decision, in_force=in_force)


# The spec's role-specific actions table, written out independently of the
# module's data: (stream, role) -> actions. A leader's Ministry row holds
# only for Ministries they lead.
REVIEW = {"accept", "edit", "ignore", "back_to_review"}
ROLE_TABLE = {
    ("census", "ministry_leader"): set(),
    ("census", "staff"): REVIEW | {"add", "entered_by_hand"},
    ("census", "administrator"): REVIEW | {"add", "entered_by_hand", "start_update"},
    ("ministry", "ministry_leader"): REVIEW,
    ("ministry", "staff"): REVIEW | {"add", "entered_by_hand"},
    ("ministry", "administrator"): REVIEW | {"add", "entered_by_hand"},
    ("financial", "ministry_leader"): set(),
    ("financial", "staff"): REVIEW | {"entered_by_hand"},
    ("financial", "administrator"): REVIEW | {"entered_by_hand"},
    ("cannot_participate", "ministry_leader"): set(),
    ("cannot_participate", "staff"): {"accept", "ignore", "back_to_review", "handled"},
    ("cannot_participate", "administrator"): {
        "accept",
        "ignore",
        "back_to_review",
        "handled",
    },
    ("additional_information", "ministry_leader"): set(),
    ("additional_information", "staff"): {
        "accept",
        "ignore",
        "back_to_review",
        "handled",
    },
    ("additional_information", "administrator"): {
        "accept",
        "ignore",
        "back_to_review",
        "handled",
    },
}


@pytest.mark.parametrize(("key", "expected"), sorted(ROLE_TABLE.items()))
def test_role_table_mirrors_the_spec(key, expected):
    """Each (stream, role) cell is the spec's list of decisions."""
    stream, role = key
    assert set(pipeline.ROLE_ACTIONS[Stream(stream)][role]) == expected
    assert pipeline.role_actions(stream, {role}, leads=True) == expected


def test_role_table_has_every_cell():
    """No stream or role is missing from the data."""
    assert {
        (stream.value, role)
        for stream, cells in pipeline.ROLE_ACTIONS.items()
        for role in cells
    } == set(ROLE_TABLE)


def test_leader_acts_only_on_ministries_they_lead():
    """A leader's Ministry actions need ``leads``; they never Add or mark."""
    leader = {"ministry_leader"}
    assert pipeline.role_actions("ministry", leader) == frozenset()
    assert pipeline.role_actions("ministry", leader, leads=True) == REVIEW
    assert not pipeline.may_add("ministry", leader)
    assert pipeline.allowed_actions(
        "ministry", Stage.TO_ENTER_BY_HAND, leader, leads=True
    ) == {Action.BACK_TO_REVIEW}


def test_administrator_role_set_is_the_union():
    """An Administrator holds all three roles; the union is the Admin cell."""
    roles = {"administrator", "staff", "ministry_leader"}
    assert (
        pipeline.role_actions("census", roles)
        == ROLE_TABLE[("census", "administrator")]
    )
    assert pipeline.role_actions("ministry", roles) == ROLE_TABLE[("ministry", "staff")]


def test_add_is_offered_only_where_the_spec_allows():
    """Staff Add census and Ministry changes; nothing else takes Add."""
    staff = {"staff"}
    assert [stream.value for stream in Stream if pipeline.may_add(stream, staff)] == [
        "census",
        "ministry",
    ]


def test_unknown_role_is_refused():
    """A role outside the policy's three is refused."""
    with pytest.raises(ValueError):
        pipeline.role_actions("census", {"family"})


STAFF = {"staff"}
ADMIN = {"administrator", "staff", "ministry_leader"}
DECIDE = {"accept", "edit", "ignore"}
MARK = {"back_to_review", "entered_by_hand"}
HANDLE = {"back_to_review", "handled"}
# (stream, stage, roles, by_hand) -> the row's actions.
ALLOWED_TABLE = [
    ("census", Stage.TO_REVIEW, STAFF, True, DECIDE | {"entered_by_hand"}),
    ("census", Stage.TO_REVIEW, STAFF, False, DECIDE),
    ("census", Stage.CONFLICT, STAFF, False, DECIDE),
    ("census", Stage.IGNORED, STAFF, True, {"back_to_review"}),
    ("census", Stage.READY_TO_UPDATE, STAFF, False, {"back_to_review"}),
    ("census", Stage.READY_TO_UPDATE, ADMIN, False, {"back_to_review", "start_update"}),
    ("census", Stage.TO_ENTER_BY_HAND, STAFF, True, MARK),
    ("census", Stage.ENTERED_INCORRECTLY, STAFF, True, MARK),
    ("census", Stage.UPDATING, ADMIN, False, set()),
    # Every census terminal stage takes Back to review (#1840), never for a
    # Ministry leader.
    ("census", Stage.UPDATED, ADMIN, False, {"back_to_review"}),
    ("census", Stage.ENTERED_BY_HAND, ADMIN, True, {"back_to_review"}),
    ("census", Stage.ENTERED_BY_HAND, STAFF, True, {"back_to_review"}),
    ("census", Stage.CONFIRMED, ADMIN, True, {"back_to_review"}),
    ("census", Stage.CONFIRMED, STAFF, False, {"back_to_review"}),
    ("census", Stage.ENTERED_BY_HAND, {"ministry_leader"}, True, set()),
    ("census", Stage.HISTORY, ADMIN, True, set()),
    ("census", Stage.TO_REVIEW, {"ministry_leader"}, True, set()),
    ("ministry", Stage.TO_REVIEW, STAFF, False, DECIDE),
    ("ministry", Stage.TO_ENTER_BY_HAND, STAFF, False, MARK),
    ("ministry", Stage.ENTERED_BY_HAND, STAFF, False, {"back_to_review"}),
    ("ministry", Stage.CONFIRMED, ADMIN, False, {"back_to_review"}),
    ("ministry", Stage.ENTERED_BY_HAND, {"ministry_leader"}, False, set()),
    ("ministry", Stage.CONFIRMED, {"ministry_leader"}, False, set()),
    ("financial", Stage.TO_REVIEW, STAFF, False, DECIDE),
    ("financial", Stage.TO_ENTER_BY_HAND, STAFF, False, MARK),
    # Back to review undoes a pledge's terminal step (#1840), Staff and
    # Administrators only.
    ("financial", Stage.ENTERED_BY_HAND, STAFF, False, {"back_to_review"}),
    ("financial", Stage.ENTERED_INCORRECTLY, ADMIN, False, MARK),
    ("financial", Stage.CONFIRMED, ADMIN, False, {"back_to_review"}),
    ("financial", Stage.CONFIRMED, {"ministry_leader"}, False, set()),
    ("cannot_participate", Stage.TO_REVIEW, STAFF, False, {"accept", "ignore"}),
    ("cannot_participate", Stage.TO_HANDLE, STAFF, False, HANDLE),
    ("additional_information", Stage.TO_REVIEW, ADMIN, False, {"accept", "ignore"}),
    ("additional_information", Stage.HANDLED, ADMIN, False, {"back_to_review"}),
]  # fmt: skip


@pytest.mark.parametrize(
    ("stream", "stage", "roles", "manual", "expected"), ALLOWED_TABLE
)
def test_row_actions_are_stage_and_role(stream, stage, roles, manual, expected):
    """A row offers what its stage offers and the reader's role allows."""
    assert (
        pipeline.allowed_actions(stream, stage, roles, leads=True, by_hand=manual)
        == expected
    )


# (stream, placement, by_hand) -> where Back to review sends the item (#1840).
UNDO_TABLE = [
    ("census", Placement(Stage.TO_ENTER_BY_HAND, A), True, Stage.TO_REVIEW),
    ("census", Placement(Stage.IGNORED, IG), True, Stage.TO_REVIEW),
    ("census", Placement(Stage.ENTERED_BY_HAND, A), True, Stage.TO_ENTER_BY_HAND),
    ("census", Placement(Stage.ENTERED_INCORRECTLY, AE), True, Stage.TO_ENTER_BY_HAND),
    ("census", Placement(Stage.CONFIRMED, A), True, Stage.TO_ENTER_BY_HAND),
    ("census", Placement(Stage.CONFIRMED, A), False, Stage.READY_TO_UPDATE),
    ("census", Placement(Stage.CONFIRMED), False, Stage.TO_REVIEW),
    ("census", Placement(Stage.UPDATED, A), False, Stage.READY_TO_UPDATE),
    ("census", Placement(Stage.ENTERED_BY_HAND, ADD), True, Stage.TO_ENTER_BY_HAND),
    ("ministry", Placement(Stage.ENTERED_BY_HAND, A), False, Stage.TO_ENTER_BY_HAND),
    ("ministry", Placement(Stage.CONFIRMED), False, Stage.TO_REVIEW),
    ("financial", Placement(Stage.CONFIRMED, AE), False, Stage.TO_ENTER_BY_HAND),
    ("financial", Placement(Stage.CONFIRMED), False, Stage.TO_REVIEW),
    ("cannot_participate", Placement(Stage.HANDLED, A), False, Stage.TO_HANDLE),
    ("additional_information", Placement(Stage.HANDLED, A), False, Stage.TO_HANDLE),
]  # fmt: skip


@pytest.mark.parametrize(("stream", "placement", "manual", "expected"), UNDO_TABLE)
def test_back_to_review_returns_to_the_stage_before_the_step(
    stream, placement, manual, expected
):
    """A terminal step's undo keeps the decision; a decision's undo reviews."""
    assert pipeline.undo_stage(stream, placement, by_hand=manual) is expected


@pytest.mark.parametrize("stage", sorted(pipeline.TERMINAL_STAGES))
def test_terminal_undo_is_offered_once_a_stream_records_it(monkeypatch, stage):
    """Back to review on a terminal stage: Staff and Administrators, a note.

    A stream offers it only once its service and guard record it
    (``TERMINAL_UNDO_STREAMS``), and never to a Ministry leader.
    """
    monkeypatch.setattr(pipeline, "TERMINAL_UNDO_STREAMS", frozenset(Stream))
    for stream in Stream:
        offered = pipeline.allowed_actions(stream, stage, STAFF, by_hand=True)
        assert Action.BACK_TO_REVIEW in offered
        assert pipeline.undo_needs_note(stream, stage)
        leader = pipeline.allowed_actions(
            stream, stage, {"ministry_leader"}, leads=True, by_hand=True
        )
        assert leader == set()
    monkeypatch.setattr(pipeline, "TERMINAL_UNDO_STREAMS", frozenset())
    assert not pipeline.undo_needs_note("financial", stage)
    assert pipeline.undo_needs_note("financial", Stage.IGNORED)
    assert not pipeline.undo_needs_note("financial", Stage.TO_ENTER_BY_HAND)


def test_confirmable_covers_the_handling_registry():
    """Every registry field says whether a refresh can confirm it."""
    registry = {
        (entity, field)
        for entity, fields in capabilities._REGISTRY.items()
        for field in fields
    }
    assert set(pipeline.CENSUS_CONFIRMABLE) == registry
    not_confirmable = {
        key for key, value in pipeline.CENSUS_CONFIRMABLE.items() if not value
    }
    assert not_confirmable == {
        ("family", "home_address"),
        ("family", "mailing_address"),
        ("family", "frequency"),
        ("family", "share_methods"),
    }
    # The email opt-out is the Family's sendNoMail flag (2026-10-18).
    assert pipeline.CENSUS_CONFIRMABLE[("family", "email_opt_out")]


def refresh(stage, r, a, c, **options):
    """Run the R/A/C comparison."""
    return pipeline.refresh_outcome(
        stage, refreshed=r, answered_against=a, carried_forward=c, **options
    )


OUT = RefreshOutcome
OPEN_STAGES = [
    Stage.TO_REVIEW,
    Stage.CONFLICT,
    Stage.READY_TO_UPDATE,
    Stage.UPDATING,
    Stage.UPDATED,
    Stage.TO_ENTER_BY_HAND,
]
# (stage, R, A, C, options) -> outcome, following the spec's rules in order.
REFRESH_TABLE = [
    # Rule 1: R = C confirms from any re-evaluated stage.
    *[((stage, "new", "old", "new", {}), OUT.CONFIRMED) for stage in OPEN_STAGES],
    ((Stage.ENTERED_BY_HAND, "new", "old", "new", {}), OUT.CONFIRMED),
    ((Stage.ENTERED_INCORRECTLY, "new", "old", "new", {}), OUT.CONFIRMED),
    # ...but an Ignored item is only noted.
    ((Stage.IGNORED, "new", "old", "new", {}), OUT.IGNORED_NOW_MATCHES),
    # Rule 2: R = A is unchanged...
    *[
        ((stage, "old", "old", "new", {}), OUT.UNCHANGED)
        for stage in OPEN_STAGES
        if stage is not Stage.UPDATED
    ],
    # ...except an Updated item back at A: the write was undone.
    ((Stage.UPDATED, "old", "old", "new", {}), OUT.CONFLICT),
    ((Stage.IGNORED, "old", "old", "new", {}), OUT.UNCHANGED),
    ((Stage.ENTERED_BY_HAND, "old", "old", "new", {}), OUT.UNCHANGED),
    (
        (Stage.ENTERED_BY_HAND, "old", "old", "new", {"full_refreshes_unchanged": 1}),
        OUT.UNCHANGED,
    ),
    # Not seen in ParishSoft after two full refreshes (the default).
    (
        (Stage.ENTERED_BY_HAND, "old", "old", "new", {"full_refreshes_unchanged": 2}),
        OUT.NOT_SEEN,
    ),
    (
        (
            Stage.ENTERED_BY_HAND,
            "old",
            "old",
            "new",
            {"full_refreshes_unchanged": 2, "not_seen_after": 3},
        ),
        OUT.UNCHANGED,
    ),
    # Not seen is a flag on Entered by hand only.
    (
        (Stage.TO_ENTER_BY_HAND, "old", "old", "new", {"full_refreshes_unchanged": 5}),
        OUT.UNCHANGED,
    ),
    # Rule 3: R differs from both.
    *[((stage, "third", "old", "new", {}), OUT.CONFLICT) for stage in OPEN_STAGES],
    ((Stage.ENTERED_BY_HAND, "third", "old", "new", {}), OUT.ENTERED_INCORRECTLY),
    ((Stage.ENTERED_INCORRECTLY, "third", "old", "new", {}), OUT.ENTERED_INCORRECTLY),
    # Compared with the value recorded at the mark when one was recorded.
    (
        (Stage.ENTERED_BY_HAND, "third", "old", "new", {"marked_against": "third"}),
        OUT.UNCHANGED,
    ),
    (
        (Stage.ENTERED_BY_HAND, "old", "old", "new", {"marked_against": "third"}),
        OUT.ENTERED_INCORRECTLY,
    ),
    # An Ignored item ParishSoft moved elsewhere stays Ignored.
    ((Stage.IGNORED, "third", "old", "new", {}), OUT.UNCHANGED),
    # Rule 4: the Member or Family left the baseline.
    ((Stage.TO_REVIEW, None, "old", "new", {"present": False}), OUT.ENTITY_GONE),
    ((Stage.ENTERED_BY_HAND, None, "old", "new", {"present": False}), OUT.ENTITY_GONE),
    # Ministry roster state: rule 3 cannot apply to booleans.
    ((Stage.TO_ENTER_BY_HAND, True, False, True, {}), OUT.CONFIRMED),
    ((Stage.ENTERED_BY_HAND, False, False, True, {}), OUT.UNCHANGED),
    ((Stage.TO_REVIEW, False, True, False, {}), OUT.CONFIRMED),
]


@pytest.mark.parametrize(("case", "expected"), REFRESH_TABLE)
def test_refresh_rule(case, expected):
    """The spec's re-evaluation rules, in order."""
    stage, r, a, c, options = case
    assert refresh(stage, r, a, c, **options) is expected


def test_refresh_uses_the_streams_canonical_comparison():
    """A formatting difference is neither a conflict nor an incorrect entry."""

    def money(left, right):
        """Compare amounts in their typed form."""
        return Decimal(left) == Decimal(right)

    assert refresh(Stage.ENTERED_BY_HAND, "100.0", "0", "100", same=money) is (
        OUT.CONFIRMED
    )
    assert refresh(Stage.ENTERED_BY_HAND, "100.0", "0", "100") is (
        OUT.ENTERED_INCORRECTLY
    )


@pytest.mark.parametrize(
    "stage", [Stage.CONFIRMED, Stage.HISTORY, Stage.TO_HANDLE, Stage.HANDLED]
)
def test_refresh_refuses_stages_it_does_not_re_evaluate(stage):
    """Closed stages and data ParishSoft does not hold have nothing to compare."""
    with pytest.raises(ValueError):
        refresh(stage, "new", "old", "new")


# A final census change's latest refresh check (#1689): (decision,
# execution, check) -> expected placement.
CENSUS_CHECK_TABLE = [
    (
        ("approved", "resolved_external", None),
        Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED),
    ),
    (
        ("approved", "resolved_external", "unchanged"),
        Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED),
    ),
    (
        ("approved", "resolved_external", "confirmed"),
        Placement(Stage.CONFIRMED, Decision.ACCEPTED),
    ),
    (
        ("unreviewed", "resolved_external", "confirmed"),
        Placement(Stage.CONFIRMED, Decision.ACCEPTED),
    ),
    (
        ("approved", "resolved_external", "entered_incorrectly"),
        Placement(Stage.ENTERED_INCORRECTLY, Decision.ACCEPTED),
    ),
    (
        ("approved", "published", "confirmed"),
        Placement(Stage.CONFIRMED, Decision.ACCEPTED),
    ),
    # A published change cannot be Entered incorrectly; an open one's check
    # is not read (it has none).
    (
        ("approved", "published", "entered_incorrectly"),
        Placement(Stage.UPDATED, Decision.ACCEPTED),
    ),
    (
        ("approved", "pending", "confirmed"),
        Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED),
    ),
]


@pytest.mark.parametrize(("state", "expected"), CENSUS_CHECK_TABLE)
def test_census_refresh_check_moves_a_final_change(state, expected):
    """Confirmed and Entered incorrectly come from the latest check, keeping
    the decision; an unchanged check leaves the stage."""
    decision, execution, check = state
    assert (
        pipeline.census_placement(
            decision=decision,
            execution=execution,
            handling="manual",
            entity_kind="member",
            admin_value_set=False,
            check=check,
        )
        == expected
    )


def test_census_refuses_an_unknown_check():
    """A check outcome the census stream never stores is refused."""
    with pytest.raises(ValueError):
        pipeline.census_placement(
            decision="approved",
            execution="resolved_external",
            handling="manual",
            entity_kind="member",
            admin_value_set=False,
            check="ignored_now_matches",
        )


def test_ministry_refresh_confirmation_keeps_the_acceptance():
    """A person's accepted request a refresh check Confirmed is Confirmed with
    its Accepted; a refresh-resolved one has no reviewer."""
    confirmed = pipeline.ministry_placement(
        state="resolved",
        outcome="joined",
        source_resolved=False,
        roster_entered=True,
        confirmed=True,
    )
    assert confirmed == Placement(Stage.CONFIRMED, Decision.ACCEPTED, outcome="joined")
    assert ministry("resolved", "joined", source=True) == Placement(
        Stage.CONFIRMED, outcome="joined"
    )
    with pytest.raises(ValueError):
        pipeline.ministry_placement(
            state="resolved",
            outcome="joined",
            source_resolved=False,
            roster_entered=False,
            confirmed="yes",
        )
