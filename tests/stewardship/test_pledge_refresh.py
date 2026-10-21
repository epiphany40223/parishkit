"""Pledge refresh checks' pure parts and their page and files (#1704).

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
    """A latest refresh check as ``pledge_check_states`` reads it."""
    return CheckState(outcome, Decimal(observed), unseen, 2)


@pytest.mark.parametrize(
    "decision,entered,outcome,stage",
    [
        ("accepted", False, "confirmed", Stage.CONFIRMED),
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


def test_a_check_needs_a_decision_and_a_known_outcome():
    """An undecided pledge has no check; an unknown outcome is a defect."""
    with pytest.raises(ValueError):
        financial_placement(check="confirmed")
    with pytest.raises(ValueError):
        financial_placement(decision="accepted", check="conflict")


def test_placement_reads_the_check_after_the_shared_selection():
    """The SQL stage is checked first; the check then moves the pledge on,
    and a pledge the Family changed since (not in force) ignores it."""
    marked = item("entered_by_hand", review(entered=True))
    assert placement(marked, check("confirmed")).stage is Stage.CONFIRMED
    assert (
        placement(marked, check("entered_incorrectly")).stage
        is Stage.ENTERED_INCORRECTLY
    )
    changed = item("to_review", review(entered=True), in_force=False)
    assert placement(changed, check("confirmed")).stage is Stage.TO_REVIEW
    assert not checked(changed)
    assert checked(marked) and not checked(item())


def test_entered_incorrectly_and_confirmed_offer_nothing():
    """Entered incorrectly's fixes wait for #1737; Confirmed is closed."""
    roles = STAFF
    assert offered(Stage.ENTERED_INCORRECTLY, roles) == frozenset()
    assert offered(Stage.CONFIRMED, roles) == frozenset()
    assert Action.ENTERED_BY_HAND in offered(Stage.TO_ENTER_BY_HAND, roles)


def shown(found, latest):
    """One page row of ``found`` with ``latest`` as its refresh check."""
    row = pledge_row(found, check=latest, placement=placement(found, latest))
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


def test_entered_incorrectly_shows_parishsoft_now_and_no_action():
    """ParishSoft now sits beside the Reviewer's value; no icon is offered."""
    found = item("entered_by_hand", review(entered=True))
    row = shown(found, check("entered_incorrectly", "1100.00"))
    worklist = drawn([row], Stage.ENTERED_INCORRECTLY.value)
    (drawn_row,) = worklist.rows
    assert drawn_row["stage_label"] == "Entered incorrectly"
    assert drawn_row["stage_look"] == "error"
    assert worklist.shows_now and drawn_row["parishsoft_now"] == "$1,100.00"
    assert [icon["action"] for icon in drawn_row["icons"] if not icon["selected"]] == []


def test_not_seen_is_a_choice_and_a_flag_in_the_files():
    """The flag is counted beside Entered by hand, chosen on its own, and
    the files word it after the stage."""
    flagged = shown(
        item("entered_by_hand", review(entered=True)),
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
    row = shown(item("ignored", review("ignored")), check("ignored_now_matches"))
    (drawn_row,) = drawn([row], Stage.IGNORED.value).rows
    assert drawn_row["stage_label"] == "Ignored"
    assert NOW_MATCHES in drawn_row["decision_label"]


def test_a_confirmed_pledge_names_its_amount_to_enter_and_its_mark():
    """The files still name the pledge entered and who marked it."""
    found = item("entered_by_hand", review(entered=True))
    row = shown(found, check("confirmed"))
    (line,) = views.export_rows([row], ZoneInfo("UTC"))
    values = dict(zip(views.HEADINGS, line, strict=True))
    assert values["Stage"] == "Confirmed"
    assert values["Annual pledge to enter"] == "$1,200.00"
    assert values["Entered by"] == "staff@example.org"
    assert values["ParishSoft annual pledge"] == "$1,200.00"
