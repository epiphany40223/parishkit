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
from parishkit.stewardship.web.review_worklist import WorklistDecision
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
    # Publish to ParishSoft is the Administrator's alone.
    assert "Publish to ParishSoft" not in html
    assert "Publish to ParishSoft" in page(rows, roles=ADMINISTRATOR)[1]


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
    shows ParishSoft's value and offers no action yet (#1737); Not seen in
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
    # Only the decision in force is drawn (selected); nothing is offered.
    assert all(icon["selected"] for icon in by_id["wrong"]["icons"])
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


def test_link_pairs_carry_only_the_link_safe_choices():
    """The view's own address keeps the Stage and closed filters other than
    their defaults, the zone only with a day, and the table's choices; the
    name search never (R38 finding 6)."""
    from parishkit.stewardship.reports.census_changes import CensusQuery
    from parishkit.stewardship.reports.census_worklist import link_pairs
    from parishkit.stewardship.web.review_worklist import page_address

    plain = CensusQuery(stage="to_review", search="Smith", zone="UTC")
    assert page_address("/c", link_pairs(plain)) == "/c"
    chosen = CensusQuery(
        stage="all", route="automatic", start="2054-10-01", zone="UTC", search="Smith"
    )
    assert page_address("/c", link_pairs(chosen, {"sort": "-who"})) == (
        "/c?stage=all&route=automatic&start=2054-10-01&zone=UTC&sort=-who"
    )
