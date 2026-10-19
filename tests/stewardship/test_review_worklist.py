"""The shared review worklist's view helper and template (#1681)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from parishkit.stewardship.pipeline import Action, Decision, Placement, Stage
from parishkit.stewardship.web import review_worklist as worklist
from parishkit.stewardship.web.review_worklist import (
    Column,
    StaleDecision,
    WorklistDecision,
    build,
    bulk_buttons,
    read_decision,
    record,
    row_icons,
    stage_choices,
)
from parishkit.stewardship.web.tables import paginate

STAFF = {"staff"}
LEADER = {"ministry_leader"}
KEY = "00000000-0000-0000-0000-000000000001"


def icons(stream, placement, roles=STAFF, **flags):
    """(action, selected, pressed) of each icon a reader sees, in order."""
    return [
        (icon["action"], icon["selected"], icon["pressed"])
        for icon in row_icons(stream, placement, roles, name="Item", **flags)
    ]


@pytest.mark.parametrize(
    "placement,flags,expected",
    [
        # To review offers the decisions, none selected; Edit is no toggle.
        (
            Placement(Stage.TO_REVIEW),
            {},
            [("accept", False, False), ("edit", False, None), ("ignore", False, False)],
        ),
        # A By hand To review change also offers Entered by hand (decision 2).
        (
            Placement(Stage.TO_REVIEW),
            {"by_hand": True},
            [
                ("accept", False, False),
                ("edit", False, None),
                ("ignore", False, False),
                ("entered_by_hand", False, False),
            ],
        ),
        # The decision in force is drawn selected, beside its undo.
        (
            Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED),
            {},
            [("accept", True, True), ("back_to_review", False, None)],
        ),
        # Accepted (edited) selects Accept: Edit is Accept with a value.
        (
            Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED_EDITED),
            {"by_hand": True},
            [
                ("accept", True, True),
                ("back_to_review", False, None),
                ("entered_by_hand", False, False),
            ],
        ),
        (
            Placement(Stage.IGNORED, Decision.IGNORED),
            {},
            [("ignore", True, True), ("back_to_review", False, None)],
        ),
        # A mark in force is selected too, and final: no undo.
        (
            Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED),
            {},
            [("accept", True, True), ("entered_by_hand", True, True)],
        ),
        # Entered incorrectly's old mark is not in force: mark it again.
        (
            Placement(Stage.ENTERED_INCORRECTLY, Decision.ACCEPTED),
            {},
            [
                ("accept", True, True),
                ("back_to_review", False, None),
                ("entered_by_hand", False, False),
            ],
        ),
        # A Conflict's held decision is not in force.
        (
            Placement(Stage.CONFLICT, held_decision=Decision.ACCEPTED),
            {},
            [("accept", False, False), ("edit", False, None), ("ignore", False, False)],
        ),
    ],
)
def test_staff_census_icons_show_the_decision_in_force(placement, flags, expected):
    """Offered icons and the selected one follow the stage and the decision."""
    assert icons("census", placement, **flags) == expected


def test_a_leader_sees_only_what_their_role_allows():
    """On a Ministry they lead a leader reviews; never Entered by hand,
    selected or not; on another Ministry, nothing at all."""
    review = Placement(Stage.TO_REVIEW)
    assert [a for a, _, _ in icons("ministry", review, LEADER, leads=True)] == [
        "accept",
        "edit",
        "ignore",
    ]
    assert icons("ministry", review, LEADER, leads=False) == []
    entered = Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)
    assert icons("ministry", entered, LEADER, leads=True) == [("accept", True, True)]
    assert icons("census", review, LEADER) == []


def test_selected_icons_are_named_and_filled():
    """A selected icon keeps the action's name, says the state in its
    tooltip and takes its status look; an unselected one is plain."""
    edited = Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED_EDITED)
    accept, back = row_icons("census", edited, STAFF, name="Phone for Pat")
    assert accept["label"] == "Accept Phone for Pat"
    assert accept["title"] == "Accepted (edited); choose Back to review to change it"
    assert accept["css"] == "icon-button is-selected status-working"
    assert back["css"] == "icon-button" and back["label"] == (
        "Send Phone for Pat back to review"
    )
    # Once marked there is no undo, so the tooltip names none.
    marked = Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)
    assert [icon["title"] for icon in row_icons("census", marked, STAFF, name="x")] == [
        "Accepted",
        "Entered by hand",
    ]
    assert accept["focus"] == "accept"
    edit = row_icons("census", Placement(Stage.TO_REVIEW), STAFF, name="x")[1]
    assert edit["action"] == "edit" and edit["focus"] == "accept"
    ignored = Placement(Stage.IGNORED, Decision.IGNORED)
    assert row_icons("census", ignored, STAFF, name="x")[0]["css"].endswith(
        "status-unknown"
    )


def test_bulk_buttons_follow_the_role():
    """Staff get every bulk decision their stream has; a leader no mark;
    no stream offers Edit or Add in bulk."""
    values = lambda stream, roles: [b["value"] for b in bulk_buttons(stream, roles)]  # noqa: E731
    assert values("census", STAFF) == [
        "accept",
        "ignore",
        "back_to_review",
        "entered_by_hand",
    ]
    assert values("ministry", LEADER) == ["accept", "ignore", "back_to_review"]
    assert values("additional_information", STAFF) == [
        "accept",
        "ignore",
        "back_to_review",
        "handled",
    ]
    assert values("census", LEADER) == []
    back = bulk_buttons("census", STAFF, items="changes")[2]
    assert back["choice"] == "reopen"
    assert back["title_many"] == "Send {count} changes back to review?"


def test_stage_choices_count_each_stage_and_end_with_every_stage():
    """To review first, the Not seen flag when counted, then Every stage."""
    choices = stage_choices(
        {"to_review": 1200, "ignored": 3, "not_seen": 2},
        [Stage.TO_REVIEW, Stage.CONFLICT, Stage.IGNORED],
    )
    assert choices == [
        ("to_review", "To review (1,200)"),
        ("conflict", "Conflict (0)"),
        ("ignored", "Ignored (3)"),
        ("not_seen", "Not seen in ParishSoft (2)"),
        ("all", "Every stage (1,203)"),
    ]


def rows():
    """Three census rows: To review, Ready to update and Entered by hand."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)
    shaped = []
    for index, placement in enumerate(
        (
            Placement(Stage.TO_REVIEW),
            Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED),
            Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED),
        )
    ):
        shaped.append(
            {
                "id": str(index),
                "version": 3,
                "name": f"Phone for Member {index}",
                "placement": placement,
                "who": f"Member {index}",
                "family_answer": "555-0101",
                "submitted_at": when,
            }
        )
    return shaped


def made(roles=STAFF, shown=None, **options):
    """A worklist built from ``rows()`` (or ``shown``) for ``roles``."""
    return build(
        "census",
        rows() if shown is None else shown,
        roles,
        prefix="census",
        action="/review",
        columns=[Column("who", "Who")],
        stage="all",
        counts={"to_review": 1},
        stages=[Stage.TO_REVIEW],
        items="changes",
        **options,
    )


def test_build_gives_each_row_its_box_kinds_and_posts():
    """A row's box lists the bulk actions its icons offer; a row with none
    has no box; each icon posts its decision, version and request key."""
    built = made()
    first, ready, entered = built.rows
    assert first["kinds"] == "accept ignore"
    assert ready["kinds"] == "back_to_review" and ready["choice"] == ""
    assert entered["kinds"] == ""
    accept = first["icons"][0]
    assert accept["dialog"] == "census-decide-dialog"
    assert accept["post"].startswith("decide=accept&version=3&request_key=")
    assert first["icons"][1]["dialog"] == "census-edit-dialog-0"
    assert first["offers_edit"] and not ready["offers_edit"]
    assert built.fields == {"stage": "all"}
    assert built.selectable and not built.add
    assert [kind for kind, _, _ in built.dialogs] == ["decide", "bulk"]
    # Who, Baseline, Family's answer, Reviewer's value, Stage, Submitted,
    # Last decision, Actions and the selection column.
    assert built.span == 9


def test_add_needs_the_role_and_an_editor():
    """Add shows for Staff only once the stream gives its editor."""
    assert made(add_template="editor.html").add
    assert not made(LEADER, add_template="editor.html").add
    assert not made(STAFF).add


def test_too_many_rows_turn_the_bulk_bar_off():
    """More rows than one bulk decision covers: no selection column."""
    many = [dict(rows()[0], id=str(index)) for index in range(worklist.MAX_BULK + 1)]
    built = made(shown=many)
    assert built.bulk_too_many and not built.selectable
    assert [kind for kind, _, _ in built.dialogs] == ["decide"]


def test_the_template_draws_selected_icons_and_the_filters_open():
    """The rendered worklist: Stage filter outside any disclosure, a box
    only on rows with a bulk action, and selected icons pressed and
    unavailable with no dialog to open."""
    from django.template.loader import render_to_string

    table = paginate(rows(), {"size": "25"}, carry=[("stage", "all")])
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": made(shown=table.rows), "table": table, "csrf_token": "x"},
    )
    assert "<details" not in html.split('id="table-filters"')[0][-200:]
    assert html.count("data-select-row") == 2
    selected = html.split('aria-label="Accept Phone for Member 1"')[1].split(">")[0]
    assert 'aria-pressed="true"' in selected and 'aria-disabled="true"' in selected
    assert "data-confirm-open" not in selected
    offered = html.split('aria-label="Accept Phone for Member 0"')[1].split(">")[0]
    assert 'aria-pressed="false"' in offered
    assert 'data-confirm-open="census-decide-dialog"' in offered
    assert 'id="census-bulk-dialog"' in html and 'id="census-decide-dialog"' in html
    assert 'name="stage"' in html and "Every stage (1)" in html


def test_read_decision_reads_a_row_add_and_bulk_post():
    """Each control's fields come out as one decision; the page's own
    fields stay for the view."""
    row = {
        "decide": ["accept"],
        "item": ["7"],
        "version": ["2"],
        "request_key": [KEY],
        "note": ["  ok  "],
        "stage": ["to_review"],
    }
    assert read_decision(row) == WorklistDecision(
        Action.ACCEPT, {"7": 2}, "ok", UUID(KEY)
    )
    assert row == {"stage": ["to_review"]}
    edit = {
        "decide": ["edit"],
        "item": ["7"],
        "version": ["2"],
        "request_key": [KEY],
        "note": [""],
        "value": ["150"],
        "value_frequency": ["annual"],
        "size": ["25"],
    }
    decided = read_decision(edit)
    assert decided.fields == {"value": ["150"], "value_frequency": ["annual"]}
    assert edit == {"size": ["25"]}
    add = {"decide": ["add"], "request_key": [KEY], "note": [""], "value": ["x"]}
    assert read_decision(add).versions == {}
    bulk = {
        "bulk": ["ignore"],
        "bulk_key": [KEY],
        "bulk_note": [""],
        "item": ["1:4", "2:5"],
    }
    decided = read_decision(bulk)
    assert decided.bulk and decided.versions == {"1": 4, "2": 5}
    assert read_decision({"stage": ["all"]}) is None


@pytest.mark.parametrize(
    "parameters",
    [
        {"item": ["1"]},
        {
            "decide": ["start_update"],
            "item": ["1"],
            "version": ["1"],
            "request_key": [KEY],
            "note": [""],
        },
        {
            "decide": ["accept"],
            "item": ["1", "2"],
            "version": ["1"],
            "request_key": [KEY],
            "note": [""],
        },
        {
            "decide": ["accept"],
            "item": ["1"],
            "version": ["-1"],
            "request_key": [KEY],
            "note": [""],
        },
        {"decide": ["add"], "item": ["1"], "request_key": [KEY], "note": [""]},
        {
            "decide": ["accept"],
            "item": ["1"],
            "version": ["1"],
            "request_key": [KEY],
            "note": ["x" * 2001],
        },
        {"bulk": ["edit"], "bulk_key": [KEY], "bulk_note": [""], "item": ["1:1"]},
        {
            "bulk": ["accept"],
            "bulk_key": [KEY],
            "bulk_note": [""],
            "item": ["1:1", "1:2"],
        },
        {"bulk": ["accept"], "bulk_key": [KEY], "bulk_note": [""], "item": ["1"]},
        {
            "bulk": ["accept"],
            "bulk_key": [KEY],
            "bulk_note": [""],
            "decide": ["accept"],
            "item": ["1:1"],
        },
    ],
)
def test_read_decision_refuses_a_malformed_post(parameters):
    """Anything a page never sends is a ValueError (a 400)."""
    with pytest.raises(ValueError):
        read_decision(parameters)


def test_record_words_a_race_as_the_notice():
    """A stale item answers with the page and says so; otherwise the
    service's own notice."""

    class Service:
        """A stream's decisions that lost one race."""

        def __init__(self, error=None):
            self.error = error

        def decide(self, principal, decision):
            """Record, or raise the race."""
            if self.error:
                raise self.error
            return "Done."

    decision = WorklistDecision(Action.ACCEPT, {"1": 1}, "", UUID(KEY))
    assert record(Service(), "staff", decision) == "Done."
    assert record(Service(StaleDecision("Phone for Pat")), "staff", decision) == (
        "Phone for Pat: not recorded, changed by someone else."
    )
    assert record(Service(StaleDecision()), "staff", decision) == (
        "A chosen item: not recorded, changed by someone else."
    )


def test_every_icon_tooltip_is_one_phrase_without_a_full_stop():
    """Guard (UX conventions, "Row actions and confirmation"; R38 finding 5):
    every tooltip a worklist icon can show, offered or selected, for every
    stream, stage, decision and role, is one short phrase with no full stop
    or other sentence end, as the portal's other icon tooltips are."""
    readers = (STAFF, LEADER, {"administrator"}, {"staff", "administrator"})
    tips = set()
    for stream in (
        "census",
        "ministry",
        "financial",
        "cannot_participate",
        "additional_information",
    ):
        for stage in Stage:
            for decision in (None, *Decision):
                for roles in readers:
                    for by_hand in (False, True):
                        icons = row_icons(
                            stream,
                            Placement(stage, decision),
                            roles,
                            name="Phone for Pat",
                            leads=True,
                            by_hand=by_hand,
                        )
                        tips |= {icon["title"] for icon in icons}
                        tips |= {icon["label"] for icon in icons}
    assert tips
    for tip in tips:
        assert tip == tip.strip() and not tip.endswith((".", "?", "!", ":")), tip
        assert not any(mark in tip for mark in (". ", "? ", "! ")), tip


def test_page_address_carries_only_the_choices_given():
    """The view's own address keeps the non-empty link-safe choices, in
    order, and is the plain address for the default view (R38 finding 6)."""
    assert worklist.page_address("/w", []) == "/w"
    assert worklist.page_address("/w", [("stage", ""), ("sort", None)]) == "/w"
    assert worklist.page_address("/w", [("stage", "ignored"), ("kind", "a b")]) == (
        "/w?stage=ignored&kind=a+b"
    )


def test_identity_cells_carry_their_item_page():
    """A column with ``link`` gives its cell the row's item page; others
    none (R38 finding 13)."""
    columns = (Column("who", "Who", family=True), Column("what", "What", link="url"))
    row = {
        "id": "1",
        "version": 1,
        "name": "x",
        "placement": Placement(Stage.TO_REVIEW),
        "who": "Pat",
        "what": "Phone",
        "url": "/item/1",
    }
    worklist = build(
        "census",
        [row],
        STAFF,
        prefix="w",
        action="/w",
        columns=columns,
        stage="all",
        counts={},
        stages=[],
    )
    (decorated,) = worklist.rows
    assert [(value, href) for value, _column, href in decorated["identity"]] == [
        ("Pat", ""),
        ("Phone", "/item/1"),
    ]
