"""Guard: decision buttons are toggles, with no undo button beside them (#2131).

UX conventions, "Decision toggles": the decision in force is its own button
drawn pressed, and choosing it again undoes it, so no row draws a separate
Back to review (or other undo) button beside a decision, and a row draws the
same buttons whatever its stage, so none appears or disappears when a
decision is made.
"""

import re
from pathlib import Path

from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import (
    CENSUS_ADDITION_STATES,
    Decision,
    Placement,
    Stage,
    Stream,
    census_addition_actions,
    census_addition_placement,
)
from parishkit.stewardship.web.review_worklist import Column, build
from parishkit.stewardship.web.tables import paginate

READERS = (
    frozenset({"staff"}),
    frozenset({"ministry_leader"}),
    frozenset({"administrator"}),
)
# The stages a Family's item moves through by a decision or mark, in no
# order: a row draws the same buttons at each of them.
DECIDED = (
    Placement(Stage.TO_REVIEW),
    Placement(Stage.IGNORED, Decision.IGNORED),
    Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED),
    Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED),
    Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED),
    Placement(Stage.TO_HANDLE, Decision.ACCEPTED),
    Placement(Stage.HANDLED, Decision.ACCEPTED),
    # A step no decision was behind (ParishSoft already held it): Accept is
    # drawn pressed as its undo, so the row keeps its buttons.
    Placement(Stage.CONFIRMED),
)
BY_HAND_STAGES = (Stage.TO_ENTER_BY_HAND, Stage.ENTERED_BY_HAND)
TEMPLATES = Path(__file__).parents[2] / (
    "src/parishkit/stewardship/accounts/templates/stewardship"
)
BUTTON = re.compile(r"<button\b[^>]*>")


def drawn_rows(stream, roles, placements, *, by_hand=False, offers=None):
    """The rendered worklist's buttons, one list of tags per row.

    ``offers``, when given, is each placement's own actions (a census
    addition's, #2041), in the same order as ``placements``.
    """
    shown = [
        {
            "id": str(index),
            "version": 1,
            "name": f"Item {index}",
            "placement": placement,
            "who": f"Member {index}",
            "leads": True,
            "by_hand": by_hand,
            **({"offers": offers[index]} if offers is not None else {}),
        }
        for index, placement in enumerate(placements)
    ]
    table = paginate(shown, {"size": "25"}, carry=[("stage", "all")])
    worklist = build(
        stream,
        table.rows,
        roles,
        prefix="guard",
        action="/review",
        columns=[Column("who", "Who")],
        stage="all",
        counts={},
        stages=[],
    )
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": worklist, "table": table, "csrf_token": "x"},
    )
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.S)
    return [
        [tag for tag in BUTTON.findall(row) if "data-row-action" in tag]
        for row in rows
        if "data-row-action" in row
    ]


def check_rows(rows):
    """Assert no row draws a Back to review button, and that each pressed
    undo opens Back to review's dialog; return how many undos were seen."""
    undos = 0
    for buttons in rows:
        assert not any('data-row-action="back_to_review"' in tag for tag in buttons), (
            buttons
        )
        for tag in buttons:
            if "data-row-undo" in tag:
                undos += 1
                assert 'aria-pressed="true"' in tag, tag
                assert "decide=back_to_review" in tag, tag
                assert "aria-disabled" not in tag, tag
    return undos


def test_no_row_draws_an_undo_button():
    """Every stream, stage, decision and role, a confirmed item with no
    decision behind it included: no row draws a Back to review (or other
    undo) button of its own, and its pressed undo opens Back to review's
    dialog (the Administrator's rule is universal, #2131)."""
    undos = 0
    for stream in Stream:
        for roles in READERS:
            for by_hand in (False, True):
                placements = [
                    Placement(stage, decision)
                    for stage in Stage
                    for decision in (None, *Decision)
                ]
                undos += check_rows(
                    drawn_rows(stream, roles, placements, by_hand=by_hand)
                )
    assert undos


def test_a_confirmed_row_presses_accept_as_its_undo():
    """A Confirmed item no decision was behind draws Accept pressed, named
    for what it is and what choosing it does."""
    for stream in Stream:
        (buttons,) = drawn_rows(
            stream, frozenset({"staff"}), [Placement(Stage.CONFIRMED)]
        )
        (undo,) = [tag for tag in buttons if "data-row-undo" in tag]
        assert 'data-row-action="accept"' in undo, undo
        assert (
            'title="Already in ParishSoft; select to send Item 0 back to review"'
            in undo
        ), undo


def test_no_census_addition_draws_an_undo_button():
    """A parish-added census change's own actions (#2041): Not entered yet
    is its pressed Entered by hand, never a button of its own."""
    states = sorted(CENSUS_ADDITION_STATES)
    for roles in READERS:
        rows = drawn_rows(
            Stream.CENSUS,
            roles,
            [census_addition_placement(state) for state in states],
            by_hand=True,
            offers=[census_addition_actions(state) for state in states],
        )
        undos = check_rows(rows)
        if "staff" in roles or "administrator" in roles:
            assert undos == 1, rows


def test_a_row_keeps_its_buttons_whatever_its_decision():
    """The same buttons, in the same order, at every stage a decision or a
    mark moves an item to, so nothing appears or disappears under the
    pointer (UX conventions, "No layout shift")."""
    for stream in Stream:
        for roles in READERS:
            for by_hand in (False, True):
                placements = DECIDED
                if stream is Stream.CENSUS and not by_hand:
                    # The update run carries it: it is never entered by hand.
                    placements = [p for p in DECIDED if p.stage not in BY_HAND_STAGES]
                rows = drawn_rows(stream, roles, placements, by_hand=by_hand)
                kinds = {
                    tuple(re.findall(r'data-row-action="([^"]+)"', " ".join(row)))
                    for row in rows
                }
                assert len(kinds) <= 1, (stream, roles, by_hand, kinds)


def test_no_template_draws_the_undo_arrow():
    """The undo arrow ("clear") was the separate undo button's icon; no
    Admin template draws it any longer (the review worklist's own Back to
    review, on a row with nothing pressed, is drawn from its words)."""
    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r'action-icon\.html" with icon="clear"', source), path
