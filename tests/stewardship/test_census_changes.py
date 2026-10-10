"""Census changes worklist wording, status and filters (#528), without a database."""

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.reports.census_change_views import SORTING
from parishkit.stewardship.reports.census_changes import (
    HEADINGS,
    CensusQuery,
    census_csv,
    display,
    select,
    shape,
    status,
)
from parishkit.stewardship.web.dates import UnknownZone

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def row(**values):
    """One proposal row as the query returns it, with overrides."""
    return {
        "id": "1",
        "entity_kind": "member",
        "entity_key": "41",
        "field": "mobile_phone",
        "baseline_available": True,
        "baseline_value": "555-0100",
        "submitted_value": "555-0199",
        "current_available": True,
        "current_value": "555-0100",
        "admin_value_set": False,
        "admin_value": None,
        "handling": "api",
        "decision": "unreviewed",
        "execution": "pending",
        "submitted_at": SUBMITTED,
        "family_duid": 7001,
        "family_name": "Sample",
        "member_name": "Pat Sample",
    } | values


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # A terminal execution is the outcome, whatever the decision says.
        ({"execution": "published", "decision": "ignored"}, "published"),
        ({"execution": "resolved_upstream"}, "already"),
        ({"execution": "resolved_external", "handling": "manual"}, "entered"),
        ({"execution": "superseded"}, "superseded"),
        ({"execution": "cancelled"}, "cancelled"),
        # Then an ignored decision, then the execution.
        ({"decision": "ignored", "execution": "conflict"}, "ignored"),
        ({"decision": "approved", "execution": "queued"}, "being_published"),
        ({"execution": "conflict"}, "conflict"),
        ({"decision": "approved", "execution": "failed"}, "to_do"),
        # A pending automatic change waits for an Administrator's decision.
        ({}, "to_review"),
        ({"decision": "approved"}, "to_do"),
        ({"handling": "manual", "field": "suffix"}, "to_do"),
        # A Family address is worked by hand: no verified address read exists.
        ({"entity_kind": "family", "field": "home_address"}, "to_do"),
    ],
)
def test_status_follows_the_spec_mapping(values, expected):
    """The status comes from decision and execution in the spec's order."""
    assert status(row(**values)) == expected


def test_display_words_each_value_shape():
    """Addresses, Members, phones, choices and nothing read as plain text."""
    address = {
        "line1": "1 Main St",
        "line2": "",
        "city": "Springfield",
        "region": "IL",
        "postal_code": "62701",
        "country": "US",
    }
    assert display(address) == "1 Main St, Springfield, IL, 62701, US"
    assert display({"display": "(555) 010-0199", "normalized": "+15550100199"}) == (
        "(555) 010-0199"
    )
    assert display(True) == "Yes" and display(False) == "No"
    assert display(None) == ""
    member = {"first_name": "Lee", "last_name": "Sample", "email": "lee@example.org"}
    assert display(member) == "Lee Sample; Email address: lee@example.org"


def test_shape_names_who_and_hides_unread_family_values():
    """A Family field has no ParishSoft value; a new Member is named."""
    family = shape(
        row(
            entity_kind="family",
            field="email_opt_out",
            handling="manual",
            current_available=True,
            current_value=False,
            submitted_value=True,
        )
    )
    assert family["who"] == "Family" and family["parishsoft_now"] == ""
    assert family["label"] == "Opt out of all parish emails"
    assert family["route_label"] == "By hand"
    new = shape(
        row(
            entity_kind="proposed_member",
            entity_key="p1",
            field="new_member",
            handling="manual",
            current_available=False,
            submitted_value={"first_name": "Ari", "last_name": "Sample"},
        )
    )
    assert new["who"] == "New Member: Ari Sample" and new["kind"] == "new_member"
    edited = shape(row(admin_value_set=True, admin_value="555-0123"))
    assert edited["edited"] == "555-0123" and edited["route_label"] == "Automatic"


def rows():
    """One row of each status the default view weighs."""
    return [
        shape(row(id="review")),
        shape(row(id="todo", handling="manual", field="suffix")),
        shape(row(id="conflict", execution="conflict")),
        shape(row(id="done", execution="published", decision="approved")),
        shape(row(id="old", execution="superseded")),
        shape(row(id="moved", field="moved_household", handling="manual")),
    ]


def ids(result):
    """The kept rows' ids."""
    return [item["id"] for item in result["rows"]]


def test_default_view_shows_open_work_and_to_review_only_to_administrators():
    """Staff see To do and Conflict; an Administrator also sees To review."""
    query = CensusQuery()
    assert ids(select(rows(), query, administrator=False)) == [
        "todo",
        "conflict",
        "moved",
    ]
    assert ids(select(rows(), query, administrator=True)) == [
        "review",
        "todo",
        "conflict",
        "moved",
    ]


def test_filters_narrow_the_rows():
    """History, route, kind and search each narrow the list."""
    every = CensusQuery(status="all")
    assert "old" not in ids(select(rows(), every, administrator=True))
    history = CensusQuery(status="all", history="yes")
    assert "old" in ids(select(rows(), history, administrator=True))
    by_hand = CensusQuery(status="all", route="by_hand")
    assert ids(select(rows(), by_hand, administrator=True)) == ["todo", "moved"]
    moved = CensusQuery(status="all", kind="moved")
    assert ids(select(rows(), moved, administrator=True)) == ["moved"]
    found = CensusQuery(status="all", search="7001")
    assert len(ids(select(rows(), found, administrator=True))) == 5
    assert ids(select(rows(), CensusQuery(search="nobody"), administrator=True)) == []
    # The summary counts history only when it is shown.
    summary = dict(select(rows(), every, administrator=True)["summary"])
    assert summary["To do"] == 2 and "Superseded" not in summary
    summary = dict(select(rows(), history, administrator=True)["summary"])
    assert summary["Superseded"] == 1


def test_dates_are_days_in_the_browser_zone():
    """From and Through are whole local days; a date needs a known zone."""
    query = CensusQuery.parse(
        {"start": "2054-10-05", "end": "2054-10-05", "zone": "America/Chicago"}
    )
    assert ids(select(rows(), query, administrator=True))
    later = CensusQuery.parse({"start": "2054-10-06", "zone": "America/Chicago"})
    assert ids(select(rows(), later, administrator=True)) == []
    with pytest.raises(UnknownZone):
        CensusQuery.parse({"start": "2054-10-05"})
    assert CensusQuery.parse({"zone": "Nowhere/Zone"}).zone == ""


@pytest.mark.parametrize(
    "values",
    [
        {"status": "superseded"},
        {"route": "api"},
        {"kind": "pledge"},
        {"history": "1"},
        {"start": "2054-13-01", "zone": "UTC"},
        {"start": "2054-10-06", "end": "2054-10-05", "zone": "UTC"},
        {"unknown": "x"},
    ],
)
def test_parse_refuses_values_outside_the_closed_choices(values):
    """Only the offered choices are accepted, as single values."""
    with pytest.raises(ValueError):
        CensusQuery.parse(values)


def test_csv_keeps_its_own_column_order_in_the_chosen_zone():
    """The download keeps its own column order, times in the chosen zone."""
    result = select(rows(), CensusQuery(), administrator=False)
    text = census_csv(result, ZoneInfo("America/Chicago")).decode()
    table = list(csv.reader(io.StringIO(text)))
    assert tuple(table[0]) == HEADINGS
    assert table[1][2] == "Pat Sample" and table[1][-1] == "2054-10-05T09:30:00-05:00"
    assert len(table) == 4


def test_family_duid_column_sorts_both_ways():
    """Family DUID has its own column, and it sorts both ways (#932)."""
    shaped = [shape(row(id=str(duid), family_duid=duid)) for duid in (7002, 13, 7001)]
    assert SORTING.tokens["duid"] == ("duid", False)
    up = SORTING.sort_rows(shaped, "duid")
    assert [item["family_duid"] for item in up] == [13, 7001, 7002]
    down = SORTING.sort_rows(shaped, "-duid")
    assert [item["family_duid"] for item in down] == [7002, 7001, 13]
