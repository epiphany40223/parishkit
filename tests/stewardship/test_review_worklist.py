"""The shared review worklist's view helper and template (#1681)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from parishkit.stewardship.pipeline import Action, Decision, Placement, Stage
from parishkit.stewardship.web import review_worklist as worklist
from parishkit.stewardship.web.review_worklist import (
    REOPEN_BODY,
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
    """(action, selected, pressed, active) of each icon a reader sees, in order."""
    return [
        (icon["action"], icon["selected"], icon["pressed"], icon["active"])
        for icon in row_icons(stream, placement, roles, name="Item", **flags)
    ]


DECIDE = [("accept", False, False, True), ("edit", False, None, True)]
DECIDE.append(("ignore", False, False, True))
# Edit and Ignore once a decision is in force: unavailable until it is undone.
WAITING = [("edit", False, None, False), ("ignore", False, False, False)]


@pytest.mark.parametrize(
    "placement,flags,expected",
    [
        # To review offers the decisions, none selected; Edit is no toggle.
        (Placement(Stage.TO_REVIEW), {}, DECIDE),
        # A By hand To review change draws Entered by hand unavailable: it
        # follows an Accept (#1890, decision 2 reversed), and the row keeps
        # its icons at every stage (#2131).
        (
            Placement(Stage.TO_REVIEW),
            {"by_hand": True},
            [*DECIDE, ("entered_by_hand", False, False, False)],
        ),
        # The decision in force is drawn pressed and is the row's undo: no
        # Back to review icon beside it (#2131).
        (
            Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED),
            {},
            [("accept", True, True, True), *WAITING],
        ),
        # Accepted (edited) selects Accept: Edit is Accept with a value.
        (
            Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED_EDITED),
            {"by_hand": True},
            [
                ("accept", True, True, True),
                *WAITING,
                ("entered_by_hand", False, False, True),
            ],
        ),
        (
            Placement(Stage.IGNORED, Decision.IGNORED),
            {},
            [
                ("accept", False, False, False),
                ("edit", False, None, False),
                ("ignore", True, True, True),
            ],
        ),
        # A mark in force is pressed too and is the undo (#1840): the
        # decision before it waits for that undo.
        (
            Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED),
            {},
            [
                ("accept", True, True, False),
                *WAITING,
                ("entered_by_hand", True, True, True),
            ],
        ),
        # Entered incorrectly's old mark is not in force: mark it again, or
        # choose the pressed Accept to undo the step.
        (
            Placement(Stage.ENTERED_INCORRECTLY, Decision.ACCEPTED),
            {},
            [
                ("accept", True, True, True),
                *WAITING,
                ("entered_by_hand", False, False, True),
            ],
        ),
        # A Conflict's held decision is not in force.
        (Placement(Stage.CONFLICT, held_decision=Decision.ACCEPTED), {}, DECIDE),
    ],
)
def test_staff_census_icons_show_the_decision_in_force(placement, flags, expected):
    """A row draws the same icons at every stage; the decision in force is
    pressed, the latest one is active as the undo, and the rest wait."""
    assert icons("census", placement, **flags) == expected


def test_no_row_draws_back_to_review():
    """Back to review never has an icon of its own (#2131): a Ministry
    request a refresh confirmed, with no decision behind it, draws Accept
    pressed as its undo, the row's icons unchanged."""
    for stage in Stage:
        for decision in (None, *Decision):
            placement = Placement(stage, decision)
            drawn = [a for a, *_ in icons("ministry", placement, leads=True)]
            assert "back_to_review" not in drawn, placement
    confirmed = Placement(Stage.CONFIRMED)
    assert icons("ministry", confirmed) == [
        ("accept", True, True, True),
        *WAITING,
        ("entered_by_hand", False, False, False),
    ]


def test_a_leader_sees_only_what_their_role_allows():
    """On a Ministry they lead a leader reviews; never Entered by hand,
    selected or not; on another Ministry, nothing at all. A terminal step
    is Staff's to undo, so its pressed Accept says so."""
    review = Placement(Stage.TO_REVIEW)
    assert [a for a, *_ in icons("ministry", review, LEADER, leads=True)] == [
        "accept",
        "edit",
        "ignore",
    ]
    assert icons("ministry", review, LEADER, leads=False) == []
    entered = Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)
    drawn = row_icons("ministry", entered, LEADER, name="x", leads=True)
    assert [(i["action"], i["selected"], i["active"]) for i in drawn] == [
        ("accept", True, False),
        ("edit", False, False),
        ("ignore", False, False),
    ]
    assert drawn[0]["title"] == (
        "Accepted; only Staff and Administrators can change it now"
    )
    assert icons("census", review, LEADER) == []


def test_selected_icons_are_named_and_filled():
    """A pressed icon says its state and what choosing it does, in its name
    and tooltip alike, and takes its status look; an unavailable one says
    why; an offered one is plain (#2131)."""
    edited = Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED_EDITED)
    accept, edit, ignore = row_icons("census", edited, STAFF, name="Phone for Pat")
    assert (
        accept["label"]
        == accept["title"]
        == ("Accepted (edited); select to send Phone for Pat back to review")
    )
    assert accept["css"] == "icon-button is-selected status-working"
    assert accept["undo"] and accept["active"]
    assert ignore["css"] == "icon-button" and ignore["title"] == (
        "Ignore Phone for Pat; send it back to review first"
    )
    assert not ignore["active"]
    # A mark is undone by choosing it again (#1840), so the tooltip says so,
    # and the decision before it waits.
    marked = Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)
    titles = [
        icon["title"]
        for icon in row_icons("census", marked, STAFF, name="x", by_hand=True)
    ]
    assert titles == [
        "Accepted; undo Entered by hand first",
        "Edit x; undo Entered by hand first",
        "Ignore x; undo Entered by hand first",
        "Entered by hand; select to undo Entered by hand for x",
    ]
    review = Placement(Stage.TO_REVIEW)
    marks = row_icons("census", review, STAFF, name="x", by_hand=True)
    assert marks[-1]["title"] == "Mark x entered by hand; accept it first"
    assert accept["focus"] == "accept"
    edit = row_icons("census", review, STAFF, name="x")[1]
    assert edit["action"] == "edit" and edit["focus"] == "accept"
    ignored = Placement(Stage.IGNORED, Decision.IGNORED)
    assert row_icons("census", ignored, STAFF, name="x")[2]["css"].endswith(
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
        # A census addition's own (#2041); the census page keeps it only
        # while it lists one.
        "withdraw",
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
    """A row's box lists the bulk actions its icons offer; each icon posts
    its decision, version and request key. A census mark's Back to review
    (#1840) needs a note, so its box says so."""
    built = made()
    first, ready, entered = built.rows
    assert first["kinds"] == "accept ignore"
    assert ready["kinds"] == "back_to_review" and ready["choice"] == ""
    assert entered["kinds"] == "back_to_review" and entered["choice"] == "reopen"
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


def test_a_stream_without_baseline_or_edit_drops_those_columns():
    """No Baseline or Reviewer's value column for a stream without them (#1685)."""
    from django.template.loader import render_to_string

    table = paginate(rows(), {"size": "25"}, carry=[("stage", "all")])
    built = made(shown=table.rows, baseline=False, reviewer=False)
    assert not built.baseline and not built.reviewer
    # Who, Family's answer, Stage, Submitted, Last decision, Actions and the
    # selection column.
    assert built.span == 7
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": built, "table": table, "csrf_token": "x"},
    )
    head = html.split("<thead>")[1].split("</thead>")[0]
    assert "Reviewer's value" not in head and "Baseline" not in head
    assert "Family's answer" in head


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
    only on rows with a bulk action; the latest selected icon pressed and
    opening Back to review's dialog (#2131), an earlier one pressed and
    unavailable with no dialog to open."""
    from django.template.loader import render_to_string

    table = paginate(rows(), {"size": "25"}, carry=[("stage", "all")])
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": made(shown=table.rows), "table": table, "csrf_token": "x"},
    )
    assert "<details" not in html.split('id="table-filters"')[0][-200:]
    assert html.count("data-select-row") == 3
    undo = "Accepted; select to send Phone for Member 1 back to review"
    pressed = html.split(f'aria-label="{undo}"')[1].split(">")[0]
    assert 'aria-pressed="true"' in pressed and "aria-disabled" not in pressed
    assert "data-row-undo" in pressed and "decide=back_to_review" in pressed
    assert 'data-confirm-open="census-decide-dialog"' in pressed
    waiting = html.split('aria-label="Accepted; undo Entered by hand first"')[1]
    waiting = waiting.split(">")[0]
    assert 'aria-pressed="true"' in waiting and 'aria-disabled="true"' in waiting
    assert "data-confirm-open" not in waiting
    offered = html.split('aria-label="Accept Phone for Member 0"')[1].split(">")[0]
    assert 'aria-pressed="false"' in offered
    assert 'data-confirm-open="census-decide-dialog"' in offered
    assert 'id="census-bulk-dialog"' in html and 'id="census-decide-dialog"' in html
    assert 'name="stage"' in html and "Every stage (1)" in html


def test_a_silent_action_hides_its_note():
    """``silent`` actions record no note (a Ministry roster tick, R39 finding
    2): their row icons and bulk button carry the ``no_note`` choice, which
    hides the dialog's note (ui-v1.js data-note-field); the others keep
    theirs. Reopening an Ignored item asks for none (#1889): its icon, its
    box and its dialog's words leave the note optional."""
    shown = [
        dict(
            rows()[0],
            id="0",
            placement=Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED),
        ),
        dict(rows()[1], id="1", placement=Placement(Stage.IGNORED, Decision.IGNORED)),
    ]
    built = made(shown=shown, silent={Action.ENTERED_BY_HAND})
    entered, ignored = built.rows
    choices = {
        icon["decide"]: icon["choice"] for icon in entered["icons"] if icon["active"]
    }
    assert choices == {"back_to_review": "", "entered_by_hand": "no_note"}
    back = [i for i in ignored["icons"] if i["active"]]
    assert [(i["choice"], i["body"]) for i in back] == [("", REOPEN_BODY)]
    assert ignored["choice"] == ""
    bulk = {button["value"]: button["choice"] for button in built.bulk}
    assert bulk["entered_by_hand"] == "no_note"
    assert bulk["back_to_review"] == "reopen" and bulk["accept"] == ""
    # Without ``silent`` every action keeps its note.
    plain = made(shown=shown)
    assert {i["choice"] for i in plain.rows[0]["icons"] if i["active"]} == {""}


def test_a_stream_family_column_links_anywhere():
    """A Family column links its Timeline wherever it sits, not only first."""
    from django.template.loader import render_to_string

    shown = [
        dict(
            rows()[0],
            family="Sample, Pat and Lee",
            family_name="Sample, Pat and Lee",
            family_id=UUID(int=5),
        )
    ]
    columns = [Column("who", "Who"), Column("family", "Family")]
    table = paginate(shown, {"size": "25"}, carry=[("stage", "all")])
    built = build(
        "census",
        table.rows,
        STAFF,
        prefix="census",
        action="/review",
        columns=columns,
        stage="all",
        counts={},
        stages=[],
    )
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.policy import Principal

    staff = SimpleNamespace(principal=Principal(UUID(int=1), frozenset({"staff"})))
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": built, "table": table, "csrf_token": "x", "request": staff},
    )
    assert ">Sample, Pat and Lee</a></td>" in html


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


def test_a_withheld_action_is_left_out_of_one_row():
    """An item that cannot take an action has no icon for it (#1683).

    A census field no value can be typed for (moved, deceased, the email
    opt-out, a New Member) has no Edit, whatever its stage; Entered by hand
    waits for an Accept (#2131).
    """
    assert icons(
        "census", Placement(Stage.TO_REVIEW), by_hand=True, withheld={"edit"}
    ) == [
        ("accept", False, False, True),
        ("ignore", False, False, True),
        ("entered_by_hand", False, False, False),
    ]


@pytest.mark.parametrize(
    "placement,expected",
    [
        (Placement(Stage.TO_REVIEW), ""),
        # The stage's word already says the decision.
        (Placement(Stage.IGNORED, Decision.IGNORED), ""),
        (Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED), "Accepted."),
        # A Conflict keeps its earlier decision visible, not in force.
        (
            Placement(Stage.CONFLICT, held_decision=Decision.ACCEPTED_EDITED),
            "Earlier: Accepted (edited), not in force.",
        ),
        (
            Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED, failed=True),
            "Accepted. The last update failed.",
        ),
    ],
)
def test_the_decision_line_words_held_and_failed_items(placement, expected):
    """The line under a row's stage (#1683)."""
    assert worklist.decision_label(placement) == expected


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


def test_a_row_with_its_own_entered_fields_gets_its_own_dialog():
    """A census New Member's Entered by hand asks for the ParishSoft Member
    ID (#1749): its icon opens a dialog drawn for that row alone, holding the
    stream's fields; the bulk bar still offers Entered by hand without them.
    The stream's stage note joins the line under the stage."""
    from django.template.loader import render_to_string

    # Accepted, so it offers Entered by hand (#1890).
    shown = [
        rows()[0] | {"placement": Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)}
    ]
    shown[0] |= {
        "entered_template": "stewardship/census-worklist-member.html",
        "by_hand": True,
        "stage_note": "ParishSoft Member ID 7.",
    }
    built = made(shown=shown)
    (row,) = built.rows
    entered = next(icon for icon in row["icons"] if icon["action"] == "entered_by_hand")
    assert entered["dialog"] == "census-entered-dialog-0"
    assert row["offers_entered"] and "entered_by_hand" in row["kinds"]
    assert row["decision_label"] == "Accepted. ParishSoft Member ID 7."
    table = paginate(built.rows, {"size": "25"}, carry=[("stage", "all")])
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": built, "table": table, "csrf_token": "x"},
    )
    dialog = html.split('id="census-entered-0"')[1].split("</form>")[0]
    assert 'id="census-entered-dialog-0"' in dialog
    assert 'name="value_member"' in dialog and 'inputmode="numeric"' in dialog
    assert "ParishSoft Member ID (optional)" in dialog
    # Without the stream's fields, Entered by hand opens the shared dialog.
    plain = made(shown=[rows()[0] | {"by_hand": True}]).rows[0]
    assert not plain["offers_entered"]
    dialogs = {icon["dialog"] for icon in plain["icons"] if icon["active"]}
    assert dialogs == {"census-decide-dialog", "census-edit-dialog-0"}


def test_a_terminal_undo_says_where_the_item_goes():
    """Back to review's dialog on a terminal stage names the step and stage.

    Updated in ParishSoft is undone in the portal only, which it says
    (#1840).
    """

    entered = Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)
    assert worklist.undo_body("census", entered, by_hand=True) == (
        "Entered by hand is undone: the item goes back to To enter by hand, "
        "with its decision kept. Say why."
    )
    assert worklist.undo_body("ministry", Placement(Stage.CONFIRMED)) == (
        "Confirmed is undone: the item goes back to To review. Say why."
    )
    updated = worklist.undo_body("census", Placement(Stage.UPDATED, Decision.ACCEPTED))
    assert updated.startswith(
        "Updated in ParishSoft is undone: the item goes back to Ready to update"
    )
    assert "Nothing is written to ParishSoft" in updated
    # A census change the update run carries, Confirmed after its update or
    # accepted and Already in ParishSoft (shown Confirmed), goes back to
    # Ready to update too: its undo also writes nothing to ParishSoft.
    confirmed = Placement(Stage.CONFIRMED, Decision.ACCEPTED)
    body = worklist.undo_body("census", confirmed)
    assert "goes back to Ready to update, with its decision kept" in body
    assert body.endswith(worklist.UNDO_PORTAL_ONLY)
    # A by-hand change's Confirmed undo goes to To enter by hand: no update
    # to speak of.
    assert "Nothing is written" not in worklist.undo_body(
        "census", confirmed, by_hand=True
    )
    assert (
        "Undoing an update writes nothing to ParishSoft."
        in (worklist.BODIES[Action.BACK_TO_REVIEW])
    )
