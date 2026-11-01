"""Census changes on the shared review worklist (#1683), without a database."""

import re
from datetime import UTC
from html import unescape

import pytest
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from parishkit.stewardship.pipeline import (
    Action,
    Decision,
    Placement,
    Stage,
    census_addition_placement,
)
from parishkit.stewardship.reports import census_worklist
from parishkit.stewardship.reports.census_change_views import SORTING
from parishkit.stewardship.reports.census_changes import (
    CensusQuery,
    editor,
    export_rows,
    link_additions,
    placement,
    select,
    shape,
    shape_addition,
)
from parishkit.stewardship.reports.census_worklist import (
    _value,
    add_fields,
    worklist_page,
)
from parishkit.stewardship.responses.census import address_editor
from parishkit.stewardship.responses.census_additions import AddRefusal, parse_add
from parishkit.stewardship.responses.comparison import ADDRESS_COMPONENTS
from parishkit.stewardship.responses.refresh_checks import CheckState
from parishkit.stewardship.web.review_worklist import (
    BODIES,
    INCORRECT_BODIES,
    WORDS,
)
from parishkit.stewardship.web.tables import paginate

from .test_census_changes import SUBMITTED, row


@pytest.fixture(autouse=True)
def no_user_rows(monkeypatch):
    """No database here: no address is a current user, so every reviewer
    keeps the address the rows carry (#2259; the PostgreSQL tests read the
    Names)."""
    monkeypatch.setattr(census_worklist, "address_actors", lambda addresses: {})


STAFF = frozenset({"staff"})
ADMINISTRATOR = frozenset({"staff", "administrator"})


def shaped(**values):
    """One shaped census row, with the worklist's query columns."""
    return shape(row(reopened=False) | values)


def page(rows, *, stage="all", roles=STAFF, mutable=True, replacements=None, **extra):
    """The rendered Census changes page for ``rows`` and its worklist."""
    query = CensusQuery(stage=stage)
    result = select(rows, query, administrator="administrator" in roles)
    result["roles"] = roles
    result["replacements"] = replacements or {}
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
    """(action, selected) of one row's icons, in order.

    ``index`` is the row's position, or its id (a string), for a test whose
    rows the default name order (#2124) lists in another order.
    """
    if isinstance(index, str):
        (shown,) = [row for row in worklist.rows if row["id"] == index]
    else:
        shown = worklist.rows[index]
    return [(icon["action"], icon["selected"]) for icon in shown["icons"]]


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


def test_the_page_opens_on_the_stages_its_badge_counts():
    """Census changes opens on To review and Conflict together (#2331): its
    first Stage choice lists both, with the count the menu's badge and
    Home's card show (``census_changes.OPEN_STAGES`` over the same stage
    counts, as ``admin_context`` adds them up), and an address without a
    Stage carries none."""
    from parishkit.stewardship.accounts.admin_context import _open, count_words
    from parishkit.stewardship.reports.census_changes import OPEN_STAGES

    rows = [
        shaped(id="1"),
        shaped(id="2", execution="conflict"),
        shaped(id="3", decision="approved"),
        shaped(id="4", execution="superseded"),
    ]
    worklist, html = page(rows, stage="to_review_conflict")
    assert sorted(row["id"] for row in worklist.rows) == ["1", "2"]
    first, *_rest = worklist.stage_choices
    assert first == ("to_review_conflict", "To review and Conflict (2)")
    counts = select(rows, CensusQuery(stage="all"), administrator=False)
    badge = _open(counts["stage_counts"], OPEN_STAGES)
    assert badge == 2 and count_words("census_changes") == "To review and Conflict"
    assert 'value="to_review_conflict" selected' in html


def test_staff_review_by_hand_and_automatic_rows():
    """Staff get Accept, Edit and Ignore on every To review row, By hand or
    not, and Entered by hand only once a By hand one is accepted (strict
    phase order, #1890: no one-step mark; drawn unavailable before, #2131);
    every actionable row has a box (#1680)."""
    rows = [
        shaped(id="1"),
        shaped(id="2", handling="manual", field="suffix"),
        shaped(id="3", entity_kind="family", field="email_opt_out", handling="manual"),
        shaped(id="4", handling="manual", field="suffix", decision="approved"),
    ]
    worklist, html = page(rows, stage="all")
    assert icons(worklist, "1") == [
        ("accept", False),
        ("edit", False),
        ("ignore", False),
    ]
    assert icons(worklist, "2") == [
        ("accept", False),
        ("edit", False),
        ("ignore", False),
        ("entered_by_hand", False),
    ]
    # The email opt-out takes no Edit.
    assert icons(worklist, "3") == [
        ("accept", False),
        ("ignore", False),
        ("entered_by_hand", False),
    ]
    # The pressed Accept is the undo: no Back to review icon (#2131).
    assert icons(worklist, "4") == [
        ("accept", True),
        ("edit", False),
        ("ignore", False),
        ("entered_by_hand", False),
    ]
    (fourth,) = [row for row in worklist.rows if row["id"] == "4"]
    assert [i["decide"] for i in fourth["icons"] if i["active"]] == [
        "back_to_review",
        "entered_by_hand",
    ]
    assert {row["id"]: row["kinds"] for row in worklist.rows} == {
        "1": "accept ignore",
        "2": "accept ignore",
        "3": "accept ignore",
        "4": "back_to_review entered_by_hand",
    }
    assert html.count("data-select-row") == 4
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
    """An accepted change's Accept is drawn selected, and is its undo;
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
    assert icons(worklist, 0) == [("accept", True), ("edit", False), ("ignore", False)]
    assert worklist.rows[0]["icons"][0]["undo"]
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
    # Each heading's lines (a stacked heading's name and DUID, #2124),
    # without its sort control's arrow and hidden instructions.
    headings = []
    for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S):
        lines = re.sub(r'<span class="stack-line[^"]*"[^>]*>', "|", cell)
        lines = re.sub(
            r"\s*\(sort (?:a|de)scending\)|[▲▼↕]", "", unescape(strip_tags(lines))
        )
        headings.append(" / ".join(line.strip() for line in lines.split("|") if line))
    assert [heading for heading in headings if heading] == [
        "Family / Family DUID",
        "Who / Member DUID",
        "What changed",
        "Baseline → Family's answer",
        "Stage",
        "Submitted / Last decision",
        "Actions",
    ]
    # Each name's DUID on the line under it.
    detail = (
        '<span class="stack-line cell-detail nowrap"><span class="visually-hidden">'
    )
    assert f"{detail}Member DUID </span>41</span>" in html
    assert f"{detail}Member DUID </span>New Member</span>" in html
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
    # Country first, then the street lines, City, both State controls (the
    # US drop-down with KY chosen; the free text empty) and the postal code.
    (dialog,) = set(re.findall(r'id="([^"]*-edit-dialog-[^"]*)-country"', html))
    order = [
        html.index(f'id="{dialog}-{part}"')
        for part in (
            "country",
            "line1",
            "line2",
            "city",
            "region",
            "region-text",
            "postal_code",
        )
    ]
    assert order == sorted(order)
    assert '<option value="KY" selected>Kentucky (KY)</option>' in html
    assert f'id="{dialog}-region-text" name="value_region" value=""' in html
    assert html.count('name="value_region"') == 4  # Edit's two, Add's two
    assert '<span data-show-when="value_country=US">ZIP code</span>' in html


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, {"country": "US", "us_region": "", "text_region": ""}),
        (
            {"line1": "1 Main", "city": "Louisville", "region": "ky", "country": "us"},
            {"country": "US", "us_region": "KY", "unlisted_region": ""},
        ),
        (
            {"line1": "1 Main", "city": "Louisville", "region": " Kentucky "},
            {"country": "", "us_region": "", "text_region": " Kentucky "},
        ),
        (
            {"line1": "1 Main", "city": "Toronto", "region": "ON", "country": "CA"},
            {
                "country": "CA",
                "us_region": "",
                "unlisted_region": "",
                "text_region": "ON",
            },
        ),
        (
            {
                "line1": "1 Main",
                "city": "Louisville",
                "region": " Kentucky ",
                "country": "US",
            },
            {"us_region": "", "unlisted_region": "Kentucky", "text_region": ""},
        ),
    ],
)
def test_the_address_editor_shows_its_country_and_one_state_control(value, expected):
    """The editor's Country is the address's own, or United States for a
    blank one; its State is the US drop-down's code (an unlisted US region
    kept as its own choice) or, for another country, the free text (#1785)."""
    found = address_editor(value)
    assert {key: found[key] for key in expected} == expected
    assert found["regions"][0] == ("AL", "Alabama (AL)")


def test_an_unlisted_us_region_is_drawn_selected():
    """A US region the drop-down does not offer is its own selected choice,
    so Save sends it and the server refuses it at State (#1785)."""
    html = render_to_string(
        "stewardship/components/census-address-editor.html",
        {
            "dialog_id": "edit",
            "address": address_editor(
                {
                    "line1": "1 Main",
                    "city": "Louisville",
                    "region": "Kentuky",
                    "country": "US",
                }
            ),
        },
    )
    assert '<option value="Kentuky" selected>Kentuky</option>' in html
    assert html.count(" selected>") == 2  # the country and the region


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


def test_staff_get_add_while_the_campaign_admits_changes():
    """Staff get Add in the selection bar, with its dialog: What changed
    first, a value control per field and the ignored changes to add in
    place of; none while other campaign work holds the campaign (#1710)."""
    extra = {"add_fields": add_fields(), "add_replaceable": [("7", "Prefix for X")]}
    worklist, html = page([shaped()], **extra)
    assert worklist.add and 'id="census-add-dialog"' in html
    assert html.index('name="value_field"') < html.index('name="value_family"')
    assert 'name="value_birth_date" type="date"' in html
    assert 'name="value_line1"' in html and '<option value="7">Prefix for X' in html
    assert 'data-show-when="value_field=home_address|mailing_address"' in html
    worklist, html = page([shaped()], mutable=False, **extra)
    assert not worklist.add and "census-add-dialog" not in html


def add_post(**changes):
    """The Add dialog's fields for a Member's nickname, with ``changes``."""
    fields = {
        "value_field": ["nickname"],
        "value_family": ["1001"],
        "value_member": ["101"],
        "value_nickname": ["Bo"],
        "value_in_place_of": [""],
    }
    return {key: value for key, value in (fields | changes).items() if value}


def test_parse_add_reads_only_the_chosen_fields_controls():
    """A Member field sends its Member and value; a Family address its parts;
    a stray or missing control is a malformed form, a bad DUID or field a
    refusal at its field."""
    found = parse_add(add_post())
    assert (found["family_duid"], found["member_duid"], found["value"]) == (
        1001,
        101,
        "Bo",
    )
    parts = {f"value_{part}": [""] for part in ADDRESS_COMPONENTS}
    address = parse_add(
        {"value_field": ["home_address"], "value_family": ["1001"]}
        | {"value_in_place_of": [""]}
        | parts
    )
    assert address["entity_kind"] == "family" and address["member_duid"] is None
    for broken in (
        add_post(value_line1=[""]),
        add_post(value_member=None),
        add_post(value_nickname=["Bo", "Bob"]),
    ):
        with pytest.raises(ValueError):
            parse_add(broken)
    for changes, code in (
        ({"value_field": ["moved_household"]}, "add_field"),
        ({"value_family": ["0"]}, "add_family"),
        ({"value_member": ["New"]}, "add_member"),
    ):
        with pytest.raises(AddRefusal) as refused:
            parse_add(add_post(**changes))
        assert refused.value.code == code


def test_an_addition_is_added_to_enter_by_hand_until_its_step():
    """Open, an addition is To enter by hand, Added by the parish; entered,
    Entered by hand; withdrawn, History (#1710)."""
    assert census_addition_placement("open") == Placement(
        Stage.TO_ENTER_BY_HAND, Decision.ADDED
    )
    assert census_addition_placement("entered").stage is Stage.ENTERED_BY_HAND
    assert census_addition_placement("withdrawn") == Placement(Stage.HISTORY)
    with pytest.raises(ValueError):
        census_addition_placement("ignored")


def added(**values):
    """One shaped parish-added row, as ``ADDITION_ROWS`` returns it."""
    return shape_addition(
        {
            "id": "a1",
            "entity_kind": "member",
            "entity_key": "41",
            "field": "mobile_phone",
            "value": '{"display": "555-0123", "number": "5550123"}',
            "in_place_of": None,
            "created_at": SUBMITTED,
            "baseline": '{"available": true, "value": "555-0100"}',
            "family_duid": 7001,
            "family_name": "Sample",
            "member_name": "Pat Sample",
            "snapshot_id": None,
            "added_by": "staff@example.org",
            "added_note": "",
            "step": None,
            "step_note": None,
            "step_at": None,
            "step_by": None,
            "step_sequence": None,
        }
        | values
    )


def test_an_entered_addition_is_undone_back_to_enter_by_hand():
    """Not entered yet (Back to review) on an entered addition undoes the
    mark (#1840, #2041): its icon is named so, asks for a note and says
    where it goes; Withdraw beside it needs the note too. Once reopened, it
    is To enter by hand, Added by the parish, at the next version, its last
    decision the undo with its note, and offers Withdraw, not Back to
    review."""
    entered = added(step="entered", step_sequence=1, step_at=SUBMITTED)
    assert (entered["stage"], entered["version"]) == ("entered_by_hand", 2)
    reopened = added(
        id="a2",
        step="reopened",
        step_sequence=2,
        step_note="Typo",
        step_at=SUBMITTED,
        step_by="staff@example.org",
    )
    assert (reopened["stage"], reopened["version"]) == ("to_enter_by_hand", 3)
    assert reopened["placement"] == Placement(Stage.TO_ENTER_BY_HAND, Decision.ADDED)
    assert (reopened["resolution_action"], reopened["resolution_note"]) == (
        "reopened",
        "Typo",
    )
    worklist, _ = page([entered, reopened], replacements={})
    rows = {row["id"]: row for row in worklist.rows}
    (undo,) = [icon for icon in rows["add-a1"]["icons"] if icon["undo"]]
    assert undo["body"] == (
        "Entered by hand is undone: the item goes back to To enter by hand, "
        "with its decision kept. Say why."
    )
    name = "Mobile phone for Pat Sample"
    # The pressed Entered by hand is Not entered yet (#2131).
    assert undo["action"] == "entered_by_hand" and undo["decide"] == "back_to_review"
    assert (undo["label"], undo["title"], undo["title_one"], undo["done"]) == (
        f"Entered by hand; select to mark {name} not entered yet",
        f"Entered by hand; select to mark {name} not entered yet",
        "Mark {name} not entered yet?",
        "{name} marked not entered yet.",
    )
    assert rows["add-a1"]["choice"] == "reopen"
    icons = {icon["action"]: icon for icon in rows["add-a1"]["icons"]}
    assert icons["withdraw"]["choice"] == "reopen"
    assert icons["withdraw"]["body"] == (
        "Entered by hand is undone and the added change is withdrawn: it moves "
        "to History. The Family will no longer see it. Say why."
    )
    assert icons["accept"]["title"] == "Added by the parish"
    # The same icons in both states, so none moves (#2131).
    assert list(icons) == ["accept", "withdraw", "entered_by_hand"]
    assert rows["add-a1"]["kinds"] == "back_to_review withdraw"
    icons = {icon["action"]: icon for icon in rows["add-a2"]["icons"]}
    assert set(icons) == {"accept", "withdraw", "entered_by_hand"}
    withdraw = icons["withdraw"]
    assert (withdraw["label"], withdraw["title_one"], withdraw["body"]) == (
        f"Withdraw the added {name}",
        "Withdraw the added {name}?",
        "The Family will no longer see it.",
    )
    assert withdraw["choice"] == "" and withdraw["icon"] == "remove"
    assert icons["accept"]["title"] == "Added by the parish"
    assert rows["add-a2"]["kinds"] == "withdraw entered_by_hand"
    bulk = {item["value"]: item for item in worklist.bulk}
    assert bulk["back_to_review"]["body"].endswith(
        "A ticked change the parish added that is Entered by hand goes back to To "
        "enter by hand (Not entered yet)."
    )
    assert (bulk["withdraw"]["title_many"], bulk["withdraw"]["choice"]) == (
        "Withdraw {count} changes the parish added?",
        "reopen",
    )


def test_additions_are_tied_to_the_familys_changes_of_their_field():
    """Every addition in place of an ignored change is listed under it; an
    open addition beside the Family's open change of the field says so; an
    ignored change an open addition covers offers no Back to review; the
    Family's open change of an open addition's field says Accept withdraws
    it, in its own dialogs and the bulk Accept's (#2041)."""
    ignored = shaped(id="9", decision="ignored")
    asked = shaped(id="8", entity_key="42")
    first = added(in_place_of="9")
    second = added(id="a2", in_place_of="9", entity_key="42")
    gone = added(id="a3", in_place_of="9", step="withdrawn", step_at=SUBMITTED)
    replacements = link_additions([ignored, asked], [first, second, gone])
    assert len(replacements["9"]) == 2
    assert ignored["covered"] and not asked.get("covered")
    assert second["overlap"] and not first["overlap"] and not gone["overlap"]
    worklist, _ = page([ignored, asked, first, second, gone], replacements=replacements)
    rows = {row["id"]: row for row in worklist.rows}
    assert "back_to_review" not in [icon.get("decide") for icon in rows["9"]["icons"]]
    assert rows["9"]["decision_label"].count("Mobile phone: 555-0123") == 2
    assert "open change of this field too" in rows["add-a2"]["decision_label"]
    assert asked["withdraws"] == "Mobile phone (555-0123)"
    assert not ignored["withdraws"]
    extra = "This also withdraws the parish's added Mobile phone (555-0123)."
    icons = {icon["action"]: icon for icon in rows["8"]["icons"]}
    # The withdrawal is its own paragraph (``warning``), not run on from the
    # body (Administrator, R45 W).
    assert icons["accept"]["body"] == (
        "Record this decision. Choose its icon again to undo it."
    )
    assert icons["accept"]["warning"] == icons["edit"]["warning"] == extra
    assert not icons["edit"].get("body")
    bulk = {item["value"]: item for item in worklist.bulk}
    # No entered addition here: Back to review keeps its own words.
    assert bulk["back_to_review"]["body"] == BODIES[Action.BACK_TO_REVIEW]
    assert bulk["accept"]["warning"] == (
        "A ticked change whose field has a change the parish added, still To "
        "enter by hand, withdraws that addition too."
    )
    assert "withdraws" not in bulk["accept"]["body"]
    assert "withdraw" in bulk


def test_the_bulk_bar_offers_withdraw_only_beside_an_addition():
    """#2041: Withdraw is in the bulk bar only while the page lists a change
    the parish added that is not withdrawn."""
    worklist, _ = page([shaped()])
    assert "withdraw" not in [item["value"] for item in worklist.bulk]
    gone = added(step="withdrawn", step_at=SUBMITTED)
    worklist, _ = page([shaped(), gone])
    assert "withdraw" not in [item["value"] for item in worklist.bulk]
    worklist, html = page([shaped(), added()])
    assert "withdraw" in [item["value"] for item in worklist.bulk]
    assert 'data-confirm-body="The Family will no longer see it."' in html


def test_every_terminal_row_offers_back_to_review_with_a_note():
    """#1840: Entered by hand, Updated in ParishSoft and Confirmed each offer
    Back to review, whose dialog names the stage the change returns to and
    requires a note; undoing Updated in ParishSoft says nothing is written
    to ParishSoft. A terminal change a later response replaced, a change an
    open addition covers and an entered addition offer none."""
    entered = shaped(
        id="entered",
        handling="manual",
        field="suffix",
        decision="approved",
        execution="resolved_external",
    )
    one_step = shaped(
        id="one_step",
        handling="manual",
        field="suffix",
        execution="resolved_external",
    )
    updated = shaped(id="updated", decision="approved", execution="published")
    held = shaped(id="held", execution="resolved_upstream")
    gone = shaped(
        id="gone",
        handling="manual",
        field="suffix",
        decision="approved",
        execution="resolved_external",
        entity_key="42",
    ) | {"replaced": True}
    covered = shaped(
        id="covered", decision="approved", execution="published", entity_key="43"
    )
    addition = added(id="a1", entity_key="43")
    marked = added(id="a2", entity_key="44", step="entered", step_at=SUBMITTED)
    link_additions([covered], [addition, marked])
    assert covered["covered"]
    worklist, _ = page([entered, one_step, updated, held, gone, covered, marked])
    rows = {row["id"]: row for row in worklist.rows}

    def back(name):
        """The row's undo (its pressed icon, #2131, or on a row with nothing
        pressed its own Back to review icon), or None."""
        found = [
            icon
            for icon in rows[name]["icons"]
            if icon.get("decide") == "back_to_review"
        ]
        return found[0] if found else None

    assert back("entered")["body"] == (
        "Entered by hand is undone: the item goes back to To enter by hand, "
        "with its decision kept. Say why."
    )
    # Accepted and marked in one step: shown Accepted, so it keeps it.
    assert back("one_step")["body"] == back("entered")["body"]
    assert back("updated")["body"] == (
        "Updated in ParishSoft is undone: the item goes back to Ready to "
        "update, with its decision kept. Say why. Nothing is written to "
        "ParishSoft: only this portal's record changes, and a later update "
        "reads ParishSoft again first."
    )
    # Already in ParishSoft with no reviewer: nothing was decided.
    assert back("held")["body"] == (
        "Confirmed is undone: the item goes back to To review. Say why."
    )
    for name in ("entered", "one_step", "updated", "held", "add-a2"):
        assert back(name)["choice"] == "reopen", name
        kinds = "back_to_review withdraw" if name == "add-a2" else "back_to_review"
        assert rows[name]["kinds"] == kinds, name
        assert rows[name]["choice"] == "reopen", name
    for name in ("gone", "covered"):
        assert back(name) is None, name


def test_downloads_list_an_addition_as_the_page_does():
    """The files carry an addition's row: no Family's answer, the added value
    as the reviewer's, By hand, To enter by hand and who added it."""
    result = select(
        [added(added_note="Phoned in")], CensusQuery(stage="all"), administrator=False
    )
    (line,) = export_rows(result, UTC)
    assert line[8:12] == ("", "555-0123", "By hand", "To enter by hand")
    assert line[13] == "staff@example.org" and line[15] == "Phoned in"
    assert result["rows"][0]["baseline"] == "555-0100"


def test_refresh_checks_place_final_changes_and_flag_not_seen():
    """A final change's latest refresh check (#1689): Entered incorrectly
    shows ParishSoft's value and offers its two fixes (#1737, #1840); Not seen in
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
    # The decision in force is drawn selected, and the two fixes: Back to
    # review (the pressed Accept, #2131), and the mark made again, worded
    # so (#1737).
    icons = {icon["action"]: icon for icon in by_id["wrong"]["icons"]}
    assert list(icons) == ["accept", "ignore", "entered_by_hand"]
    assert icons["accept"]["selected"] and icons["accept"]["undo"]
    again = icons["entered_by_hand"]
    assert not again["selected"]
    assert again["label"] == f"Mark {by_id['wrong']['name']} entered by hand again"
    assert again["title_one"] == "Mark {name} entered by hand again?"
    assert again["done"] == "{name} marked entered by hand again."
    assert again["body"] == INCORRECT_BODIES[Action.ENTERED_BY_HAND]
    # Back to review undoes the mark (#1840): back to To enter by hand with
    # its decision kept, with a required note.
    back = icons["accept"]
    assert back["body"] == (
        "Entered incorrectly is undone: the item goes back to To enter by "
        "hand, with its decision kept. Say why."
    )
    assert (
        back["choice"] == "reopen"
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
    # The name wraps to two lines, then clamps, whole in its tip (#2124).
    assert (
        '<span class="cell-with-badge"><span class="cell-clamp name-clamp"'
        ' data-clip-tip>Pat Sample</span> <span class="status-badge"'
        f' data-source-status="{status}">{label}</span>'
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


def test_link_pairs_carry_only_the_link_safe_choices():
    """The view's own address keeps the Stage and closed filters other than
    their defaults and the table's choices; the name search never (R38
    finding 6), nor the retired submitted dates (#1888)."""
    from parishkit.stewardship.reports.census_changes import CensusQuery
    from parishkit.stewardship.reports.census_worklist import link_pairs
    from parishkit.stewardship.web.review_worklist import page_address

    # The default Stage, To review and Conflict (#2331), is left out.
    plain = CensusQuery(stage="to_review_conflict", search="Smith", zone="UTC")
    assert page_address("/c", link_pairs(plain)) == "/c"
    to_review = CensusQuery(stage="to_review", zone="UTC")
    assert page_address("/c", link_pairs(to_review)) == "/c?stage=to_review"
    chosen = CensusQuery(
        stage="all", route="automatic", start="2054-10-01", zone="UTC", search="Smith"
    )
    assert page_address("/c", link_pairs(chosen, {"sort": "-who"})) == (
        "/c?stage=all&route=automatic&sort=-who"
    )


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
    # Accepted: Entered by hand follows an Accept (#1890).
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
        decision="approved",
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
    # Census Add's dialog (#1710) has a value_member field of its own.
    assert html.count("ParishSoft Member ID (optional)") == 1
    # Row 3's mark may be made again with another ID (#1773, below), and
    # Census Add's dialog has a value_member field of its own.
    assert html.count('name="value_member"') == 3


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
    it gives the row no bulk kind; Back to review, which undoes the mark
    (#1840), is the row's one bulk kind."""
    worklist, html = page([entered_new_member()])
    (shown,) = worklist.rows
    assert icons(worklist) == [
        ("accept", True),
        ("ignore", False),
        ("entered_by_hand", True),
        ("entered_by_hand", False),
    ]
    assert shown["icons"][2]["undo"]
    correct = shown["icons"][3]
    name = shown["name"]
    assert correct["remark"] and correct["icon"] == "edit"
    assert correct["label"] == f"Correct the ParishSoft Member ID of {name}"
    assert correct["dialog"] == "census-entered-dialog-3"
    assert "decide=entered_by_hand" in correct["post"]
    assert correct["title_one"] == "Correct the ParishSoft Member ID of {name}?"
    assert correct["done"] == "{name} had its ParishSoft Member ID corrected."
    assert correct["focus"] == "entered_by_hand" and correct["pressed"] is None
    assert shown["kinds"] == "back_to_review" and shown["offers_entered"]
    dialog = html.split('id="census-entered-3"')[1].split("</form>")[0]
    field = re.search(r"<input[^>]*name=\"value_member\"[^>]*>", dialog)[0]
    assert "required" in field and 'value="7"' in field
    assert "ParishSoft Member ID (optional)" not in dialog
    assert "The next refresh checks the entry again with this ID." in dialog
    # A mark that named no ID may gain one: Add, the field starts blank.
    worklist, html = page([entered_new_member(resolution_member=None)])
    add = worklist.rows[0]["icons"][3]
    assert add["label"] == f"Add the ParishSoft Member ID of {name}"
    assert add["done"] == "{name} had its ParishSoft Member ID added."
    dialog = html.split('id="census-entered-3"')[1].split("</form>")[0]
    field = re.search(r"<input[^>]*name=\"value_member\"[^>]*>", dialog)[0]
    assert 'value=""' in field and "required" in field


def test_a_not_seen_new_member_offers_the_correction():
    """A New Member flagged Not seen in ParishSoft (a mistyped ID, most
    likely) is still Entered by hand and offers the correction, out of
    bulk."""
    unseen = CheckState(outcome="unchanged", unseen=2, not_seen_after=2)
    worklist, _ = page([shape(entered_new_member() | {"check": unseen})])
    (shown,) = worklist.rows
    assert shown["not_seen"]
    assert [icon.get("remark", False) for icon in shown["icons"]] == [
        False,
        False,
        False,
        True,
    ]
    assert shown["kinds"] == "back_to_review"


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
    assert icons(worklist) == [
        ("accept", True),
        ("ignore", False),
        ("entered_by_hand", True),
    ]
