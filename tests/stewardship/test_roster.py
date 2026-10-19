"""Roster changes to enter: eligibility, the worklist page and the download.

The list is the Ministry stream's manual handling report on the shared
review worklist (#528 step 4, #1687): Stage and Ministry filters, Entered by
hand alone as each row's and the bulk bar's action, and the files.
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.pipeline import Action, Decision, Placement, Stage
from parishkit.stewardship.reports import ministry_roster_views as views
from parishkit.stewardship.reports import pdf_design
from parishkit.stewardship.reports.ministry_roster_views import (
    HEADINGS,
    PDF_WEIGHTS,
    SORTING,
    roster_csv,
    roster_pdf,
    roster_xlsx,
)
from parishkit.stewardship.web.review_worklist import StaleDecision, WorklistDecision
from parishkit.stewardship.workflows import roster
from parishkit.stewardship.workflows.roster import eligible

WHEN = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def roster_row(**values):
    """One listed roster change, as ``roster_rows`` and ``with_decisions``
    shape it, with overrides; ``roster_entered`` sets its stage."""
    row = {
        "id": "1",
        "member_name": "Sample, Pat",
        "member_label": "Sample, Pat",
        "member_duid": 3,
        "member_status": "current",
        "source_mark": None,
        "family_name": "Sample, Pat and Lee",
        "family_duid": 1,
        "ministry_name": "Food pantry",
        "family_ministry_name": "Food pantry",
        "ministry_duid": 9,
        "action": "join",
        "state": "resolved",
        "outcome": "joined",
        "source_resolved": False,
        "resolved_at": WHEN,
        "submitted_at": WHEN - timedelta(days=2),
        "decided_by": "admin@example.org",
        "decided_at": WHEN,
        "note": "Called the Family",
        "latest": True,
        "roster_entered": True,
        "roster_sequence": 1,
        "roster_by": "staff@example.org",
        "roster_at": WHEN,
    } | values
    stage = Stage.ENTERED_BY_HAND if row["roster_entered"] else Stage.TO_ENTER_BY_HAND
    decision = (
        Decision.ADDED
        if row.get("parish_added")
        else Decision.ACCEPTED_EDITED
        if row.get("reviewed_ministry_duid")
        else Decision.ACCEPTED
    )
    row.setdefault("placement", Placement(stage, decision))
    row.setdefault("stage", row["placement"].stage.value)
    return row


def test_only_person_resolved_roster_changes_take_the_tick():
    """Resolved joins and leaves with no resolution source; nothing else."""
    base = {"state": "resolved", "outcome": "joined", "source_resolved": False}
    assert eligible(base)
    assert eligible(base | {"outcome": "leave_confirmed"})
    assert not eligible(base | {"source_resolved": True})
    assert not eligible(base | {"outcome": "declined"})
    assert not eligible(base | {"state": "superseded"})


def test_download_times_are_in_the_chosen_zone_and_formulas_are_neutral():
    """Instants carry the zone's offset; a formula-like name stays text."""
    row = roster_row(member_name="=HYPERLINK(1)")
    text = roster_csv([row], ZoneInfo("America/Chicago")).decode()
    table = list(csv.reader(io.StringIO(text)))
    assert tuple(table[0]) == HEADINGS
    # The Ministry is named; its DUID is not a column (#1123).
    assert "Ministry" in HEADINGS and "Ministry DUID" not in HEADINGS
    line = dict(zip(HEADINGS, table[1], strict=True))
    assert not line["Member"].startswith("=")
    assert line["Last decision at"] == "2054-10-05T09:30:00-05:00"
    assert line["Last decision"] == "admin@example.org"
    assert line["Note"] == "Called the Family"
    assert line["Entered at"] == "2054-10-05T09:30:00-05:00"
    assert line["Submitted"] == "2054-10-03T09:30:00-05:00"
    assert line["Family"] == "Sample, Pat and Lee" and line["Family DUID"] == "1"
    assert line["Stage"] == "Entered by hand" and line["Decision"] == "Accepted"
    assert line["Family's answer"] == "Join" and line["Reviewer's value"] == ""


def test_download_words_a_new_member_and_an_unentered_change_as_the_page():
    """A Member added on the form has "New Member" for a DUID; a change not
    yet entered is To enter by hand, with no one and no time; an edited one
    names the Family's Ministry and the one to enter."""
    row = roster_row(
        member_duid=None, roster_entered=False, roster_by="", roster_at=None
    )
    text = roster_csv([row], ZoneInfo("UTC")).decode()
    line = dict(zip(HEADINGS, list(csv.reader(io.StringIO(text)))[1], strict=True))
    assert line["Member DUID"] == "New Member"
    assert line["Stage"] == "To enter by hand"
    assert line["Replaced by a later response"] == ""
    assert line["Entered by"] == "" and line["Entered at"] == ""
    # A Family that answered again after the change was decided.
    replaced = roster_csv([roster_row(latest=False)], ZoneInfo("UTC")).decode()
    line = dict(zip(HEADINGS, list(csv.reader(io.StringIO(replaced)))[1], strict=True))
    assert line["Replaced by a later response"] == "Yes"
    edited = roster_row(
        reviewed_ministry_duid=4,
        reviewed_ministry_name="Adult Choir",
        ministry_name="Adult Choir",
        family_ministry_name="Altar Servers",
    )
    text = roster_csv([edited], ZoneInfo("UTC")).decode()
    line = dict(zip(HEADINGS, list(csv.reader(io.StringIO(text)))[1], strict=True))
    assert line["Ministry"] == "Adult Choir"
    assert line["Family's answer"] == "Join Altar Servers"
    assert line["Reviewer's value"] == "Join Adult Choir"
    assert line["Decision"] == "Accepted (edited)"


def test_csv_xlsx_and_pdf_carry_the_same_columns_and_rows():
    """One column set in every format (#966): the XLSX cells are the CSV's,
    and the PDF table has the same headings and values, only its times worded
    in the parish's compact date format."""
    zone = ZoneInfo("America/Chicago")
    rows = [roster_row(), roster_row(id="2", action="leave", member_duid=None)]
    table = list(csv.reader(io.StringIO(roster_csv(rows, zone).decode())))
    book = load_workbook(io.BytesIO(roster_xlsx(rows, zone)))
    sheet = [
        ["" if value is None else value for value in values]
        for values in book["Roster changes"].iter_rows(values_only=True)
    ]
    assert sheet == table
    drawn = {}
    original = pdf_design.table_pages

    def capture(pdf_table, pdf_rows, frame):
        """Keep the table and rows the PDF lays out, then lay them out."""
        pdf_rows = list(pdf_rows)
        drawn.update(table=pdf_table, rows=pdf_rows, frame=frame)
        return original(pdf_table, pdf_rows, frame)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pdf_design, "table_pages", capture)
        body = roster_pdf(rows, zone, parish="P", campaign="C", as_of=WHEN)
    assert body.startswith(b"%PDF-")
    assert drawn["table"].headings == HEADINGS == tuple(table[0])
    times = {
        HEADINGS.index(name) for name in ("Entered at", "Submitted", "Last decision at")
    }

    def untimed(values):
        """The row's cells other than its times."""
        return [cell for index, cell in enumerate(values) if index not in times]

    assert [untimed(values) for values in drawn["rows"]] == [
        untimed(values) for values in table[1:]
    ]
    assert drawn["rows"][0][HEADINGS.index("Last decision at")] == (
        "Oct 5, 2054 9:30 AM"
    )
    assert drawn["frame"].details == ("2 changes in this file.",)
    assert set(PDF_WEIGHTS) == set(HEADINGS) and sum(PDF_WEIGHTS.values()) == 100
    # A whole DUID stays on one line; a compact time keeps its date whole.
    for heading, text, lines in (
        ("Member DUID", "1234567", ["1234567"]),
        ("Family DUID", "1234567", ["1234567"]),
        ("Entered at", "Sep 30, 2026 12:04 PM", ["Sep 30, 2026", "12:04 PM"]),
    ):
        values = [text if name == heading else "" for name in HEADINGS]
        cell = drawn["table"].cells(values)[HEADINGS.index(heading)]
        assert [line.strip() for line in cell] == lines, heading


def test_every_named_column_sorts_both_ways():
    """Member, Family and Ministry, the Member and Family DUIDs, the request
    and the resolution time sort in place; a New Member follows the numbered
    DUIDs. No Ministry DUID is shown, so none sorts (#1123)."""
    rows = [
        roster_row(id="a", member_duid=7, family_duid=20, ministry_duid=5),
        roster_row(id="b", member_duid=None, family_duid=10, ministry_duid=9),
        roster_row(id="c", member_duid=3, family_duid=30, ministry_duid=1),
    ]
    order = {
        "member_duid": ["c", "a", "b"],
        "duid": ["b", "a", "c"],
    }
    assert "ministry_duid" not in SORTING.columns()
    # Request is no column of the worklist, so it does not sort (#1687).
    assert "request" not in SORTING.columns()
    for key, expected in order.items():
        assert [row["id"] for row in SORTING.sort_rows(rows, key)] == expected, key
        assert [row["id"] for row in SORTING.sort_rows(rows, f"-{key}")] == list(
            reversed(expected)
        ), key
    # Every sortable worklist column has the list's own sort token.
    assert {column.sort for column in views.COLUMNS} <= set(SORTING.columns())


def tick_html(row, **context):
    """The roster tick template rendered for one row and context."""
    from django.template.loader import render_to_string

    return render_to_string(
        "stewardship/roster-tick.html", {"row": row, "roster_key": "k"} | context
    ).strip()


def test_queue_status_cell_says_one_short_line():
    """The follow-up queue's Status cell says only Entered or Not yet
    entered, and never draws the form; who and when are on the request
    page."""

    def cell(**values):
        """The tick as the queue's Status cell draws it for Staff."""
        row = roster_row(roster_eligible=True, source_resolved=False) | values
        return tick_html(row, can_tick=True, compact=True, mutable=True)

    assert cell() == "<br>Entered"
    assert cell(roster_entered=False) == "<br>Not yet entered"
    assert "staff@example.org" not in cell()


def test_a_leader_sees_no_tick_at_all():
    """A Ministry leader (no can_tick) gets nothing: no state, who, when or
    form, on the request page or in the queue (#1160)."""
    for entered in (True, False):
        row = roster_row(
            roster_eligible=True, source_resolved=False, roster_entered=entered
        )
        for compact in (True, False):
            assert tick_html(row, can_tick=False, compact=compact, mutable=True) == ""


def test_download_names_a_member_no_longer_in_parishsoft_in_its_own_column():
    """The Member's ParishSoft status is blank while ParishSoft lists them,
    and otherwise #1216's phrase, beside the Member, not in the name (#1217)."""
    zone = ZoneInfo("UTC")
    rows = [
        roster_row(),
        roster_row(id="2", source_mark=("Deceased in ParishSoft", "reason")),
    ]
    table = list(csv.reader(io.StringIO(roster_csv(rows, zone).decode())))
    lines = [dict(zip(HEADINGS, line, strict=True)) for line in table[1:]]
    assert [line["ParishSoft status"] for line in lines] == [
        "",
        "Deceased in ParishSoft",
    ]
    assert lines[1]["Member"] == "Sample, Pat"


def test_the_view_grammar_is_closed():
    """The page takes Stage (To enter by hand by default), Ministry and the
    table's paging; the Show pair is gone (#1687) and so is any other field;
    a download takes Stage and Ministry with its format and zone."""
    from django.http import QueryDict

    def form(**values):
        """A QueryDict of the given single values."""
        query = QueryDict(mutable=True)
        for name, value in values.items():
            query[name] = value
        return query

    stage, extra = views._choices(form(), export=False)
    assert stage == "to_enter_by_hand" and extra["ministry"] == ""
    stage, extra = views._choices(
        form(stage="entered_by_hand", ministry="9", sort="member", size="25"),
        export=False,
    )
    assert (stage, extra["ministry"]) == ("entered_by_hand", "9")
    assert views._choices(form(stage="all"), export=False)[0] == "all"
    for bad in (
        {"show": "all"},
        {"stage": "to_review"},
        {"stage": "ignored"},
        {"ministry": "x"},
        {"changed": "1"},
        {"sort": "nonsense"},
    ):
        with pytest.raises(ValueError):
            views._choices(form(**bad), export=False)
    stage, extra = views._choices(
        form(stage="all", ministry="9", format="pdf", browser_timezone="Asia/Tokyo"),
        export=True,
    )
    assert (stage, extra["format"], extra["zone"].key) == ("all", "pdf", "Asia/Tokyo")
    with pytest.raises(ValueError):
        views._choices(form(stage="all", page="2", format="csv"), export=True)


def page_worklist(rows, roles=frozenset({"staff"}), stage="all"):
    """The worklist the page draws for ``rows`` (``page_context``)."""
    return views.page_context(
        rows,
        {"metadata": {}, "ministries": []},
        stage,
        {"ministry": "", "paging": {"size": "25"}},
        roles,
        mutable=bool(roles),
        notice=None,
        prepare=lambda shown: None,
    )["worklist"]


def test_rows_offer_entered_by_hand_alone():
    """A row not yet marked offers only Entered by hand, never the review
    decisions (they stay on Ministry requests); a marked row shows it
    selected, with no undo here; the bulk bar has Entered by hand alone and
    its dialogs take no note. Its version is the tick's next sequence."""
    rows = [
        roster_row(id=str(UUID(int=1)), roster_entered=False, roster_sequence=2),
        roster_row(id=str(UUID(int=2))),
    ]
    worklist = page_worklist(rows)
    todo, done = worklist.rows
    assert [icon["action"] for icon in todo["icons"]] == ["entered_by_hand"]
    assert not todo["icons"][0]["selected"] and todo["kinds"] == "entered_by_hand"
    assert todo["version"] == 3
    assert [icon["action"] for icon in done["icons"]] == ["entered_by_hand"]
    assert done["icons"][0]["selected"] and done["icons"][0]["title"] == (
        "Entered by hand"
    )
    assert done["kinds"] == ""
    assert [button["value"] for button in worklist.bulk] == ["entered_by_hand"]
    assert worklist.add is False and worklist.notes is False
    assert worklist.note_template.endswith("review-worklist-no-note.html")
    # While other campaign work holds the list, no row offers anything.
    held = page_worklist(rows, roles=frozenset())
    assert all(not row["kinds"] for row in held.rows) and not held.selectable


def test_the_stage_filter_counts_every_stage_and_keeps_one():
    """Stage counts every change under the applied Ministry, To enter by hand
    first; the table holds only the chosen Stage's rows."""
    rows = [
        roster_row(id=str(UUID(int=1)), roster_entered=False),
        roster_row(id=str(UUID(int=2)), roster_entered=False),
        roster_row(id=str(UUID(int=3))),
    ]
    worklist = page_worklist(rows, stage="to_enter_by_hand")
    assert worklist.stage_choices == [
        ("to_enter_by_hand", "To enter by hand (2)"),
        ("entered_by_hand", "Entered by hand (1)"),
        ("all", "Every stage (3)"),
    ]
    assert [row["id"] for row in worklist.rows] == [str(UUID(int=1)), str(UUID(int=2))]
    assert worklist.fields == {"stage": "to_enter_by_hand", "ministry": "", "page": "1"}
    assert [row["id"] for row in page_worklist(rows, stage="entered_by_hand").rows] == [
        str(UUID(int=3))
    ]


def test_the_stage_line_says_replaced_and_who_marked():
    """Under the Stage: the decision the stage word does not say, Replaced
    by a later response, and who marked an entered change and when."""
    worklist = page_worklist(
        [
            roster_row(id=str(UUID(int=1)), latest=False, roster_entered=False),
            roster_row(
                id=str(UUID(int=2)),
                reviewed_ministry_duid=4,
                reviewed_ministry_name="Adult Choir",
            ),
            roster_row(id=str(UUID(int=3)), roster_by="", roster_at=None),
        ]
    )
    replaced, edited, unknown = (str(row["decision_label"]) for row in worklist.rows)
    assert replaced == "Accepted<br>Replaced by a later response"
    assert edited.startswith("Accepted (edited)<br>Entered by staff@example.org, ")
    assert 'datetime="2054-10-05T14:30:00+00:00"' in edited
    assert unknown == "Accepted<br>Entered by Former portal user"


def test_a_change_listed_twice_names_each_row():
    """The same change listed twice (a Family answered again and both
    requests were accepted) would give both rows one name; the earlier one,
    replaced by the later response, says so, so each icon, box, dialog and
    notice names its own row. Other rows stay short."""
    rows = [
        roster_row(id=str(UUID(int=1))),
        roster_row(id=str(UUID(int=2)), latest=False),
        roster_row(id=str(UUID(int=3)), member_name="Other, Lee"),
    ]
    assert [row["name"] for row in views.decorate(rows)] == [
        "Sample, Pat — Food pantry",
        "Sample, Pat — Food pantry, replaced by a later response",
        "Other, Lee — Food pantry",
    ]


def test_a_family_with_no_name_is_not_linked():
    """The Family cell links its Timeline only when ParishSoft names it; a
    blank name stays blank, as before (#1570)."""
    named, blank = views.decorate(
        [
            roster_row(id=str(UUID(int=1)), family_id=uuid4()),
            roster_row(id=str(UUID(int=2)), family_id=uuid4(), family_name=""),
        ]
    )
    assert named["family_id"] is not None and blank["family_id"] is None


class FakePrincipal:
    """A principal with a fixed identity."""

    identity = UUID(int=99)


def test_roster_decisions_record_ticks_and_word_races(monkeypatch):
    """Entered by hand on one row sets its tick with the row's key and next
    sequence; the bulk bar marks every ticked row, all or none, with the
    page's key. A tick someone else changed first, or a request sent back
    to review since, is a StaleDecision naming it; any other action, or a
    note, is a malformed form."""
    calls = []
    one, two = UUID(int=1), UUID(int=2)
    monkeypatch.setattr(
        roster,
        "set_roster_entered",
        lambda store, actor, request_id, **kw: calls.append(("one", request_id, kw)),
    )
    monkeypatch.setattr(
        roster,
        "mark_many_entered",
        lambda store, actor, sequences, **kw: calls.append(("many", sequences, kw)),
    )
    names = {one: "Sample, Pat — Food pantry", two: "Other, Lee — Choir"}
    service = roster.RosterDecisions(object(), namer=names.get)
    key = uuid4()
    single = WorklistDecision(Action.ENTERED_BY_HAND, {str(one): 3}, "", key)
    assert service.decide(FakePrincipal(), single) == (
        "Sample, Pat — Food pantry marked entered by hand."
    )
    assert calls[-1] == (
        "one",
        one,
        {"sequence": 3, "entered": True, "request_key": key},
    )
    bulk = WorklistDecision(
        Action.ENTERED_BY_HAND, {str(one): 1, str(two): 2}, "", key, bulk=True
    )
    assert service.decide(FakePrincipal(), bulk) == "2 changes marked entered by hand."
    assert calls[-1] == ("many", {one: 1, two: 2}, {"bulk_key": key})

    def stale(error):
        """Make both services raise ``error``."""

        def fail(*args, **kwargs):
            """Raise the error."""
            raise error

        monkeypatch.setattr(roster, "set_roster_entered", fail)
        monkeypatch.setattr(roster, "mark_many_entered", fail)

    stale(roster.RosterStale(two))
    with pytest.raises(StaleDecision) as raised:
        service.decide(FakePrincipal(), bulk)
    assert raised.value.name == "Other, Lee — Choir"
    stale(roster.RosterIneligible("This request does not take the roster tick."))
    with pytest.raises(StaleDecision) as raised:
        service.decide(FakePrincipal(), single)
    assert raised.value.name == "Sample, Pat — Food pantry"
    with pytest.raises(StaleDecision) as raised:
        service.decide(FakePrincipal(), bulk)
    assert raised.value.name is None
    for bad in (
        WorklistDecision(Action.ACCEPT, {str(one): 3}, "", key),
        WorklistDecision(Action.ENTERED_BY_HAND, {str(one): 3}, "a note", key),
        WorklistDecision(
            Action.ENTERED_BY_HAND, {str(one): 3}, "", key, fields={"value": ["9"]}
        ),
    ):
        with pytest.raises(ValueError):
            service.decide(FakePrincipal(), bad)


def test_a_notice_names_the_row_as_the_list_does(monkeypatch):
    """The notice's namer reads the request through the scoped selection and
    names it as its row: the Ministry to enter (an Edit's), and a change the
    Family answered again after it was decided says so; a failed read names
    no row."""
    from contextlib import nullcontext

    from django.db import DatabaseError

    found = {}

    def page(campaign_id, query, principal, *, request_id, every_member):
        """The selection's one row for ``request_id``."""
        assert every_member and query.history == "all"
        return {"rows": [found[request_id]]}

    monkeypatch.setattr(views, "followup_page", page)
    monkeypatch.setattr(views.transaction, "atomic", nullcontext)
    found["edited"] = roster_row(
        reviewed_ministry_name="Adult Choir", ministry_name="Altar Servers"
    )
    found["replaced"] = roster_row(id=str(UUID(int=7)), latest=False)
    assert views.roster_name(None, None, "edited") == "Sample, Pat — Adult Choir"
    assert views.roster_name(None, None, "replaced") == (
        "Sample, Pat — Food pantry, replaced by a later response"
    )
    # The row the page draws for the same change has the same name.
    assert views.decorate([found["replaced"]])[0]["name"] == views.roster_name(
        None, None, "replaced"
    )

    def broken(*args, **kwargs):
        """A read that fails."""
        raise DatabaseError("down")

    monkeypatch.setattr(views, "followup_page", broken)
    assert views.roster_name(None, None, "edited") is None
