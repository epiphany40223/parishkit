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

from parishkit.stewardship.pipeline import Decision, Placement, Stage, Stream
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
)
BY_HAND_STAGES = (Stage.TO_ENTER_BY_HAND, Stage.ENTERED_BY_HAND)
TEMPLATES = Path(__file__).parents[2] / (
    "src/parishkit/stewardship/accounts/templates/stewardship"
)
BUTTON = re.compile(r"<button\b[^>]*>")


def drawn_rows(stream, roles, placements, *, by_hand=False):
    """The rendered worklist's buttons, one list of tags per row."""
    shown = [
        {
            "id": str(index),
            "version": 1,
            "name": f"Item {index}",
            "placement": placement,
            "who": f"Member {index}",
            "leads": True,
            "by_hand": by_hand,
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


def test_no_row_draws_an_undo_button_beside_a_decision():
    """Every stream, stage, decision and role: a row with a pressed button
    draws no Back to review button, and its pressed undo opens Back to
    review's dialog."""
    checked = 0
    for stream in Stream:
        for roles in READERS:
            for by_hand in (False, True):
                placements = [
                    Placement(stage, decision)
                    for stage in Stage
                    for decision in (None, *Decision)
                ]
                for buttons in drawn_rows(stream, roles, placements, by_hand=by_hand):
                    if not any('aria-pressed="true"' in tag for tag in buttons):
                        continue
                    checked += 1
                    assert not any(
                        'data-row-action="back_to_review"' in tag for tag in buttons
                    ), buttons
                    for tag in buttons:
                        if "data-row-undo" in tag:
                            assert 'aria-pressed="true"' in tag, tag
                            assert "decide=back_to_review" in tag, tag
                            assert "aria-disabled" not in tag, tag
    assert checked


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
