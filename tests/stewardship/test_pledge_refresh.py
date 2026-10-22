"""Pledge refresh checks' pure parts and their page and files (#1704, #1747).

The checks themselves are written by real promotions in
``database/test_pledge_refresh_postgresql.py``; these tests cover how a
check places a pledge (``pipeline.financial_placement``), what the page's
rows and filter show for it, and what the files say.
"""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.pipeline import (
    Action,
    Decision,
    Placement,
    Stage,
    Stream,
    financial_placement,
)
from parishkit.stewardship.reports import pledge_review_views as views
from parishkit.stewardship.reports.pledge_refresh import checked
from parishkit.stewardship.reports.pledge_review import offered, placement
from parishkit.stewardship.responses.refresh_checks import CheckState
from parishkit.stewardship.web.review_worklist import NOT_SEEN, NOW_MATCHES, build

from .test_pledge_review import STAFF, item, pledge_row, review

AT = datetime(2054, 10, 6, 15, 30, tzinfo=UTC)


def check(outcome, observed="1200.00", unseen=0):
    """A decided pledge's check run as ``pledge_check_states`` reads it."""
    return CheckState(outcome, Decimal(observed), unseen, 2)


@pytest.mark.parametrize(
    "decision,entered,outcome,stage",
    [
        # Only a marked pledge is Confirmed (#1890): an accepted one not yet
        # marked whose check found it in ParishSoft stays To enter by hand.
        ("accepted", False, "confirmed", Stage.TO_ENTER_BY_HAND),
        ("accepted_edited", True, "confirmed", Stage.CONFIRMED),
        ("accepted", True, "entered_incorrectly", Stage.ENTERED_INCORRECTLY),
        ("accepted", True, "unchanged", Stage.ENTERED_BY_HAND),
        ("accepted", False, "unchanged", Stage.TO_ENTER_BY_HAND),
        ("ignored", False, "ignored_now_matches", Stage.IGNORED),
        ("ignored", False, "unchanged", Stage.IGNORED),
    ],
)
def test_a_check_moves_a_decided_pledge_on(decision, entered, outcome, stage):
    """Confirmed and Entered incorrectly keep the decision behind them; an
    Ignored pledge's note never changes its stage."""
    found = financial_placement(decision=decision, entered=entered, check=outcome)
    assert found.stage is stage
    assert found.decision is (
        Decision.IGNORED if decision == "ignored" else Decision(decision)
    )


@pytest.mark.parametrize("decision", ["accepted", "accepted_edited"])
def test_a_conflict_holds_an_accepted_unmarked_pledge_decision(decision):
    """A third amount under an accepted pledge not yet marked is a Conflict
    that keeps its decision visible but not in force (#1747)."""
    assert financial_placement(decision=decision, check="conflict") == Placement(
        Stage.CONFLICT, held_decision=Decision(decision)
    )


@pytest.mark.parametrize(
    "outcome,stage",
    [
        (None, Stage.TO_REVIEW),
        ("unchanged", Stage.TO_REVIEW),
        ("confirmed", Stage.TO_REVIEW),
        ("conflict", Stage.CONFLICT),
    ],
)
def test_a_to_review_pledge_check_places_it_with_no_decision(outcome, stage):
    """A Conflict with nothing held (#1747); a check that found the pledge
    in ParishSoft leaves it To review (#1890: never Confirmed before its
    review and mark)."""
    assert financial_placement(check=outcome) == Placement(stage)


def test_a_check_needs_a_known_outcome_for_its_stage():
    """A To review pledge has no Entered incorrectly or Ignored note; an
    unknown outcome is a defect."""
    for outcome in ("entered_incorrectly", "ignored_now_matches", "lost"):
        with pytest.raises(ValueError):
            financial_placement(check=outcome)
    with pytest.raises(ValueError):
        financial_placement(decision="accepted", check="lost")


def test_placement_agrees_with_the_shared_selection():
    """The SQL selection's stage, check included, must equal the Python one;
    a pledge the Family changed since (not in force) has no decided check."""
    marked = item("confirmed", review(entered=True), check="confirmed")
    assert placement(marked).stage is Stage.CONFIRMED
    wrong = item("entered_by_hand", review(entered=True), check="entered_incorrectly")
    with pytest.raises(ValueError):
        placement(wrong)
    changed = item("to_review", review(entered=True), in_force=False)
    assert placement(changed).stage is Stage.TO_REVIEW
    assert not checked(changed)
    entered = item("entered_by_hand", review(entered=True))
    assert checked(entered) and not checked(item())


def test_entered_incorrectly_and_confirmed_go_back_a_conflict_is_decided():
    """Entered incorrectly and Confirmed offer Back to review (#1840; Entered
    by hand again waits for #1737), never to a leader; a Conflict is decided
    again (#1747)."""
    roles = STAFF
    assert offered(Stage.ENTERED_INCORRECTLY, roles) == {Action.BACK_TO_REVIEW}
    assert offered(Stage.CONFIRMED, roles) == {Action.BACK_TO_REVIEW}
    assert offered(Stage.CONFIRMED, {"ministry_leader"}) == frozenset()
    assert Action.ENTERED_BY_HAND in offered(Stage.TO_ENTER_BY_HAND, roles)
    assert offered(Stage.CONFLICT, roles) == {
        Action.ACCEPT,
        Action.EDIT,
        Action.IGNORE,
    }


def shown(found, latest=None):
    """One page row of ``found``, with ``latest`` as a decided pledge's check run."""
    row = pledge_row(found, check=latest, placement=placement(found))
    row["stage"] = row["placement"].stage.value
    row["not_seen"] = bool(
        latest and latest.not_seen(row["placement"].stage is Stage.ENTERED_BY_HAND)
    )
    return row


def drawn(rows, stage):
    """The worklist the page builds for ``rows`` at ``stage``."""
    counts = {}
    for row in rows:
        counts[row["stage"]] = counts.get(row["stage"], 0) + 1
        if row["not_seen"]:
            counts[NOT_SEEN] = counts.get(NOT_SEEN, 0) + 1
    return build(
        Stream.FINANCIAL,
        views.decorate(views.at_stage(rows, stage)),
        STAFF,
        prefix="pledges",
        action="/x/",
        columns=views.COLUMNS,
        stage=stage,
        counts=counts,
        stages=views.STAGES,
        baseline=False,
    )


def offered_icons(row):
    """The actions a drawn row offers (its unselected icons)."""
    return [icon["action"] for icon in row["icons"] if not icon["selected"]]


def test_entered_incorrectly_shows_parishsoft_now_and_back_to_review():
    """ParishSoft now sits beside the Reviewer's value; Back to review undoes
    the mark (#1840), and Entered by hand again waits for #1737."""
    found = item(
        "entered_incorrectly",
        review(entered=True),
        check="entered_incorrectly",
        observed="1100.00",
        base="entered_by_hand",
    )
    row = shown(found, check("entered_incorrectly", "1100.00"))
    worklist = drawn([row], Stage.ENTERED_INCORRECTLY.value)
    (drawn_row,) = worklist.rows
    assert drawn_row["stage_label"] == "Entered incorrectly"
    assert drawn_row["stage_look"] == "error"
    assert worklist.shows_now and drawn_row["parishsoft_now"] == "$1,100.00"
    assert offered_icons(drawn_row) == ["back_to_review"]


def test_a_to_review_conflict_is_decided_against_parishsoft_now():
    """A To review pledge ParishSoft holds at a third amount is a Conflict:
    ParishSoft now beside the Family's answer, Accept, Edit and Ignore."""
    found = item("conflict", check="conflict", observed="900.00", base="to_review")
    worklist = drawn([shown(found)], Stage.CONFLICT.value)
    (drawn_row,) = worklist.rows
    assert drawn_row["stage_label"] == "Conflict"
    assert worklist.shows_now and drawn_row["parishsoft_now"] == "$900.00"
    assert offered_icons(drawn_row) == ["accept", "edit", "ignore"]
    assert "not in force" not in str(drawn_row["decision_label"])
    (line,) = views.export_rows([shown(found)], ZoneInfo("UTC"))
    values = dict(zip(views.HEADINGS, line, strict=True))
    # Nothing is to be entered until the Conflict is decided.
    assert values["Stage"] == "Conflict"
    assert values["Annual pledge to enter"] == ""
    assert values["ParishSoft annual pledge"] == "$900.00"


def test_an_accepted_conflict_shows_its_earlier_decision_not_in_force():
    """An accepted pledge not yet marked at a third amount keeps its Accept
    visible, not in force, and offers the decision again."""
    found = item(
        "conflict",
        review(),
        check="conflict",
        observed="900.00",
        base="to_enter_by_hand",
    )
    (drawn_row,) = drawn([shown(found)], Stage.CONFLICT.value).rows
    assert "Earlier: Accepted, not in force." in str(drawn_row["decision_label"])
    assert offered_icons(drawn_row) == ["accept", "edit", "ignore"]


def test_a_pledge_parishsoft_already_holds_keeps_its_stage_and_says_so():
    """Strict phase order (#1890): a To review pledge whose check found it in
    ParishSoft stays To review, offering the decisions (never Entered by
    hand), and says so under its stage; an accepted one stays To enter by
    hand with Entered by hand; at $0.00 the words do not claim ParishSoft
    holds a pledge. A marked pledge's confirmed check Confirms it, with no
    such words."""
    found = item("to_review", check="confirmed", observed="1200.00", base="to_review")
    row = shown(found)
    (drawn_row,) = drawn([row], Stage.TO_REVIEW.value).rows
    assert drawn_row["stage_label"] == "To review"
    assert offered_icons(drawn_row) == ["accept", "edit", "ignore"]
    # The page's line under the stage (``page_context``).
    assert str(views._stage_line(row, "")) == views.HELD
    assert views.HELD == "ParishSoft already holds this value."
    accepted = shown(
        item("to_enter_by_hand", review(), check="confirmed", base="to_enter_by_hand")
    )
    (drawn_row,) = drawn([accepted], Stage.TO_ENTER_BY_HAND.value).rows
    assert offered_icons(drawn_row) == ["back_to_review", "entered_by_hand"]
    assert views.HELD in str(views._stage_line(accepted, ""))
    zero = shown(
        item("to_review", check="confirmed", observed="0.00", base="to_review")
    )
    assert str(views._stage_line(zero, "")) == views.NOTHING
    marked = shown(
        item(
            "confirmed",
            review(entered=True),
            check="confirmed",
            base="entered_by_hand",
        )
    )
    assert views.HELD not in str(views._stage_line(marked, ""))
    assert views.NOTHING not in str(views._stage_line(marked, ""))


def test_the_stage_choices_list_conflict_after_to_review():
    """Conflict follows To review and comes before To enter by hand."""
    assert views.STAGES[:3] == (
        Stage.TO_REVIEW,
        Stage.CONFLICT,
        Stage.TO_ENTER_BY_HAND,
    )
    rows = [
        shown(item("conflict", check="conflict", observed="9.00")),
        shown(item()),
    ]
    choices = drawn(rows, Stage.TO_REVIEW.value).stage_choices
    assert choices[:2] == [("to_review", "To review (1)"), ("conflict", "Conflict (1)")]


def test_not_seen_is_a_choice_and_a_flag_in_the_files():
    """The flag is counted beside Entered by hand, chosen on its own, and
    the files word it after the stage."""
    flagged = shown(
        item(
            "entered_by_hand",
            review(entered=True),
            check="unchanged",
            observed="0.00",
        ),
        check("unchanged", "0.00", unseen=2),
    )
    plain = shown(item("to_enter_by_hand", review()), None)
    assert flagged["not_seen"] and not plain["not_seen"]
    assert views.at_stage([flagged, plain], NOT_SEEN) == [flagged]
    worklist = drawn([flagged, plain], NOT_SEEN)
    assert (NOT_SEEN, "Not seen in ParishSoft (1)") in worklist.stage_choices
    assert ("all", "Every stage (2)") in worklist.stage_choices
    (line,) = views.export_rows([flagged], ZoneInfo("UTC"))
    values = dict(zip(views.HEADINGS, line, strict=True))
    assert values["Stage"] == "Entered by hand, Not seen in ParishSoft"
    assert values["ParishSoft annual pledge"] == "$0.00"
    assert values["Annual pledge to enter"] == "$1,200.00"


def test_an_ignored_pledge_parishsoft_now_matches_is_only_noted():
    """Refresh rule 1 on an Ignored pledge adds the note under its stage."""
    found = item("ignored", review("ignored"), check="ignored_now_matches")
    (drawn_row,) = drawn([shown(found)], Stage.IGNORED.value).rows
    assert drawn_row["stage_label"] == "Ignored"
    assert NOW_MATCHES in drawn_row["decision_label"]


def test_a_confirmed_pledge_names_its_amount_to_enter_and_its_mark():
    """The files still name the pledge entered and who marked it."""
    found = item(
        "confirmed",
        review(entered=True),
        check="confirmed",
        observed="1200.00",
        base="entered_by_hand",
    )
    (line,) = views.export_rows([shown(found)], ZoneInfo("UTC"))
    values = dict(zip(views.HEADINGS, line, strict=True))
    assert values["Stage"] == "Confirmed"
    assert values["Annual pledge to enter"] == "$1,200.00"
    assert values["Entered by"] == "staff@example.org"
    assert values["ParishSoft annual pledge"] == "$1,200.00"
