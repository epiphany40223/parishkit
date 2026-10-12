"""``events list`` and ``events acknowledge`` (ADM-11 PR 11).

Pure tests: the golden documents, built through the projections the
commands use from stand-ins for the page's own reads; the catalog entries;
the ledger; and usage errors. The commands against a real database are in
database/test_admin_events_cli_postgresql.py.
"""

import io
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_events
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.admin_reads import plain
from parishkit.stewardship.audit.critical_events import shown
from parishkit.stewardship.observability import Event

from .test_admin_reads import FORBIDDEN, members

EVENT = UUID("00000000-0000-4000-8000-000000000091")
ACTOR = UUID("00000000-0000-4000-8000-000000000092")
ROWS = [UUID(int=index) for index in (3, 1, 2)]
WHEN = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)
# The members ``events list`` may print, at any depth. ``target`` is the
# address or domain the event is about (the page names it; it is a portal
# user's or a rule's, never a Family's) and ``token`` the banner's signed
# row list, so both pass the personal-data pattern for this command alone.
ALLOWED = {
    "security",
    "id",
    "kind",
    "name",
    "target",
    "before",
    "after",
    "key_before",
    "key_after",
    "created_at",
    "actor_id",
    "critical",
    "count",
    "all_ended",
    "since",
    "events",
    "event",
    "ended_at",
    "token",
    "acknowledgeable",
}
PUBLIC = {"token"}


def page_row(**values):
    """A stand-in ``open_events`` row for an Administrator grant."""
    return {
        "id": EVENT,
        "kind": "administrator_granted",
        "label": "Administrator added to an exact address",
        "target": "new@example.org",
        "before": [],
        "after": ["Administrator"],
        "key_ids": None,
        "created_at": WHEN,
        "actor": "admin@example.org",
        "actor_id": ACTOR,
        **values,
    }


def test_events_list_is_its_golden_projection():
    """The page's rows with the actor by account; the banner's groups in its
    order with the signed list of exactly its rows."""
    counts = {Event.TASK_FAILED.value: 1, Event.SOURCE_INVALID.value: 2}
    ended = {Event.TASK_FAILED.value: WHEN}
    result = admin_events.EventsList(
        security=[admin_events.security_model(page_row())],
        critical=admin_events.critical_model(counts, ROWS, ended, WHEN),
    ).to_document()
    token = result["critical"].pop("token")
    assert shown(token) == ROWS
    assert result == {
        "security": [
            {
                "id": str(EVENT),
                "kind": "administrator_granted",
                "name": "Administrator added to an exact address",
                "target": "new@example.org",
                "before": [],
                "after": ["Administrator"],
                "key_before": None,
                "key_after": None,
                "created_at": plain(WHEN),
                "actor_id": str(ACTOR),
            }
        ],
        "critical": {
            "count": 3,
            "all_ended": False,
            "since": plain(WHEN),
            "events": [
                {
                    "event": Event.SOURCE_INVALID.value,
                    "name": "ParishSoft data refresh failed",
                    "count": 2,
                    "ended_at": None,
                },
                {
                    "event": Event.TASK_FAILED.value,
                    "name": "Background task failed",
                    "count": 1,
                    "ended_at": plain(WHEN),
                },
            ],
            "acknowledgeable": 3,
        },
    }
    assert set(members({**result, "token": token})) == ALLOWED
    for name in ALLOWED - PUBLIC:
        assert FORBIDDEN.search(name) is None, name


def test_a_replaced_key_prints_its_key_ids_and_no_actor():
    """A replaced backup key names the keys; an unknown earlier key is null."""
    row = admin_events.security_model(
        page_row(
            kind="backup_key_replaced",
            key_ids=(None, "key-2"),
            before=[],
            after=[],
            actor_id=None,
        )
    )
    assert (row["key_before"], row["key_after"], row["actor_id"]) == (
        None,
        "key-2",
        None,
    )


def test_an_empty_banner_has_no_token():
    """Nothing awaiting: no groups, nothing to acknowledge, not all ended."""
    assert admin_events.critical_model({}, [], {}, WHEN) == {
        "count": 0,
        "all_ended": False,
        "since": WHEN,
        "events": [],
        "token": None,
        "acknowledgeable": 0,
    }


def test_a_banner_whose_problems_all_ended_says_so():
    """Every group ended (#633): ``all_ended``, as the banner says."""
    counts = {Event.TASK_FAILED.value: 1}
    model = admin_events.critical_model(
        counts, ROWS[:1], {Event.TASK_FAILED.value: WHEN}, WHEN
    )
    assert model["all_ended"] and model["acknowledgeable"] == 1


def test_the_catalog_entries():
    """events list: any session, no event; events acknowledge: full scope,
    a state change with its described event, no prompt or recent sign-in."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    listing = entries["events list"]
    assert listing["scope"] == "read_only" and not listing["changes_state"]
    assert listing["audit_event"] is None and listing["pr"] == 11
    assert listing["result_fields"] == ["security", "critical"]
    assert {option["name"] for option in listing["options"]} == {
        "--config",
        "--session-stdin",
    }
    change = entries["events acknowledge"]
    assert change["scope"] == "full" and change["changes_state"]
    assert change["audit_event"] == "admin_cmd_events_acknowledge"
    assert change["pr"] == 11
    assert not change["prompts"] and not change["fresh_gated"]
    assert change["result_fields"] == ["kind", "event_id", "acknowledged"]
    assert {option["name"] for option in change["options"]} == {
        "--config",
        "--session-stdin",
        "--security",
        "--critical",
    }


def test_the_ledger_covers_both_acknowledgements():
    """Both Acknowledge routes are covered, with nothing still owed."""
    for route in ("security_event_acknowledge", "critical_events_acknowledge"):
        assert LEDGER[route].kind == "command"
        assert LEDGER[route].detail == ("events list", "events acknowledge")
        assert LEDGER[route].owed is None


@pytest.mark.parametrize(
    "argv",
    [
        ["events", "list", "extra"],
        ["events", "acknowledge"],
        ["events", "acknowledge", "--security", str(EVENT), "--critical", "x"],
        ["events", "acknowledge", "--security", "not-a-uuid"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """events acknowledge takes exactly one of a security event's UUID and a
    critical token; events list takes nothing."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
