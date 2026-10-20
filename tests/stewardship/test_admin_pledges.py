"""The pledge commands' own pieces (#1733): JSON Lines, choices and refusals.

The page's files, admission, service and events are the page's own, proven
against the page and the SQL guard in
``database/test_admin_pledges_cli_postgresql.py``; these tests cover what
only the commands add.
"""

import json
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship import admin_cli, admin_pledges
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.admin_followup import InvalidFollowup
from parishkit.stewardship.reports import pledge_review_views as views
from parishkit.stewardship.reports.pledge_review import RECORDED, bulk_key_for
from parishkit.stewardship.responses.financial import FREQUENCIES

from .test_admin_cli import run
from .test_pledge_review import item, pledge_row, review

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset())


def records(rows, principal, *, mutable=True):
    """The JSON Lines file of ``rows``, decoded one record per line."""
    body = admin_pledges.pledges_jsonl(
        rows, ZoneInfo("America/New_York"), principal, mutable=mutable
    )
    assert body.endswith(b"\n") or not body
    return [json.loads(line) for line in body.decode().splitlines()]


def test_the_command_line_vocabularies_are_the_pages():
    """Stage, decision, frequency and format choices match the page's own."""
    assert (
        *(stage.value for stage in views.STAGES),
        views.EVERY,
    ) == admin_pledges.STAGE_CHOICES
    assert admin_pledges.DEFAULT_STAGE == views.DEFAULT_STAGE
    assert set(admin_pledges.DECIDE_ACTIONS) == {action.value for action in RECORDED}
    assert set(admin_pledges.REPEATS) == set(admin_pledges.DECIDE_ACTIONS)
    assert tuple(FREQUENCIES) == admin_cli.PLEDGE_FREQUENCIES
    assert {
        key: value
        for key, value in admin_pledges.CONTENT_TYPES.items()
        if key != "jsonl"
    } == views.FORMATS
    # Each key names its heading, in the files' order.
    assert dict(zip(views.HEADINGS, admin_pledges.JSONL_KEYS, strict=True)) == {
        "Family": "family",
        "Family DUID": "family_duid",
        "ParishSoft status": "parishsoft_status",
        "Annual pledge": "annual_pledge",
        "Frequency": "frequency",
        "Share methods": "share_methods",
        "Reviewer's annual pledge": "reviewer_annual_pledge",
        "Reviewer's frequency": "reviewer_frequency",
        "Annual pledge to enter": "annual_pledge_to_enter",
        "Frequency to enter": "frequency_to_enter",
        "Stage": "stage",
        "Decision": "decision",
        "Entered by": "entered_by",
        "Entered at": "entered_at",
        "Submitted": "submitted",
        "Last decision": "last_decision",
        "Last decision at": "last_decision_at",
        "Note": "note",
    }


def test_each_record_is_the_csv_row_with_its_item_version_and_actions():
    """The cells are the files' under fixed keys; the item, version and the
    actions the reader's role and the row's stage offer are the row's."""
    entered = pledge_row()
    fresh = pledge_row(item())
    found = records([entered, fresh], STAFF)
    cells = views.export_rows([entered, fresh], ZoneInfo("America/New_York"))
    for record, values, source in zip(found, cells, (entered, fresh), strict=True):
        assert set(record) == {
            *admin_pledges.JSONL_KEYS,
            "item_id",
            "version",
            "actions",
        }
        assert [record[key] for key in admin_pledges.JSONL_KEYS] == list(values)
        assert record["item_id"] == str(source["item"].submission_id)
        assert record["version"] == source["item"].sequence
    assert found[0]["actions"] == ["back_to_review", "entered_by_hand"]
    assert found[1]["actions"] == ["accept", "edit", "ignore"]
    assert found[0]["annual_pledge_to_enter"] == "$1,300.00"
    # A leader is offered nothing; neither is anyone while other campaign
    # work holds the campaign.
    assert all(not record["actions"] for record in records([fresh], LEADER))
    assert all(
        not record["actions"] for record in records([fresh], STAFF, mutable=False)
    )
    assert records([], STAFF) == []


def test_an_entered_pledge_offers_no_action():
    """Entered by hand is final: its row offers nothing."""
    row = pledge_row(item("entered_by_hand", review("accepted", entered=True)))
    assert records([row], STAFF)[0]["actions"] == []


@pytest.mark.parametrize(
    "fmt,zone", [("json", "UTC"), ("csv", "Mars/Olympus"), ("jsonl", "Nowhere")]
)
def test_an_unknown_format_or_zone_is_invalid(fmt, zone):
    """Only the page's formats, the command's JSON Lines and real zones."""
    with pytest.raises(ValueError):
        admin_pledges.choice(fmt, zone)


def test_the_choice_is_refused_before_any_prompt():
    """More than the bulk bar takes, an Edit of several, a long note or an
    Edit value the Family form refuses is invalid, at its option."""
    many = {uuid4(): 0 for _ in range(101)}
    with pytest.raises(InvalidFollowup):
        admin_pledges.check_choice(many, "accept")
    with pytest.raises(InvalidFollowup) as refused:
        admin_pledges.check_choice({uuid4(): 0, uuid4(): 1}, "edit")
    assert refused.value.fields[0]["field"] == "item"
    assert admin_pledges.check_choice({uuid4(): 0, uuid4(): 1}, "accept") == ({}, None)
    one = {uuid4(): 0}
    fields, value = admin_pledges.check_choice(
        one, "edit", amount="1,300", frequency="monthly"
    )
    assert fields["value_amount"] == ["1,300"]
    assert value == (Decimal("1300.00"), "monthly")
    for options, field in (
        ({"amount": "100"}, "frequency"),
        ({"amount": "ten", "frequency": "weekly"}, "amount"),
    ):
        with pytest.raises(InvalidFollowup) as refused:
            admin_pledges.check_choice(one, "edit", **options)
        assert [problem["field"] for problem in refused.value.fields] == [field]
    with pytest.raises(InvalidFollowup) as refused:
        admin_pledges.check_choice(one, "entered_by_hand", note="x" * 2001)
    assert refused.value.fields[0]["field"] == "note"
    admin_pledges.check_choice(one, "entered_by_hand", note="x" * 2000)


def test_only_an_edit_takes_a_value():
    """An Edit needs --amount; any other decision takes neither option."""
    assert admin_pledges._edit_fields("edit", "1,200", "monthly") == {
        "value_amount": ["1,200"],
        "value_frequency": ["monthly"],
    }
    assert admin_pledges._edit_fields("edit", "0", None) == {
        "value_amount": ["0"],
        "value_frequency": [""],
    }
    assert admin_pledges._edit_fields("accept", None, None) == {}
    for action, amount, frequency in (
        ("edit", None, "weekly"),
        ("accept", "100", None),
        ("ignore", None, "weekly"),
    ):
        with pytest.raises(InvalidFollowup):
            admin_pledges._edit_fields(action, amount, frequency)


def test_one_pledge_uses_the_key_and_several_derive_theirs():
    """A single item records the key itself; a bulk one each item's own."""
    key, first, second = uuid4(), uuid4(), uuid4()
    assert admin_pledges._keys({first: 0}, key) == {first: key}
    keys = admin_pledges._keys({first: 0, second: 2}, key)
    # Exactly the service's own derivation, so a replay is found again.
    assert keys == {first: bulk_key_for(key, first), second: bulk_key_for(key, second)}
    assert len(set(keys.values())) == 2 and key not in keys.values()


@pytest.mark.parametrize(
    "argv",
    [
        ["pledges", "decide", "--action", "accept"],
        ["pledges", "decide", "--action", "add", "--item", f"{uuid4()}:0"],
        ["pledges", "decide", "--action", "accept", "--item", str(uuid4())],
        ["pledges", "decide", "--action", "accept", "--item", "nope:0"],
        ["pledges", "decide", "--action", "accept", "--item", f"{uuid4()}:-1"],
        [
            *("pledges", "decide", "--action", "edit", "--item", f"{uuid4()}:0"),
            *("--amount", "10", "--frequency", "daily"),
        ],
        ["export", "pledges", "--format", "json"],
        ["export", "pledges", "--search", "Private"],
        ["report", "pledges", "--stage", "history"],
    ],
)
def test_options_outside_the_pages_vocabulary_are_usage_errors(argv):
    """A malformed choice is refused before anything is admitted (exit 2)."""
    code, document = run([*argv, "--config", "x", "--session-stdin"])
    assert code == 2 and document["error"]["code"] == "usage", document
