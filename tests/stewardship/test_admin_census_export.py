"""``export census``'s own pieces: its JSON Lines and choices (#463).

The page's CSV, XLSX and PDF, the admission and the event are the page's
own, proven against the page in
``database/test_admin_census_export_cli_postgresql.py``; these tests cover
what only the command adds.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship import admin_census_export
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import census_change_views
from parishkit.stewardship.reports.census_changes import (
    COLUMNS,
    HEADINGS,
    export_rows,
    shape,
)

from .test_admin_reads import FORBIDDEN, members

ADMIN = Principal(uuid4(), frozenset({"administrator"}), frozenset())
STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())


def row(**values):
    """One worded worklist row: a By-hand Family address, open, by default."""
    raw = dict.fromkeys(COLUMNS)
    raw |= {
        "id": uuid4(),
        "entity_kind": "family",
        "entity_key": "family",
        "field": "home_address",
        "submitted_value": "9 Census Way",
        "admin_value_set": False,
        "baseline_available": False,
        "current_available": False,
        "handling": "manual",
        "decision": "unreviewed",
        "execution": "pending",
        "submitted_at": datetime(2054, 10, 3, 14, 5, tzinfo=UTC),
        "family_duid": 1234,
        "family_name": "Example, Pat",
        "version": 3,
    }
    return shape(raw | values)


def records(rows, principal, zone="UTC", *, mutable=True):
    """The JSON Lines file of ``rows``, decoded one record per line."""
    body = admin_census_export.census_jsonl(
        {"rows": rows}, ZoneInfo(zone), principal, mutable=mutable
    )
    assert body.endswith(b"\n") or not body
    return [json.loads(line) for line in body.decode().splitlines()]


def test_each_record_is_the_csv_row_with_its_id_version_and_actions():
    """The cells are the page's export cells under fixed keys, one key per
    heading; the id and version are the row's; the times are in the zone."""
    assert len(admin_census_export.JSONL_KEYS) == len(HEADINGS)
    first, second = row(), row(decision="ignored", version=7)
    found = records([first, second], ADMIN, "America/New_York")
    cells = export_rows({"rows": [first, second]}, ZoneInfo("America/New_York"))
    for record, values, source in zip(found, cells, (first, second), strict=True):
        assert set(record) == {
            *admin_census_export.JSONL_KEYS,
            "proposal_id",
            "version",
            "actions",
        }
        assert [record[key] for key in admin_census_export.JSONL_KEYS] == list(values)
        assert record["proposal_id"] == str(source["id"])
        assert record["version"] == source["version"]
    assert found[0]["family_duid"] == "1234" and found[0]["who"] == "Family"
    assert found[0]["submitted"] == "2054-10-03T10:05:00-04:00"
    # An open By-hand row may be entered or ignored; an ignored one reopened.
    assert found[0]["actions"] == ["entered", "ignored"]
    assert found[1]["actions"] == ["reopened"]
    assert records([], ADMIN) == []


def test_actions_follow_the_reader_s_capabilities():
    """Reopen is the Administrator's alone; nothing is offered on a row the
    page offers nothing on (an automatic change, a published one)."""
    assert records([row()], STAFF)[0]["actions"] == ["entered", "ignored"]
    assert records([row(decision="ignored")], STAFF)[0]["actions"] == []
    automatic = row(entity_kind="member", entity_key="3", handling="api")
    assert records([automatic], ADMIN)[0]["actions"] == []
    done = row(execution="published")
    assert records([done], ADMIN)[0]["actions"] == []
    # While other campaign work holds the campaign, the page offers none.
    held = [row(), row(decision="ignored")]
    assert [record["actions"] for record in records(held, ADMIN, mutable=False)] == [
        [],
        [],
    ]


def test_the_formats_are_the_page_s_and_json_lines():
    """The page's three formats with the page's types, plus the command's."""
    types = admin_census_export.CONTENT_TYPES
    assert {key: types[key] for key in census_change_views.FORMATS} == (
        census_change_views.FORMATS
    )
    assert set(types) - set(census_change_views.FORMATS) == {"jsonl"}


@pytest.mark.parametrize(
    ("fmt", "zone"),
    [("png", "UTC"), ("csv", "Mars/Olympus"), ("CSV", "UTC"), ("jsonl", "")],
)
def test_a_choice_the_page_refuses_is_invalid(fmt, zone):
    """An unknown format or time zone is a ValueError (``invalid``)."""
    with pytest.raises(ValueError):
        admin_census_export.choice(fmt, zone)


def test_a_good_choice_and_the_document():
    """The choice gives the zone; the document describes exactly the bytes."""
    assert admin_census_export.choice("pdf", "America/Chicago") == (
        "pdf",
        ZoneInfo("America/Chicago"),
    )
    campaign = uuid4()
    model = admin_census_export.census_export_model(
        campaign,
        b"abc",
        fmt="jsonl",
        zone_name="UTC",
        count=1,
        now=datetime(2054, 10, 3, 12, 0, 1, tzinfo=UTC),
    )
    assert model.file_name == "stewardship-census-changes-20541003-120001Z.jsonl"
    assert model.content_type == "application/x-ndjson"
    assert model.size == 3 and model.count == 1 and model.campaign_id == campaign
    assert model.sha256 == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_the_document_names_no_person():
    """The standard-error document describes the file: its golden form, and
    no member name the personal-data rule forbids."""
    campaign = uuid4()
    document = admin_census_export.census_export_model(
        campaign,
        b"abc",
        fmt="csv",
        zone_name="America/New_York",
        count=2,
        now=datetime(2054, 10, 3, 12, 0, 1, tzinfo=UTC),
    ).to_document()
    assert document == {
        "campaign_id": str(campaign),
        "file_name": "stewardship-census-changes-20541003-120001Z.csv",
        "content_type": "text/csv",
        "format": "csv",
        "timezone": "America/New_York",
        "size": 3,
        "sha256": ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        "count": 2,
    }
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
