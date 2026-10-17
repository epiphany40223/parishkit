"""``followup list``, ``followup show``, ``followup update`` and ``followup bulk``.

ADM-11 PR 11a. Pure tests: the golden documents, built through the
projections the commands use from stand-ins for the page's own rows; the
catalog entries; the parser's closed choices against the page's; the ledger;
the contact instant and the page's refusals as field errors; and usage
errors. The commands against a real database are in
database/test_admin_followup_cli_postgresql.py.
"""

import io
import json
from argparse import Namespace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_followup
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.admin_reads import plain

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-0000000000c1")
REQUEST = UUID("00000000-0000-4000-8000-0000000000c2")
REVISION = UUID("00000000-0000-4000-8000-0000000000c3")
ACTOR = UUID("00000000-0000-4000-8000-0000000000c4")
WHEN = datetime(2054, 10, 5, 14, tzinfo=UTC)
# A Member name, notes and contact times the stand-in carries: none may
# reach a document.
PRIVATE = ("Private, Ann", "Private notes", "Private contact")
# Every member ``followup list`` and ``followup show`` may print, at any
# depth. None names a Member, a Family or a contact.
ALLOWED = {
    "campaign_id",
    "filters",
    "ministry",
    "action",
    "state",
    "outcome",
    "sort",
    "page",
    "size",
    "total",
    "rows",
    "request",
    "history_page",
    "history",
    "more_history",
    "id",
    "version",
    "ministry_name",
    "in_campaign",
    "kind",
    "open",
    "member_status",
    "submitted_at",
    "resolved_at",
    "source_resolved",
    "latest",
    "last_contact_at",
    "roster",
    "eligible",
    "entered",
    "sequence",
    "request_id",
    "expected_version",
    "contact_channel",
    "contact_at",
    "actor_id",
    "created_at",
    "bulk",
}


def page_row(**values):
    """A stand-in follow-up selection row, marked with its roster tick."""
    return {
        "id": str(REQUEST),
        "version": 3,
        "ministry_duid": 9,
        "ministry_name": "Choir",
        "in_campaign": True,
        "member_name": PRIVATE[0],
        "member_label": PRIVATE[0],
        "member_duid": 3,
        "entity_kind": "member",
        "action": "join",
        "state": "resolved",
        "outcome": "joined",
        "open": False,
        "member_status": None,
        "cannot_serve": False,
        "submitted_at": WHEN,
        "resolved_at": WHEN,
        "source_resolved": False,
        "latest": True,
        "notes": PRIVATE[1],
        "email_contact_at": WHEN,
        "phone_contact_at": None,
        "last_contact_at": WHEN,
        "roster_eligible": True,
        "roster_entered": False,
        "roster_sequence": 0,
        "roster_at": None,
        "roster_by": "staff@example.org",
        **values,
    }


ROW = {
    "id": str(REQUEST),
    "version": 3,
    "ministry": 9,
    "ministry_name": "Choir",
    "in_campaign": True,
    "kind": "member",
    "action": "join",
    "state": "resolved",
    "outcome": "joined",
    "open": False,
    "member_status": "current",
    "submitted_at": plain(WHEN),
    "resolved_at": plain(WHEN),
    "source_resolved": False,
    "latest": True,
    "last_contact_at": plain(WHEN),
    "roster": {"eligible": True, "entered": False, "sequence": 0},
}


def revision():
    """A stand-in history revision, with notes the document leaves out."""
    return SimpleNamespace(
        pk=REVISION,
        request_id=REQUEST,
        expected_version=2,
        state="resolved",
        outcome="joined",
        notes=PRIVATE[1],
        contact_channel="phone",
        contact_at=WHEN,
        contact_notes=PRIVATE[2],
        actor_id=ACTOR,
        created_at=WHEN,
        bulk=True,
    )


def documents():
    """A list and a show document, built through the commands' projections."""
    listing = admin_followup.FollowupList(
        campaign_id=CAMPAIGN,
        filters={
            "ministry": "9",
            "action": "any",
            "state": "any",
            "outcome": "any",
            "sort": "newest",
        },
        page=1,
        size=50,
        total=1,
        rows=[admin_followup._row(page_row())],
    ).to_document()
    show = admin_followup.FollowupShow(
        campaign_id=CAMPAIGN,
        request=admin_followup._row(page_row()),
        history_page=1,
        history=[admin_followup._history_row(revision())],
        more_history=False,
    ).to_document()
    return listing, show


def test_the_documents_are_their_golden_projections():
    """The page's rows without the Member, the notes or the contact text."""
    listing, show = documents()
    assert listing == {
        "campaign_id": str(CAMPAIGN),
        "filters": {
            "ministry": "9",
            "action": "any",
            "state": "any",
            "outcome": "any",
            "sort": "newest",
        },
        "page": 1,
        "size": 50,
        "total": 1,
        "rows": [ROW],
    }
    assert show == {
        "campaign_id": str(CAMPAIGN),
        "request": ROW,
        "history_page": 1,
        "history": [
            {
                "id": str(REVISION),
                "request_id": str(REQUEST),
                "expected_version": 2,
                "state": "resolved",
                "outcome": "joined",
                "contact_channel": "phone",
                "contact_at": plain(WHEN),
                "actor_id": str(ACTOR),
                "created_at": plain(WHEN),
                "bulk": True,
            }
        ],
        "more_history": False,
    }


def test_no_member_or_value_is_personal_data():
    """Exactly the allowlisted members, none matching the personal-data
    pattern, and no Member name, DUID, notes or tick author printed."""
    listing, show = documents()
    assert set(members(listing)) | set(members(show)) == ALLOWED
    for name in ALLOWED:
        assert FORBIDDEN.search(name) is None, name
    text = json.dumps([listing, show])
    assert "@" not in text
    for value in PRIVATE:
        assert value not in text


def test_a_member_no_longer_in_parishsoft_prints_its_status():
    """The queue's mark for a Member ParishSoft no longer lists (#1217)."""
    assert admin_followup._row(page_row(member_status="moved"))["member_status"] == (
        "moved"
    )


def test_the_catalog_entries():
    """Reads: any session, the page's view event. Save: full scope, keyed,
    versioned, asking nothing. Bulk: full scope, keyed, asking first. None
    is fresh-gated, as the page asks for no recent sign-in."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    for name, fields in (
        ("followup list", ["campaign_id", "filters", "page", "size", "total", "rows"]),
        (
            "followup show",
            ["campaign_id", "request", "history_page", "history", "more_history"],
        ),
    ):
        entry = entries[name]
        assert entry["scope"] == "read_only" and not entry["changes_state"], name
        assert entry["audit_event"] == "ministry_followup_viewed", name
        assert not entry["prompts"] and not entry["fresh_gated"], name
        assert entry["pr"] == 11 and entry["result_fields"] == fields, name
    assert entries["followup show"]["arguments"] == ["REQUEST_ID"]
    listing = {option["name"]: option for option in entries["followup list"]["options"]}
    assert "--search" not in listing
    assert listing["--size"]["choices"] == ["25", "50", "100", "250"]

    update = entries["followup update"]
    assert update["scope"] == "full" and update["changes_state"]
    assert update["request_key"] and update["expected_version"]
    assert not update["prompts"] and not update["fresh_gated"]
    assert update["audit_event"] == "admin_cmd_followup_update"
    assert update["arguments"] == ["REQUEST_ID"]
    assert update["result_fields"] == ["created", "request_key", "revision", "request"]
    options = {option["name"]: option for option in update["options"]}
    assert options["--state"]["required"] and options["--expected-version"]["required"]
    assert not options["--notes"]["required"]

    bulk = entries["followup bulk"]
    assert bulk["scope"] == "full" and bulk["changes_state"]
    assert bulk["request_key"] and bulk["prompts"] and not bulk["fresh_gated"]
    assert bulk["audit_event"] == "admin_cmd_followup_bulk"
    assert bulk["result_fields"] == [
        "created",
        "request_key",
        "outcome",
        "changed",
        "requests",
    ]
    options = {option["name"]: option for option in bulk["options"]}
    assert options["--outcome"]["required"] and options["--request"]["required"]
    assert "--yes" in options


def test_the_parser_s_choices_are_the_page_s():
    """The closed choices spelled out for the parser equal the page's own."""
    from parishkit.stewardship.reports.ministry_followup import (
        FILTER_OUTCOMES,
        NOT_IN_SOURCE,
        STATES,
    )
    from parishkit.stewardship.web.tables import PAGE_SIZES
    from parishkit.stewardship.workflows.followup import (
        BULK_ACTIONS,
        MAX_BULK,
        MAX_CONTACT_NOTES,
        MAX_NOTES,
        RETIRED_OUTCOMES,
    )
    from parishkit.stewardship.workflows.models import (
        CONTACT_CHANNELS,
        RESOLVED_OUTCOMES,
        STAFF_STATES,
    )

    assert set(admin_cli.FOLLOWUP_STATES) == {"unresolved", "any", NOT_IN_SOURCE} | set(
        STATES
    )
    assert set(admin_cli.FOLLOWUP_OUTCOMES) == {"any", *FILTER_OUTCOMES}
    assert admin_cli.FOLLOWUP_SAVE_STATES == STAFF_STATES
    assert set(admin_cli.FOLLOWUP_RESOLVED) == set(RESOLVED_OUTCOMES) - RETIRED_OUTCOMES
    assert admin_cli.FOLLOWUP_CHANNELS == CONTACT_CHANNELS
    assert tuple(f"{a}:{b}" for a, b in BULK_ACTIONS) == admin_cli.FOLLOWUP_BULK
    assert admin_cli.FOLLOWUP_NOTES_LIMIT == MAX_NOTES * 4
    assert admin_cli.FOLLOWUP_CONTACT_NOTES_LIMIT == MAX_CONTACT_NOTES * 4
    assert MAX_BULK == 250
    assert {str(size) for size in PAGE_SIZES} == {"25", "50", "100", "250"}


def test_the_ledger_covers_the_queue_its_requests_and_the_bulk_outcome():
    """The queue, a request's page, its Save and the bulk outcome are
    covered; the queue's export stays pending in PR 11 (the roster tick and
    Family information follow-up are PR 11b's,
    test_admin_roster_information.py)."""
    for route, commands in (
        ("ministry_report", ("report ministry", "export ministry", "followup list")),
        ("ministry_followup_item", ("followup show",)),
        ("ministry_followup_update", ("followup show", "followup update")),
        ("ministry_followup_bulk", ("followup list", "followup bulk")),
    ):
        assert LEDGER[route].kind == "command", route
        assert LEDGER[route].detail == commands, route
        assert LEDGER[route].owed is None, route
    route = "ministry_followup_export"
    assert LEDGER[route].kind == "pending" and LEDGER[route].pr == "PR 11"


def test_the_contact_instant_needs_its_offset():
    """An ISO instant with its offset; anything else names contact_at."""
    assert admin_followup.contact_instant("2054-10-05T10:00:00-04:00") == WHEN
    for text in ("2054-10-05T10:00:00", "yesterday", ""):
        with pytest.raises(admin_followup.InvalidFollowup) as refused:
            admin_followup.contact_instant(text)
        [problem] = refused.value.fields
        assert (problem["field"], problem["code"]) == ("contact_at", "invalid")


@pytest.mark.parametrize(
    ("values", "field"),
    [
        # Resolved without an outcome: the page's words, at --outcome.
        ({"state": "resolved", "outcome": None}, "outcome"),
        # The outcome Other needs notes.
        ({"state": "resolved", "outcome": "other"}, "notes"),
        # A contact channel without its instant.
        ({"state": "in_progress", "outcome": None, "channel": "phone"}, "contact_at"),
    ],
)
def test_the_page_s_refusals_are_field_errors(values, field):
    """A correctable refusal is ``invalid`` at the option that sets it."""
    arguments = {
        "notes": "",
        "channel": None,
        "contact_at": None,
        "contact_notes": "",
    } | values
    with pytest.raises(admin_followup.InvalidFollowup) as refused:
        admin_followup.change_values(**arguments)
    [problem] = refused.value.fields
    assert problem["field"] == field and problem["code"] == "invalid"
    assert problem["message"]


def test_closed_no_response_records_no_response():
    """Closed: no response records No response, as on the page."""
    change = admin_followup.change_values(
        state="closed_no_response",
        outcome=None,
        notes="",
        channel=None,
        contact_at=None,
        contact_notes=None,
    )
    assert (change.outcome, change.contact_notes) == ("no_response", "")


def test_a_contact_in_the_future_says_so_for_the_command_line():
    """The page's message is about the browser's clock; this one names the
    option the instant was typed in."""
    from parishkit.stewardship.workflows.followup import FollowupRefusal

    [problem] = admin_followup.refused(FollowupRefusal("contact_future")).fields
    assert problem == {
        "field": "contact_at",
        "code": "invalid",
        "message": admin_followup.FUTURE_CONTACT,
    }
    assert "--contact-at" in problem["message"] and "clock" not in problem["message"]


def test_lengths_past_the_page_s_limits_are_field_errors():
    """Notes, contact notes and a bulk choice past the page's limits are
    refused with the option named, and nothing at the limits is."""
    admin_followup.check_lengths(
        notes="n" * 5000, contact_notes="c" * 2000, requests=range(250)
    )
    with pytest.raises(admin_followup.InvalidFollowup) as refused:
        admin_followup.check_lengths(
            notes="n" * 5001, contact_notes="c" * 2001, requests=range(251)
        )
    assert [problem["field"] for problem in refused.value.fields] == [
        "notes",
        "contact_notes",
        "request",
    ]
    assert all(problem["code"] == "invalid" for problem in refused.value.fields)


def handler_args(**values):
    """A ``followup update`` or ``bulk`` command line, as parsed."""
    return Namespace(
        **{
            "request_id": REQUEST,
            "expected_version": 1,
            "request_key": REVISION,
            "state": "in_progress",
            "outcome": None,
            "notes": None,
            "contact_channel": None,
            "contact_at": None,
            "contact_notes": None,
        }
        | values
    )


class Untouchable:
    """A runtime nothing may read: a usage error comes before any lookup."""

    def __getattr__(self, name):
        """Refuse every read."""
        raise AssertionError(f"read runtime.{name}")


@pytest.mark.parametrize(
    "values",
    [
        # An outcome goes only with Resolved, as the page shows it only then.
        {"outcome": "joined"},
        {"state": "closed_no_response", "outcome": "declined"},
        # A contact's instant or notes go only with its channel.
        {"contact_at": "2054-10-05T10:00:00-04:00"},
        {"contact_notes": "Left a message"},
        # Standard input carries one input only.
        {"notes": "-", "contact_channel": "phone", "contact_notes": "-"},
    ],
)
def test_options_without_effect_are_usage_errors(values):
    """An option the change would drop is refused, never silently ignored."""
    with pytest.raises(admin_cli.UsageError):
        admin_cli.followup_update(
            handler_args(**values),
            None,
            Untouchable(),
            {"caller": None, "stdin": io.BytesIO(b""), "stderr": io.StringIO()},
        )


def test_bulk_s_contact_options_without_a_channel_are_usage_errors(monkeypatch):
    """The bulk outcome's contact attempt follows the same rule, after the
    role check and before the prompt."""
    monkeypatch.setattr(admin_followup, "admit_followup", lambda *a, **k: None)
    monkeypatch.setattr(admin_cli, "confirm", None)
    for values in ({"contact_at": "2054-10-05T10:00:00Z"}, {"contact_notes": "x"}):
        with pytest.raises(admin_cli.UsageError):
            admin_cli.followup_bulk(
                handler_args(outcome="join:joined", request=[(REQUEST, 1)], **values),
                None,
                Untouchable(),
                {"caller": None},
            )


def test_bulk_refuses_a_choice_too_long_before_the_prompt(monkeypatch):
    """More than 250 requests, or contact notes past 2,000 characters, are
    field errors before anything is asked."""
    from uuid import uuid4

    monkeypatch.setattr(admin_followup, "admit_followup", lambda *a, **k: None)

    def unasked(*arguments, **options):
        """The prompt must not be reached."""
        raise AssertionError("prompted")

    monkeypatch.setattr(admin_cli, "confirm", unasked)
    for values, field in (
        ({"request": [(uuid4(), 1) for _ in range(251)]}, "request"),
        (
            {
                "request": [(REQUEST, 1)],
                "contact_channel": "phone",
                "contact_at": "2054-10-05T10:00:00Z",
                "contact_notes": "c" * 2001,
            },
            "contact_notes",
        ),
    ):
        with pytest.raises(admin_followup.InvalidFollowup) as refused:
            admin_cli.followup_bulk(
                handler_args(outcome="join:joined", **values),
                None,
                Untouchable(),
                {"caller": None},
            )
        assert [problem["field"] for problem in refused.value.fields] == [field]


# The members ``followup update`` and ``followup bulk`` may print.
CHANGE_ALLOWED = {
    "created",
    "request_key",
    "revision",
    "request",
    "id",
    "request_id",
    "expected_version",
    "state",
    "outcome",
    "contact_channel",
    "contact_at",
    "created_at",
    "version",
    "changed",
    "requests",
}


def change_documents():
    """An update and a bulk document, as the commands build them."""
    request = {"id": REQUEST, "version": 2, "state": "resolved", "outcome": "joined"}
    update = admin_followup.FollowupUpdate(
        created=True,
        request_key=REVISION,
        revision={
            "id": REVISION,
            "request_id": REQUEST,
            "expected_version": 1,
            "state": "resolved",
            "outcome": "joined",
            "contact_channel": "phone",
            "contact_at": WHEN,
            "created_at": WHEN,
        },
        request=request,
    ).to_document()
    bulk = admin_followup.FollowupBulk(
        created=False,
        request_key=REVISION,
        outcome="join:joined",
        changed=0,
        requests=[request],
    ).to_document()
    return update, bulk


def test_the_change_documents_are_their_golden_projections():
    """The update and bulk documents print ids, versions and states only,
    and the revision's members are exactly the projection's."""
    update, bulk = change_documents()
    request = {
        "id": str(REQUEST),
        "version": 2,
        "state": "resolved",
        "outcome": "joined",
    }
    assert update == {
        "created": True,
        "request_key": str(REVISION),
        "revision": {
            "id": str(REVISION),
            "request_id": str(REQUEST),
            "expected_version": 1,
            "state": "resolved",
            "outcome": "joined",
            "contact_channel": "phone",
            "contact_at": plain(WHEN),
            "created_at": plain(WHEN),
        },
        "request": request,
    }
    assert tuple(update["revision"]) == admin_followup.REVISION_FIELDS
    assert bulk == {
        "created": False,
        "request_key": str(REVISION),
        "outcome": "join:joined",
        "changed": 0,
        "requests": [request],
    }
    assert set(members(update)) | set(members(bulk)) == CHANGE_ALLOWED
    for name in CHANGE_ALLOWED:
        assert FORBIDDEN.search(name) is None, name
    assert not {"notes", "contact_notes"} & set(admin_followup.REVISION_FIELDS)


def test_the_refusal_documents_name_no_member():
    """A refusal's fields are the option, code and message; the other-kind
    refusal names the request by its id, never by its Member."""
    from parishkit.stewardship.workflows.followup import FollowupRefusal

    refusal = FollowupRefusal(
        "outcome_kind",
        outcome="joined",
        kind="join",
        action="leave",
        request_id=REQUEST,
    )
    [problem] = admin_followup.kind_refused(refusal).fields
    assert set(problem) == {"field", "code", "message"}
    assert problem["field"] == "request" and str(REQUEST) in problem["message"]
    for code in ("outcome_required", "other_needs_notes", "contact_incomplete"):
        for item in admin_followup.refused(FollowupRefusal(code)).fields:
            assert set(item) == {"field", "code", "message"}
            for value in PRIVATE:
                assert value not in item["message"]


@pytest.mark.parametrize(
    "argv",
    [
        ["followup", "list", "--search", "Ann"],
        ["followup", "list", "--state", "assigned"],
        ["followup", "list", "--page", "0"],
        ["followup", "list", "--size", "10"],
        ["followup", "show"],
        ["followup", "show", "not-a-uuid"],
        ["followup", "update", str(REQUEST), "--state", "resolved"],
        [
            *("followup", "update", str(REQUEST), "--expected-version", "1"),
            *("--state", "cancelled"),
        ],
        [
            *("followup", "update", str(REQUEST), "--expected-version", "1"),
            *("--state", "resolved", "--outcome", "duplicate"),
        ],
        ["followup", "bulk", "--outcome", "join:joined"],
        ["followup", "bulk", "--outcome", "join:left", "--request", f"{REQUEST}:1"],
        ["followup", "bulk", "--outcome", "join:joined", "--request", str(REQUEST)],
        ["followup", "bulk", "--outcome", "join:joined", "--request", f"{REQUEST}:0"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """No search, closed choices, a positive page, a request's id and version."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
