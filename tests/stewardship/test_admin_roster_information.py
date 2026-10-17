"""``followup roster-entered``, ``roster-bulk``, ``information-show`` and
``information-update`` (ADM-11 PR 11b).

Pure tests: the golden documents, built through the projections the
commands use from stand-ins for the pages' own rows; the catalog entries;
the ledger; the service's refusals as field errors; and usage errors. The
commands against a real database are in
database/test_admin_roster_information_cli_postgresql.py.
"""

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_information, admin_roster_tick
from parishkit.stewardship.admin_followup import InvalidFollowup
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.admin_reads import plain

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-0000000000d1")
ITEM = UUID("00000000-0000-4000-8000-0000000000d2")
REVISION = UUID("00000000-0000-4000-8000-0000000000d3")
ACTOR = UUID("00000000-0000-4000-8000-0000000000d4")
WHEN = datetime(2054, 10, 5, 14, tzinfo=UTC)
# What the stand-in item carries that no document may print: the Family's
# name and DUID, its words and the Staff notes.
PRIVATE = ("Private, Ann", "Private request text", "Private staff notes", "12345")
# Every member the documents may print, at any depth.
ALLOWED = {
    "campaign_id",
    "item",
    "history_page",
    "history",
    "more_history",
    "id",
    "version",
    "disposition",
    "submitted_at",
    "follow_up_needed",
    "completed",
    "followed_up_at",
    "followed_up_by",
    "replacement_id",
    "previously_reported",
    "correction_resolved",
    "expected_version",
    "followed_up",
    "confirm_clear",
    "actor_id",
    "created_at",
    "created",
    "request_key",
    "revision",
    "entry",
    "entries",
    "changed",
    "request_id",
    "sequence",
    "entered",
}


def page_row():
    """A stand-in row of the information page's own selection."""
    return {
        "id": ITEM,
        "version": 3,
        "disposition": "current_actionable",
        "submitted_at": WHEN,
        "follow_up_needed": True,
        "followed_up_at": WHEN,
        "followed_up_by_id": ACTOR,
        "replacement_id": None,
        "previously_reported": False,
        "correction_resolved": None,
        "family_name": PRIVATE[0],
        "text": PRIVATE[1],
        "notes": PRIVATE[2],
        "family_duid": int(PRIVATE[3]),
        "active": True,
    }


def revision():
    """A stand-in history revision, notes included."""
    return SimpleNamespace(
        pk=REVISION,
        expected_version=2,
        follow_up_needed=True,
        followed_up=True,
        confirm_clear=False,
        followed_up_at=WHEN,
        followed_up_by_id=ACTOR,
        actor_id=ACTOR,
        created_at=WHEN,
        notes=PRIVATE[2],
    )


def entry():
    """A stand-in roster tick entry."""
    return SimpleNamespace(
        pk=REVISION, request_id=ITEM, sequence=2, entered=False, created_at=WHEN
    )


def documents():
    """Each command's document, built as the command builds it."""
    show = admin_information.InformationShow(
        campaign_id=CAMPAIGN,
        item=admin_information._item(page_row()),
        history_page=1,
        history=[admin_information._revision(revision())],
        more_history=False,
    )
    update = admin_information.InformationUpdate(
        created=True,
        request_key=REVISION,
        revision=admin_information._revision(revision()),
        item={
            "id": ITEM,
            "version": 4,
            "follow_up_needed": True,
            "followed_up_at": WHEN,
        },
    )
    tick = admin_roster_tick.RosterEntered(
        created=True, request_key=REVISION, entry=admin_roster_tick._entry(entry())
    )
    bulk = admin_roster_tick.RosterBulk(
        created=False,
        request_key=REVISION,
        changed=0,
        entries=[admin_roster_tick._entry(entry())],
    )
    return [plain(model.to_document()) for model in (show, update, tick, bulk)]


def test_the_documents_are_their_golden_projections():
    """The item read prints its state and history; the changes their record."""
    show, update, tick, bulk = documents()
    assert show["item"] == {
        "id": str(ITEM),
        "version": 3,
        "disposition": "current_actionable",
        "submitted_at": "2054-10-05T14:00:00+00:00",
        "follow_up_needed": True,
        "completed": True,
        "followed_up_at": "2054-10-05T14:00:00+00:00",
        "followed_up_by": str(ACTOR),
        "replacement_id": None,
        "previously_reported": False,
        "correction_resolved": False,
    }
    assert show["history"][0]["expected_version"] == 2
    assert update["revision"]["followed_up"] is True
    assert tick["entry"] == {
        "id": str(REVISION),
        "request_id": str(ITEM),
        "sequence": 2,
        "entered": False,
        "created_at": "2054-10-05T14:00:00+00:00",
    }
    assert (bulk["created"], bulk["changed"]) == (False, 0)


def test_no_member_or_value_is_personal_data():
    """No document prints the Family, its words or the notes, under any name."""
    for document in documents():
        names = set(members(document))
        assert names <= ALLOWED, names - ALLOWED
        assert not any(FORBIDDEN.search(name) for name in names), names
        assert not any(value in json.dumps(document) for value in PRIVATE)


def test_the_catalog_entries():
    """The read: any session, the page's view event. The changes: full
    scope, keyed and versioned; only the bulk mark asks. None is
    fresh-gated, as the pages ask for no recent sign-in."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    show = entries["followup information-show"]
    assert show["scope"] == "read_only" and not show["changes_state"]
    assert show["audit_event"] == "information_viewed"
    assert not show["prompts"] and not show["fresh_gated"] and show["pr"] == 11
    assert show["arguments"] == ["ITEM_ID"]
    assert show["result_fields"] == [
        "campaign_id",
        "item",
        "history_page",
        "history",
        "more_history",
    ]
    for name, event, prompts, fields in (
        (
            "followup roster-entered",
            "admin_cmd_followup_roster_entered",
            False,
            ["created", "request_key", "entry"],
        ),
        (
            "followup roster-bulk",
            "admin_cmd_followup_roster_bulk",
            True,
            ["created", "request_key", "changed", "entries"],
        ),
        (
            "followup information-update",
            "admin_cmd_followup_information_update",
            False,
            ["created", "request_key", "revision", "item"],
        ),
    ):
        spec = entries[name]
        assert spec["scope"] == "full" and spec["changes_state"], name
        assert spec["request_key"] and spec["expected_version"], name
        assert spec["prompts"] is prompts and not spec["fresh_gated"], name
        assert spec["audit_event"] == event and spec["pr"] == 11, name
        assert spec["result_fields"] == fields, name
    options = {
        option["name"]: option
        for option in entries["followup roster-entered"]["options"]
    }
    assert options["--expected-version"]["required"]
    assert {"--set", "--clear"} <= set(options)
    options = {
        option["name"]: option
        for option in entries["followup information-update"]["options"]
    }
    assert options["--expected-version"]["required"]
    assert not any(
        options[name]["required"]
        for name in ("--needed", "--completed", "--notes", "--confirm-clear")
    )
    assert "--yes" in {
        option["name"] for option in entries["followup roster-bulk"]["options"]
    }


def test_the_parser_s_limits_are_the_page_s():
    """The notes limit and the bulk size are the services' own."""
    from parishkit.stewardship.workflows.roster import MAX_BULK

    assert admin_cli.INFORMATION_NOTES_LIMIT == admin_information.MAX_NOTES * 4
    assert admin_information.MAX_NOTES == 5000 and MAX_BULK == 250


def test_the_ledger_covers_the_tick_its_bulk_and_information_follow_up():
    """The tick, the bulk mark and an information item's page and Save are
    covered (the queue's export is PR 11c's, test_admin_followup.py)."""
    for route, commands in (
        ("ministry_followup_roster", ("followup show", "followup roster-entered")),
        ("ministry_roster_bulk", ("report roster", "followup roster-bulk")),
        ("information_item", ("followup information-show",)),
        (
            "information_update",
            ("followup information-show", "followup information-update"),
        ),
    ):
        assert LEDGER[route].kind == "command", route
        assert LEDGER[route].detail == commands, route
        assert LEDGER[route].owed is None, route


@pytest.mark.parametrize(
    ("message", "field", "expected"),
    [
        (
            "This request does not take the roster tick.",
            "set",
            ("request", admin_roster_tick.NOT_ELIGIBLE),
        ),
        (
            "The roster tick already says so.",
            "clear",
            ("clear", admin_roster_tick.ALREADY),
        ),
    ],
)
def test_the_service_s_refusals_are_field_errors(message, field, expected):
    """A request that takes no tick, or a tick that already says so, names
    the option that would correct it."""
    refused = admin_roster_tick._refused(ValueError(message), field=field)
    assert isinstance(refused, InvalidFollowup)
    assert [(item["field"], item["message"]) for item in refused.fields] == [expected]


def test_a_bound_key_stays_a_plain_refusal():
    """A key already bound to another tick is ``invalid`` with no field."""
    error = ValueError("This request key is already bound.")
    assert admin_roster_tick._refused(error, field="set") is error


def test_a_bulk_choice_past_a_page_is_refused_before_admission():
    """More than 250 requests is a field error before anything is read."""
    versions = {UUID(int=number): 0 for number in range(251)}
    with pytest.raises(InvalidFollowup) as refused:
        admin_roster_tick.roster_bulk(
            None, None, versions, request_key=REVISION, context={}
        )
    assert refused.value.fields[0]["field"] == "request"


def test_notes_past_the_page_s_limit_are_refused_before_admission():
    """Notes past 5,000 characters (counted as the browser counts them)."""
    with pytest.raises(InvalidFollowup) as refused:
        admin_information.update_information_item(
            None,
            None,
            ITEM,
            expected_version=1,
            request_key=REVISION,
            needed=None,
            completed=None,
            confirm_clear=False,
            notes="x" * 5001,
            context={},
        )
    assert refused.value.fields[0]["field"] == "notes"
    # CRLF counts as one line break, as the page's form keeps it.
    assert admin_information._canonical("a\r\nb\rc") == "a\nb\nc"


@pytest.mark.parametrize(
    "argv",
    [
        ["followup", "roster-entered", str(ITEM), "--expected-version", "0"],
        [
            *("followup", "roster-entered", str(ITEM), "--set", "--clear"),
            *("--expected-version", "0"),
        ],
        ["followup", "roster-entered", str(ITEM), "--set"],
        ["followup", "roster-entered", str(ITEM), "--set", "--expected-version", "-1"],
        ["followup", "roster-bulk"],
        ["followup", "roster-bulk", "--request", str(ITEM)],
        ["followup", "roster-bulk", "--request", f"{ITEM}:x"],
        ["followup", "information-show"],
        ["followup", "information-show", str(ITEM), "--page", "0"],
        ["followup", "information-update", str(ITEM)],
        [
            *("followup", "information-update", str(ITEM)),
            *("--expected-version", "0"),
        ],
        [
            *("followup", "information-update", str(ITEM)),
            *("--expected-version", "1", "--needed", "maybe"),
        ],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """One of --set and --clear, a tick's sequence, an item's version, yes or no."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_the_matched_refusals_are_the_service_s_own_words():
    """``_refused`` matches the service's messages, so a reworded refusal
    would fail here rather than silently lose its field."""
    import inspect

    from parishkit.stewardship.workflows import roster

    source = inspect.getsource(roster._record)
    assert '"This request does not take the roster tick."' in source
    assert '"The roster tick already says so."' in source
