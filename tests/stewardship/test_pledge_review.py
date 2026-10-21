"""Pledges without a database (#1686).

The financial stream's stage mapping, the Edit value's checks, how a row's
cells and files word a pledge, and which actions each stage offers on the
shared worklist. The decisions, the guard and the page against PostgreSQL
are in ``database/test_pledge_review_postgresql.py``.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.http import QueryDict

from parishkit.stewardship.pipeline import (
    Decision,
    Placement,
    Stage,
    Stream,
    financial_placement,
)
from parishkit.stewardship.reports import pledge_review_views as views
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.reports.pledge_models import PledgeReview
from parishkit.stewardship.reports.pledge_review import (
    EditRefusal,
    Item,
    edited_value,
    placement,
)
from parishkit.stewardship.web.review_worklist import build

SUBMITTED = datetime(2054, 10, 6, 15, 30, tzinfo=UTC)
STAFF = frozenset({"staff"})


def review(decision="accepted", *, entered=False, amount=None, frequency=None):
    """An unsaved latest decision row, as the selection would return it."""
    row = PledgeReview(
        actor_id=uuid4(),
        decision=decision,
        reviewed_annual_pledge=amount,
        reviewed_frequency=frequency,
        entered=entered,
        note="Checked with the Family",
    )
    row.created_at = SUBMITTED
    return row


def item(
    stage="to_review",
    latest=None,
    *,
    in_force=True,
    sequence=None,
    check=None,
    observed=None,
    base=None,
):
    """One pledge item; ``sequence`` defaults to 1 with a row, 0 without.

    ``stage`` is the shared selection's final stage and ``base`` the one
    its decision rows give (``stage`` unless given); ``check`` and
    ``observed`` are its latest counting refresh check's (#1704, #1747).
    """
    return Item(
        family_id=uuid4(),
        family_duid=101,
        submission_id=uuid4(),
        sequence=(1 if latest else 0) if sequence is None else sequence,
        stage=stage,
        review=latest,
        in_force=in_force,
        base_stage=base or stage,
        check=check,
        observed=None if observed is None else Decimal(observed),
    )


def pledge_row(found=None, **extra):
    """One shaped Financial stewardship row with its item, as the page reads it."""
    found = found or item(
        "to_enter_by_hand",
        review("accepted_edited", amount=Decimal("1300.00"), frequency="monthly"),
    )
    row = {
        "id": str(found.submission_id),
        "family_name": "Smith, Ann and Bob",
        "family_duid": 101,
        "active": True,
        "cannot_give": False,
        "annual": MoneyAmount(120000),
        "frequency": "weekly",
        "shares": [
            {"label": "Online giving", "text": ""},
            {"label": "Other", "text": "Stock gift"},
        ],
        "submitted_at": SUBMITTED,
        "item": found,
        "placement": placement(found),
        "decided_by": "staff@example.org" if found.review else "",
        "decided_at": SUBMITTED if found.review else None,
        "note": found.review.note if found.review else "",
    } | extra
    row["stage"] = row["placement"].stage.value
    return row


@pytest.mark.parametrize(
    "decision,entered,expected",
    [
        (None, False, Placement(Stage.TO_REVIEW)),
        ("ignored", False, Placement(Stage.IGNORED, Decision.IGNORED)),
        ("accepted", False, Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)),
        (
            "accepted_edited",
            False,
            Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED_EDITED),
        ),
        ("accepted", True, Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)),
        (
            "accepted_edited",
            True,
            Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED_EDITED),
        ),
    ],
)
def test_financial_placement_maps_every_stored_state(decision, entered, expected):
    """The spec's Financial stream mapping, one stored state per row."""
    assert financial_placement(decision=decision, entered=entered) == expected


def test_financial_placement_without_arguments_is_to_review():
    """Callers that predate stored decisions (My review work) still read To review."""
    assert financial_placement() == Placement(Stage.TO_REVIEW)


@pytest.mark.parametrize(
    "decision,entered", [("approved", False), (None, True), ("ignored", True)]
)
def test_financial_placement_refuses_states_the_guard_never_stores(decision, entered):
    """An unknown decision, or a mark without an acceptance, is refused."""
    with pytest.raises(ValueError):
        financial_placement(decision=decision, entered=entered)


def test_a_changed_pledge_is_to_review_whatever_its_last_decision():
    """A decision no longer in force places nothing (rule 2)."""
    found = item("to_review", review("accepted", entered=True), in_force=False)
    assert placement(found) == Placement(Stage.TO_REVIEW)


def test_placement_must_agree_with_the_shared_selection():
    """SQL and Python derive the stage alike; a disagreement is a defect."""
    with pytest.raises(ValueError):
        placement(item("ignored", review("accepted")))


def fields(amount, frequency):
    """An Edit's posted editor fields."""
    return {"value_amount": [amount], "value_frequency": [frequency]}


@pytest.mark.parametrize(
    "amount,frequency,expected",
    [
        ("1200", "monthly", (Decimal("1200.00"), "monthly")),
        ("1,250.5", "annual", (Decimal("1250.50"), "annual")),
        ("0", "", (Decimal("0.00"), None)),
        ("0.00", "weekly", (Decimal("0.00"), "weekly")),
        ("999,999,999.99", "quarterly", (Decimal("999999999.99"), "quarterly")),
    ],
)
def test_an_edit_takes_the_family_forms_amounts(amount, frequency, expected):
    """The Family form's format and limit; a zero pledge may have no frequency."""
    assert edited_value(fields(amount, frequency)) == expected


@pytest.mark.parametrize(
    "amount,frequency,field",
    [
        ("12.345", "monthly", "value_amount"),
        ("-5", "monthly", "value_amount"),
        ("1000000000", "monthly", "value_amount"),
        ("", "monthly", "value_amount"),
        ("100", "", "value_frequency"),
    ],
)
def test_an_edit_value_is_refused_at_its_field(amount, frequency, field):
    """A correctable value names the field it is shown at."""
    with pytest.raises(EditRefusal) as refused:
        edited_value(fields(amount, frequency))
    assert refused.value.fields == (field,)


@pytest.mark.parametrize(
    "posted",
    [
        {"value_amount": ["1"]},
        {"value_amount": ["1", "2"], "value_frequency": [""]},
        {"value_amount": ["1"], "value_frequency": ["fortnightly"]},
    ],
)
def test_a_malformed_edit_is_a_plain_value_error(posted):
    """A missing, repeated or unknown field is a malformed form, not a field error."""
    with pytest.raises(ValueError) as refused:
        edited_value(posted)
    assert not isinstance(refused.value, EditRefusal)


def test_cells_show_the_familys_answer_and_the_reviewers_value():
    """Family's answer keeps the Family's pledge; the edit is beside it."""
    row = pledge_row()
    assert str(views.family_answer(row)) == (
        '$1,200.00, Weekly<br><span class="help">Online giving, Other: Stock gift'
        "</span>"
    )
    assert views.reviewer_value(row) == "$1,300.00, Monthly"
    assert views.to_enter(row) == ("$1,300.00", "Monthly")


def test_the_pledge_to_enter_is_the_familys_without_an_edit():
    """An accepted pledge carries the Family's answer forward."""
    row = pledge_row(item("to_enter_by_hand", review("accepted")))
    assert views.reviewer_value(row) == ""
    assert views.to_enter(row) == ("$1,200.00", "Weekly")


def test_a_family_that_cannot_contribute_enters_a_zero_pledge():
    """Cannot contribute is its own answer, entered as no pledge."""
    row = pledge_row(
        item("to_review"), cannot_give=True, annual=MoneyAmount(0), frequency=None
    )
    assert views.family_answer(row) == "Cannot contribute"
    assert views.to_enter(row) == ("$0.00", "No frequency")


def test_a_changed_pledge_shows_no_edit_or_last_decision():
    """An Edit not in force is not the Reviewer's value, and the row says why."""
    found = item(
        "to_review",
        review("accepted_edited", amount=Decimal("5.00"), frequency="annual"),
        in_force=False,
    )
    row = pledge_row(found)
    assert views.reviewer_value(row) == ""
    (decorated,) = views.decorate([row])
    assert decorated["decided_by"] == "" and decorated["note"] == ""
    assert views.CHANGED in str(views._stage_line(row, ""))


def test_rows_post_their_response_and_version():
    """The row's id is the Family's current response; its version the item's."""
    row = pledge_row()
    (decorated,) = views.decorate([row])
    assert decorated["id"] == str(row["item"].submission_id)
    assert decorated["version"] == 1
    assert (decorated["edit_amount"], decorated["edit_frequency"]) == (
        "1300.00",
        "monthly",
    )
    fresh = views.decorate([pledge_row(item())])[0]
    assert (fresh["version"], fresh["edit_amount"], fresh["edit_frequency"]) == (
        0,
        "1200.00",
        "weekly",
    )


@pytest.mark.parametrize(
    "stage,latest,offered",
    [
        ("to_review", None, ["accept", "edit", "ignore"]),
        ("ignored", review("ignored"), ["back_to_review"]),
        ("to_enter_by_hand", review("accepted"), ["back_to_review", "entered_by_hand"]),
        # Back to review undoes a terminal step (#1840); Entered by hand
        # again on Entered incorrectly waits for #1737.
        ("entered_by_hand", review("accepted", entered=True), ["back_to_review"]),
    ],
)
def test_each_stage_offers_its_role_tables_actions(stage, latest, offered):
    """Staff see the stream's actions at each stage; a selected one is drawn so."""
    row = views.decorate([pledge_row(item(stage, latest))])
    worklist = build(
        Stream.FINANCIAL,
        row,
        STAFF,
        prefix="pledges",
        action="/admin/reports/financial/pledges/",
        columns=views.COLUMNS,
        stage=stage,
        counts={stage: 1},
        stages=views.STAGES,
        baseline=False,
    )
    icons = worklist.rows[0]["icons"]
    assert [icon["action"] for icon in icons if not icon["selected"]] == offered
    assert not worklist.add
    assert [choice["value"] for choice in worklist.bulk] == [
        "accept",
        "ignore",
        "back_to_review",
        "entered_by_hand",
    ]


def test_a_ministry_leader_is_offered_nothing():
    """No Ministry leader decides on a pledge (and the page denies them first)."""
    row = views.decorate([pledge_row(item())])
    worklist = build(
        Stream.FINANCIAL,
        row,
        frozenset({"ministry_leader"}),
        prefix="pledges",
        action="/x/",
        columns=views.COLUMNS,
        stage="to_review",
        counts={},
        stages=views.STAGES,
    )
    assert worklist.rows[0]["icons"] == [] and worklist.bulk == []


def test_the_search_never_travels_in_an_address():
    """A GET takes the Stage and table choices only; a name search is POST only."""
    assert (
        views._choices(
            QueryDict("stage=to_enter_by_hand"), export=False, private=False
        )[0]
        == "to_enter_by_hand"
    )
    with pytest.raises(ValueError):
        views._choices(QueryDict("search=Smith"), export=False, private=False)
    stage, extra = views._choices(
        QueryDict("search=Smith&stage=all"), export=False, private=True
    )
    assert (stage, extra["search"]) == ("all", "Smith")
    with pytest.raises(ValueError):
        views._choices(QueryDict("stage=updating"), export=False, private=True)


def test_the_files_name_every_column_once():
    """CSV rows hold the headings' values in order, cents kept."""
    zone = ZoneInfo("America/Chicago")
    (line,) = views.export_rows([pledge_row()], zone)
    assert dict(zip(views.HEADINGS, line, strict=True)) == {
        "Family": "Smith, Ann and Bob",
        "Family DUID": "101",
        "ParishSoft status": "",
        "Annual pledge": "$1,200.00",
        "Frequency": "Weekly",
        "Share methods": "Online giving, Other: Stock gift",
        "Reviewer's annual pledge": "$1,300.00",
        "Reviewer's frequency": "Monthly",
        "Annual pledge to enter": "$1,300.00",
        "Frequency to enter": "Monthly",
        "ParishSoft annual pledge": "",
        "Stage": "To enter by hand",
        "Decision": "Accepted (edited)",
        "Entered by": "",
        "Entered at": "",
        "Submitted": "2054-10-06T10:30:00-05:00",
        "Last decision": "staff@example.org",
        "Last decision at": "2054-10-06T10:30:00-05:00",
        "Note": "Checked with the Family",
    }


@pytest.mark.parametrize(
    "amount,message",
    [
        ("", "Enter an annual pledge."),
        ("1000000000", "Enter an annual pledge under $1,000,000,000."),
        ("12.345", "Enter a dollar amount, like 1200 or 1200.50."),
    ],
)
def test_an_edit_amount_is_refused_in_the_family_forms_words(amount, message):
    """A blank amount, the limit and the format are each said as the form says."""
    with pytest.raises(EditRefusal) as refused:
        edited_value(fields(amount, "monthly"))
    assert refused.value.message == message


def test_only_accepted_pledges_name_a_pledge_to_enter_in_the_files():
    """At To review and Ignored the files leave the "to enter" columns blank."""
    zone = ZoneInfo("America/Chicago")
    for found in (item(), item("ignored", review("ignored"))):
        (line,) = views.export_rows([pledge_row(found)], zone)
        cells = dict(zip(views.HEADINGS, line, strict=True))
        assert cells["Annual pledge to enter"] == cells["Frequency to enter"] == ""


@pytest.mark.parametrize(
    "found,body",
    [
        (
            item("entered_by_hand", review("accepted", entered=True)),
            "Entered by hand is undone: the item goes back to To enter by hand, "
            "with its decision kept. Say why.",
        ),
        (
            item(
                "entered_incorrectly",
                review("accepted", entered=True),
                base="entered_by_hand",
                check="entered_incorrectly",
            ),
            "Entered incorrectly is undone: the item goes back to To enter by "
            "hand, with its decision kept. Say why.",
        ),
        (
            item(
                "confirmed",
                review("accepted"),
                base="to_enter_by_hand",
                check="confirmed",
            ),
            "Confirmed is undone: the item goes back to To enter by hand, with "
            "its decision kept. Say why.",
        ),
        (
            item("confirmed", None, base="to_review", check="confirmed"),
            "Confirmed is undone: the item goes back to To review. Say why.",
        ),
    ],
)
def test_a_terminal_pledges_back_to_review_needs_a_note(found, body):
    """Back to review on a terminal pledge (#1840): the note is required (the
    reopen choice), the dialog says where it goes, and a bulk Back to review
    asks for the note while the row is ticked."""
    rows = views.decorate([pledge_row(found)])
    worklist = build(
        Stream.FINANCIAL,
        rows,
        STAFF,
        prefix="pledges",
        action="/admin/reports/financial/pledges/",
        columns=views.COLUMNS,
        stage=found.stage,
        counts={found.stage: 1},
        stages=views.STAGES,
        baseline=False,
    )
    row = worklist.rows[0]
    (back,) = [icon for icon in row["icons"] if not icon["selected"]]
    assert back["action"] == "back_to_review"
    assert back["choice"] == "reopen" and back["body"] == body
    assert row["choice"] == "reopen"
