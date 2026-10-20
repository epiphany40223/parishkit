"""Census changes on the shared review worklist (#1683), without a database."""

import re
from html import unescape
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import Action, Decision, Stage
from parishkit.stewardship.reports.census_change_views import SORTING
from parishkit.stewardship.reports.census_changes import (
    CensusQuery,
    editor,
    placement,
    select,
    shape,
)
from parishkit.stewardship.reports.census_worklist import (
    CensusDecisions,
    _value,
    worklist_page,
)
from parishkit.stewardship.responses.refresh_checks import CheckState
from parishkit.stewardship.web.review_worklist import (
    INCORRECT_BODIES,
    WORDS,
    WorklistDecision,
)
from parishkit.stewardship.web.tables import paginate

from .test_census_changes import SUBMITTED, row

STAFF = frozenset({"staff"})
ADMINISTRATOR = frozenset({"staff", "administrator"})


def shaped(**values):
    """One shaped census row, with the worklist's query columns."""
    return shape(row(reopened=False) | values)


def page(rows, *, stage="all", roles=STAFF, mutable=True, **extra):
    """The rendered Census changes page for ``rows`` and its worklist."""
    query = CensusQuery(stage=stage)
    result = select(rows, query, administrator="administrator" in roles)
    result["roles"] = roles
    table = paginate(result["rows"], {}, sorting=SORTING)
    worklist, table = worklist_page(
        result, query, table, "/admin/reports/census/", mutable=mutable
    )
    html = render_to_string(
        "stewardship/census-changes.html",
        {
            "worklist": worklist,
            "table": table,
            "query": query,
            "query_fields": query.form_values(),
            "route_choices": [],
            "kind_choices": [],
            "export_timezones": ["UTC"],
            "edit_today": "2054-10-05",
            "mutable": mutable,
            "administrator": "administrator" in roles,
        }
        | extra,
    )
    return worklist, unescape(html)


def icons(worklist, index=0):
    """(action, selected) of one row's icons, in order."""
    return [
        (icon["action"], icon["selected"]) for icon in worklist.rows[index]["icons"]
    ]


@pytest.mark.parametrize(
    ("values", "stage", "decision", "held"),
    [
        # By hand changes are To review now, like every other change.
        ({"handling": "manual", "field": "suffix"}, Stage.TO_REVIEW, None, None),
        ({}, Stage.TO_REVIEW, None, None),
        ({"decision": "approved"}, Stage.READY_TO_UPDATE, Decision.ACCEPTED, None),
        (
            {"decision": "approved", "handling": "manual", "field": "suffix"},
            Stage.TO_ENTER_BY_HAND,
            Decision.ACCEPTED,
            None,
        ),
        (
            {"decision": "approved", "admin_value_set": True, "admin_value": "x"},
            Stage.READY_TO_UPDATE,
            Decision.ACCEPTED_EDITED,
            None,
        ),
        # A Family's same answer reopened an approval over an accepted
        # ParishSoft value: a Conflict keeping the Accepted visible.
        (
            {"execution": "conflict", "reopened": True},
            Stage.CONFLICT,
            None,
            Decision.ACCEPTED,
        ),
        ({"execution": "conflict"}, Stage.CONFLICT, None, None),
        ({"execution": "superseded"}, Stage.HISTORY, None, None),
    ],
)
def test_each_row_takes_its_pipeline_stage(values, stage, decision, held):
    """The stage comes from ``pipeline.census_placement``, never stored."""
    found = placement(row(reopened=False) | values)
    assert (found.stage, found.decision, found.held_decision) == (stage, decision, held)


def test_the_stage_filter_lists_one_stage_and_counts_them_all():
    """To review by default; each stage counted whatever the other filters;
    History is its own choice, left out of Every stage."""
    rows = [
        shaped(id="1"),
        shaped(id="2", decision="approved"),
        shaped(id="3", execution="superseded"),
    ]
    worklist, html = page(rows, stage="to_review")
    assert [row["id"] for row in worklist.rows] == ["1"]
    choices = dict(worklist.stage_choices)
    assert choices["to_review"] == "To review (1)"
    assert choices["ready_to_update"] == "Ready to update (1)"
    assert choices["history"] == "History (1)"
    assert choices["all"] == "Every stage (2)"
    assert list(choices)[-2:] == ["history", "all"]
    assert "Summary" not in html
    worklist, _ = page(rows, stage="all")
    assert [row["id"] for row in worklist.rows] == ["1", "2"]


def test_staff_review_by_hand_and_automatic_rows():
    """Staff get Accept, Edit and Ignore on every To review row, and Entered
    by hand on a By hand one; every actionable row has a box (#1680)."""
    rows = [
        shaped(id="1"),
        shaped(id="2", handling="manual", field="suffix"),
        shaped(id="3", entity_kind="family", field="email_opt_out", handling="manual"),
    ]
    worklist, html = page(rows)
    assert icons(worklist, 0) == [("accept", False), ("edit", False), ("ignore", False)]
    assert icons(worklist, 1) == [
        ("accept", False),
        ("edit", False),
        ("ignore", False),
        ("entered_by_hand", False),
    ]
    # The email opt-out takes no Edit.
    assert icons(worklist, 2) == [
        ("accept", False),
        ("ignore", False),
        ("entered_by_hand", False),
    ]
    assert [row["kinds"] for row in worklist.rows] == [
        "accept ignore",
        "accept ignore entered_by_hand",
        "accept ignore entered_by_hand",
    ]
    assert html.count("data-select-row") == 3
    assert [button["value"] for button in worklist.bulk] == [
        "accept",
        "ignore",
        "back_to_review",
        "entered_by_hand",
    ]
    # Update ParishSoft is the Administrator's alone.
    assert "Update ParishSoft" not in html
    assert "Update ParishSoft" in page(rows, roles=ADMINISTRATOR)[1]


def test_a_conflict_with_an_unknown_parishsoft_value_waits():
    """Accept and Edit on a Conflict decide against ParishSoft's current
    value, so without a known one only Ignore is offered."""
    rows = [
        shaped(id="1", execution="conflict"),
        shaped(
            id="2", execution="conflict", current_available=False, current_value=None
        ),
    ]
    worklist, _ = page(rows, stage="conflict")
    assert icons(worklist, 0) == [("accept", False), ("edit", False), ("ignore", False)]
    assert icons(worklist, 1) == [("ignore", False)]


def test_decisions_in_force_show_on_the_icons_and_the_last_decision():
    """An accepted change's Accept is drawn selected beside Back to review;
    Last decision names who, when and the note; a reopened Conflict shows
    why it is back and its earlier Accepted, not in force."""
    rows = [
        shaped(
            id="1",
            decision="approved",
            resolution_action="approved",
            resolution_by="staff@example.org",
            resolution_at=SUBMITTED,
            resolution_note="Checked",
        ),
        shaped(id="2", execution="conflict", reopened=True),
    ]
    worklist, html = page(rows)
    assert icons(worklist, 0) == [("accept", True), ("back_to_review", False)]
    assert worklist.rows[0]["decided_by"] == "staff@example.org"
    assert "Checked" in html
    reopened = worklist.rows[1]
    assert reopened["decision_label"] == "Earlier: Accepted, not in force."
    assert reopened["decided_by"] == "Family"
    assert reopened["note"] == (
        "Reopened: the Family answered again, so the earlier Accept over"
        " ParishSoft's changed value no longer counts."
    )


def test_no_action_while_other_campaign_work_holds_the_campaign():
    """The list still reads, with no icons, boxes or bulk bar, and says why."""
    worklist, html = page([shaped()], mutable=False)
    assert worklist.rows[0]["icons"] == [] and not worklist.selectable
    assert "Other campaign work" in html


def test_the_page_draws_the_worklist_columns_and_the_download_below():
    """Identity columns in the shared order, then the worklist's columns;
    the filters before the table and the download pane after it."""
    _, html = page(
        [
            shaped(),
            shaped(
                id="2",
                entity_kind="proposed_member",
                entity_key="p-1",
                field="new_member",
                handling="manual",
                submitted_value={"first_name": "Lee"},
            ),
            shaped(id="3", family_name=None),
        ]
    )
    table = html[html.index("<thead>") : html.index("</thead>")]
    headings = re.findall(r"<th[^>]*>(?:<[^>]+>)*([^<]+)", table)
    assert [heading.strip() for heading in headings if heading.strip()] == [
        "Family",
        "Family DUID",
        "Who",
        "Member DUID",
        "What changed",
        "How it reaches ParishSoft",
        "Baseline",
        "Family's answer",
        "Reviewer's value",
        "Stage",
        "Submitted",
        "Last decision",
        "Actions",
    ]
    assert '<td class="numeric">41</td>' in html
    assert '<td class="numeric">New Member</td>' in html
    assert 'data-source-status="missing"' in html
    assert html.index('id="table-filters"') < html.index("<table")
    assert html.index("<table") < html.index('id="table-export"')


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, {"kind": "text", "value": "555-0199", "maxlength": 25}),
        (
            {
                "admin_value_set": True,
                "admin_value": "Corrected",
                "field": "suffix",
                "handling": "manual",
            },
            {"kind": "text", "value": "Corrected", "maxlength": 100},
        ),
        ({"field": "birth_date", "submitted_value": "2000-01-02"}, {"kind": "date"}),
        ({"field": "deceased_status", "submitted_value": True}, None),
        ({"entity_kind": "family", "field": "email_opt_out"}, None),
    ],
)
def test_the_editor_starts_with_the_value_carried_forward(values, expected):
    """Edit's field: the reviewer's value, else the Family's answer, within
    the field's limit (ParishSoft's for an Automatic change)."""
    found = editor(row() | values)
    if expected is None:
        assert found is None
        return
    assert {key: found[key] for key in expected} == expected


def test_an_address_editor_has_its_parts():
    """A Family address is edited part by part, with the country choices."""
    answer = {
        "line1": "9 Census Way",
        "line2": "",
        "city": "Louisville",
        "region": "KY",
        "postal_code": "40223",
        "country": "US",
    }
    found = editor(
        row(entity_kind="family", field="home_address", submitted_value=answer)
    )
    assert found["kind"] == "address" and found["parts"] == answer
    assert ("US", "United States") in found["countries"]
    _, html = page(
        [
            shaped(
                entity_kind="family",
                field="home_address",
                handling="manual",
                submitted_value=answer,
            )
        ]
    )
    for part in answer:
        assert f'name="value_{part}"' in html


def test_the_edit_value_is_read_from_the_editors_fields():
    """One ``value`` for a Member field, each part for an address; anything
    else is a malformed form."""
    assert _value({"value": ["Corrected"]}, "first_name") == "Corrected"
    parts = {f"value_{part}": [part] for part in ("line1", "line2", "city")} | {
        f"value_{part}": [part] for part in ("region", "postal_code", "country")
    }
    assert _value(parts, "home_address")["city"] == "city"
    for fields, field in (
        ({}, "first_name"),
        ({"value": ["a", "b"]}, "first_name"),
        ({"value": ["a"]}, "home_address"),
    ):
        with pytest.raises(ValueError):
            _value(fields, field)


def test_census_takes_no_add_yet():
    """Census Add is its own slice (#1710): the page offers none, and a
    posted one is refused before anything is read."""
    decision = WorklistDecision(
        Action.ADD, {}, "", UUID("00000000-0000-0000-0000-000000000001")
    )
    with pytest.raises(ValueError):
        CensusDecisions(None, None).decide(None, decision)
    worklist, _ = page([shaped()])
    assert not worklist.add


def test_refresh_checks_place_final_changes_and_flag_not_seen():
    """A final change's latest refresh check (#1689): Entered incorrectly
    shows ParishSoft's value and offers its two fixes (#1737); Not seen in
    ParishSoft is a chip and a Stage choice of its own; an Ignored change
    ParishSoft now matches says so under its stage."""
    entered = {
        "handling": "manual",
        "field": "suffix",
        "decision": "approved",
        "execution": "resolved_external",
        "submitted_value": "Jr.",
        "current_value": "Sr.",
        "baseline_value": "Sr.",
    }
    wrong = CheckState("entered_incorrectly", {"available": True, "value": "III"})
    rows = [
        shaped(id="wrong", check=wrong, **entered),
        shaped(id="unseen", check=CheckState("unchanged", None, 2, 2), **entered),
        shaped(id="ignored", decision="ignored", current_value="555-0199"),
    ]
    worklist, html = page(rows, stage="all")
    by_id = {row["id"]: row for row in worklist.rows}
    assert by_id["wrong"]["stage_label"] == "Entered incorrectly"
    # The decision in force is drawn selected, and the two fixes beside it:
    # Back to review, and the mark made again, worded so (#1737).
    icons = {icon["action"]: icon for icon in by_id["wrong"]["icons"]}
    assert list(icons) == ["accept", "back_to_review", "entered_by_hand"]
    assert icons["accept"]["selected"]
    again = icons["entered_by_hand"]
    assert not again["selected"]
    assert again["label"] == f"Mark {by_id['wrong']['name']} entered by hand again"
    assert again["title_one"] == "Mark {name} entered by hand again?"
    assert again["done"] == "{name} marked entered by hand again."
    assert again["body"] == INCORRECT_BODIES[Action.ENTERED_BY_HAND]
    back = icons["back_to_review"]
    assert back["body"] == INCORRECT_BODIES[Action.BACK_TO_REVIEW]
    assert (
        back["choice"] == ""
        and back["title_one"] == WORDS[Action.BACK_TO_REVIEW].title_one
    )
    # Both are bulk kinds of the row's box.
    assert by_id["wrong"]["kinds"] == "back_to_review entered_by_hand"
    assert by_id["wrong"]["shows_now"]
    assert by_id["wrong"]["parishsoft_now"] == "III"
    assert by_id["unseen"]["stage_label"] == "Entered by hand"
    assert by_id["unseen"]["not_seen"] and "Not seen in ParishSoft" in html
    assert (
        "ParishSoft now matches the Family's answer."
        in (by_id["ignored"]["decision_label"])
    )
    choices = dict(worklist.stage_choices)
    assert choices["entered_incorrectly"] == "Entered incorrectly (1)"
    assert choices["not_seen"] == "Not seen in ParishSoft (1)"
    assert choices["all"] == "Every stage (3)"
    worklist, _ = page(rows, stage="not_seen")
    assert [row["id"] for row in worklist.rows] == ["unseen"]


@pytest.mark.parametrize(
    "status,label",
    [
        ("moved", "Not in ParishSoft"),
        ("missing", "Not in ParishSoft"),
        ("inactive", "Inactive in ParishSoft"),
        ("deceased", "Deceased in ParishSoft"),
    ],
)
def test_a_member_who_left_keeps_the_stage_with_the_shared_badge(status, label):
    """Rule 4 (#1738): the change keeps its stage, and the Who cell shows the
    shared ParishSoft status badge beside the name; the row's name for
    screen readers and notices stays the plain name."""
    worklist, html = page([shaped(member_status=status)])
    (found,) = worklist.rows
    assert found["placement"].stage is Stage.TO_REVIEW
    assert found["name"] == "Mobile phone for Pat Sample"
    assert (
        f'Pat Sample <span class="status-badge" data-source-status="{status}">'
        f"{label}</span>"
    ) in html


def test_a_current_member_has_no_badge():
    """A Member the data still lists in the Family (and a household change)
    reads as the name alone."""
    _, html = page(
        [
            shaped(member_status="current"),
            shaped(
                id="2", entity_kind="family", field="email_opt_out", handling="manual"
            ),
        ]
    )
    assert "data-source-status" not in html


def test_a_refresh_conflict_and_a_member_id_say_so_under_the_stage():
    """#1749: an Updated in ParishSoft change a refresh put back in Conflict
    says why (an ordinary Conflict does not), and a New Member's mark names
    the Member ID Staff gave; a New Member's Entered by hand opens its own
    dialog asking for it."""
    undone = shaped(execution="conflict", decision="unreviewed", refresh_conflict=True)
    changed = shaped(
        id="2", execution="conflict", decision="approved", refresh_conflict=False
    )
    added = shaped(
        id="3",
        entity_kind="proposed_member",
        entity_key="local",
        field="new_member",
        handling="manual",
        submitted_value={"first_name": "Newly", "last_name": "Sample"},
        current_available=False,
        current_value=None,
        baseline_available=False,
        baseline_value=None,
        decision="approved",
        execution="resolved_external",
        resolution_action="entered",
        resolution_member=7,
    )
    fresh = shaped(
        id="4",
        entity_kind="proposed_member",
        entity_key="local",
        field="new_member",
        handling="manual",
        submitted_value={"first_name": "Newly", "last_name": "Sample"},
        current_available=False,
        current_value=None,
        baseline_available=False,
        baseline_value=None,
    )
    worklist, html = page([undone, changed, added, fresh])
    rows = {row["id"]: row for row in worklist.rows}
    assert rows["1"]["decision_label"].endswith(
        "ParishSoft no longer holds the update."
    )
    assert "no longer holds" not in rows["2"]["decision_label"]
    assert rows["3"]["decision_label"].endswith("ParishSoft Member ID 7.")
    assert rows["4"]["offers_entered"] and not rows["1"]["offers_entered"]
    assert 'id="census-entered-dialog-4"' in html
    # Row 3's mark may be made again with another ID (#1773, below).
    assert html.count('name="value_member"') == 2


def entered_new_member(**values):
    """A New Member Staff marked Entered by hand, by default naming ID 7."""
    return shaped(
        **{
            "id": "3",
            "entity_kind": "proposed_member",
            "entity_key": "local",
            "field": "new_member",
            "handling": "manual",
            "submitted_value": {"first_name": "Newly", "last_name": "Sample"},
            "current_available": False,
            "current_value": None,
            "baseline_available": False,
            "baseline_value": None,
            "decision": "approved",
            "execution": "resolved_external",
            "resolution_action": "entered",
            "resolution_member": 7,
        }
        | values
    )


def test_an_entered_new_member_offers_correcting_its_member_id():
    """#1773: a New Member Entered by hand and not Confirmed keeps its mark
    drawn selected and gains Correct the ParishSoft Member ID after it,
    which opens the row's own dialog asking for the ID (required, filled in
    with the mark's) and posts Entered by hand. It is one row at a time, so
    it gives the row no bulk kind."""
    worklist, html = page([entered_new_member()])
    (shown,) = worklist.rows
    assert icons(worklist) == [
        ("accept", True),
        ("entered_by_hand", True),
        ("entered_by_hand", False),
    ]
    correct = shown["icons"][2]
    name = shown["name"]
    assert correct["remark"] and correct["icon"] == "edit"
    assert correct["label"] == f"Correct the ParishSoft Member ID of {name}"
    assert correct["dialog"] == "census-entered-dialog-3"
    assert "decide=entered_by_hand" in correct["post"]
    assert correct["done"] == "{name} ParishSoft Member ID corrected."
    assert correct["focus"] == "entered_by_hand" and correct["pressed"] is None
    assert shown["kinds"] == "" and shown["offers_entered"]
    dialog = html.split('id="census-entered-3"')[1].split("</form>")[0]
    field = re.search(r"<input[^>]*name=\"value_member\"[^>]*>", dialog)[0]
    assert "required" in field and 'value="7"' in field
    assert "(optional)" not in dialog
    assert "The next refresh checks the entry again with this ID." in dialog
    # A mark that named no ID may gain one: the field starts blank.
    worklist, html = page([entered_new_member(resolution_member=None)])
    dialog = html.split('id="census-entered-3"')[1].split("</form>")[0]
    field = re.search(r"<input[^>]*name=\"value_member\"[^>]*>", dialog)[0]
    assert 'value=""' in field and "required" in field


@pytest.mark.parametrize(
    "values",
    [
        # Confirmed: the refresh found the Member; nothing to correct.
        {"check": CheckState(outcome="confirmed")},
        # A later response of the Family replaced it: history.
        {"replaced": True},
    ],
)
def test_no_correction_once_confirmed_or_replaced(values):
    """The correction is offered only while the mark is in question."""
    worklist, _ = page([shape(entered_new_member() | values)])
    assert not any(icon.get("remark") for icon in worklist.rows[0]["icons"])
    assert not worklist.rows[0]["offers_entered"]


def test_no_correction_without_the_census_roles_or_on_other_rows():
    """A reader the campaign's work holds is offered nothing; an entered
    Member field (not a New Member) has no correction."""
    worklist, _ = page([entered_new_member()], mutable=False)
    assert worklist.rows[0]["icons"] == []
    suffix = shaped(
        handling="manual",
        field="suffix",
        decision="approved",
        execution="resolved_external",
        resolution_action="entered",
    )
    worklist, _ = page([suffix])
    assert icons(worklist) == [("accept", True), ("entered_by_hand", True)]
