"""The dry-run publication planner is a pure value that can never write."""

import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.parishsoft import ParishSoftClient
from parishkit.parishsoft_transport import BoundedSourceSession
from parishkit.stewardship.responses import publication
from parishkit.stewardship.responses.comparison import phone_record
from parishkit.stewardship.responses.publication import (
    MEMBER_CONTACT_FIELDS,
    Outcome,
    dry_run_summary,
    payload_digest,
    plan_entity,
)

FIXTURE = Path(__file__).parent / "fixtures/parishsoft_contact_capabilities.json"


def proposal(field, submitted, baseline, **overrides):
    """A ProposedChange stand-in: approved, API-writable and pending by default."""
    values = {
        "pk": field,
        "field": field,
        "handling": "api",
        "decision": "approved",
        "execution": "pending",
        "baseline_available": True,
        "baseline_value": baseline,
        "submitted_value": submitted,
        "admin_value_set": False,
        "admin_value": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def payload(**values):
    """A complete fresh Member contact read, blank except for ``values``."""
    record = dict.fromkeys(MEMBER_CONTACT_FIELDS)
    record.update(values)
    return record


def outcomes(plan):
    """Map each field of an entity plan to its outcome."""
    return {item.field: item.outcome for item in plan.fields}


def test_member_fields_match_the_recorded_request_schema():
    """A payload holds exactly the PUT schema's fields, no more and no fewer."""
    schema = json.loads(FIXTURE.read_text())["schemas"]
    assert set(MEMBER_CONTACT_FIELDS) == set(
        schema["MemberContactUpdateRequestModel"]["properties"]
    )
    assert len(MEMBER_CONTACT_FIELDS) == len(set(MEMBER_CONTACT_FIELDS))


# Everything the planner may import. Adding an import here is a deliberate,
# reviewed act; a client, transport or HTTP library must never appear.
ALLOWED_IMPORTS = {
    ("hashlib", None),
    ("json", None),
    ("collections", "Counter"),
    ("dataclasses", "dataclass"),
    ("datetime", "date"),
    ("datetime", "datetime"),
    ("enum", "StrEnum"),
    ("parishkit.parishsoft", "split_email_addresses"),
    ("capabilities", "Handling"),
    ("capabilities", "capability"),
    ("capabilities", "clears_value"),
    ("comparison", "ComparisonValueError"),
    ("comparison", "ValueKind"),
    ("member_census", "MEMBER_FIELDS"),
    ("member_census", "InvalidMemberSource"),
    ("member_census", "source_value"),
    ("member_requests", "REQUEST_FIELDS"),
    ("merge", "KnownValue"),
    ("merge", "MergeState"),
    ("merge", "PriorChange"),
    ("merge", "merge_value"),
}


def test_planner_cannot_reach_parishsoft():
    """No client, transport, HTTP library or PUT exists for the planner to call.

    The planner's imports are pinned to an allowlist (so no module alias can
    smuggle in a client), it touches no request-style attribute, and neither
    ParishSoft client offers a PUT.
    """
    tree = ast.parse(inspect.getsource(publication))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {(alias.name, None) for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports |= {(node.module, alias.name) for alias in node.names}
    assert imports == ALLOWED_IMPORTS
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not attributes & {"put", "post", "patch", "request", "session", "urlopen"}
    for owner in (ParishSoftClient, BoundedSourceSession):
        assert not any(name.lower().startswith("put") for name in dir(owner))


def test_approved_change_is_merged_into_the_full_payload():
    """Only approved writes change the payload; every other field is as read."""
    current = payload(
        firstName="Pat",
        lastName="Doe",
        cellPhone="(502) 555-0100",
        emailAddress="old@example.org",
        fax="555",
        dateOfBirth="1980-01-02T00:00:00",
    )
    rows = [
        proposal("first_name", "Patricia", "Pat"),
        proposal(
            "mobile_phone",
            phone_record("502-555-0199"),
            phone_record("(502) 555-0100"),
        ),
        proposal("email", "a@example.org, b@example.org", "old@example.org", pk="mail"),
        proposal("last_name", "Smith", "Doe", decision="unreviewed"),
        proposal("middle_name", "Quinn", None, decision="ignored"),
        proposal("prefix", "Dr.", "", handling="manual"),
    ]
    plan = plan_entity("member", 7, rows, current)
    assert outcomes(plan) == {
        "first_name": Outcome.WRITE,
        "mobile_phone": Outcome.WRITE,
        "email": Outcome.WRITE,
        "last_name": Outcome.NOT_APPROVED,
        "middle_name": Outcome.NOT_APPROVED,
        "prefix": Outcome.BY_HAND,
    }
    assert not plan.blocked
    assert plan.entity_key == "7"
    assert plan.payload == {
        **current,
        "firstName": "Patricia",
        "cellPhone": "502-555-0199",
        "emailAddress": "a@example.org; b@example.org",
    }
    assert plan.current_digest == payload_digest(current)
    assert plan.planned_digest == payload_digest(plan.payload)
    assert plan.current_digest != plan.planned_digest
    # The read payload itself is never modified.
    assert current["firstName"] == "Pat"


def test_admin_edit_is_published_instead_of_the_family_value():
    """An Administrator's edited value is the one planned."""
    row = proposal("nickname", "Patty", None, admin_value_set=True, admin_value="P")
    plan = plan_entity("member", 1, [row], payload())
    assert plan.payload["nickName"] == "P"


def test_matching_value_is_already_in_parishsoft():
    """A ParishSoft value that already matches plans no write and no payload."""
    current = payload(emailAddress="B@example.org; a@example.org")
    plan = plan_entity(
        "member",
        1,
        [proposal("email", "a@example.org, b@example.org", None)],
        current,
    )
    assert outcomes(plan) == {"email": Outcome.ALREADY_IN_PARISHSOFT}
    assert plan.payload is None and not plan.blocked


@pytest.mark.parametrize(
    ("row", "current", "expected"),
    [
        # ParishSoft changed since the Family saw it.
        (proposal("first_name", "Patricia", "Pat"), "Patrick", Outcome.CONFLICT),
        # Reconciliation already marked the row a conflict.
        (
            proposal("first_name", "Patricia", "Pat", execution="conflict"),
            "Pat",
            Outcome.CONFLICT,
        ),
        # The fresh value cannot be read in census form.
        (proposal("first_name", "Patricia", "Pat"), "Pat\nrick", Outcome.CANNOT_CHECK),
    ],
)
def test_conflicts_block_the_whole_entity(row, current, expected):
    """One blocking field stops every write to that Member, including clean ones."""
    plan = plan_entity(
        "member",
        1,
        [row, proposal("nickname", "Patty", None)],
        payload(firstName=current),
    )
    assert outcomes(plan) == {"first_name": expected, "nickname": Outcome.WRITE}
    assert plan.blocked
    assert plan.payload is None and plan.planned_digest is None


@pytest.mark.parametrize(
    "current", [None, {"firstName": "Pat"}, {**payload(), "extra": 1}]
)
def test_incomplete_read_is_never_planned(current):
    """A missing or partial read blocks the entity so no field can be cleared."""
    plan = plan_entity("member", 1, [proposal("nickname", "Patty", None)], current)
    assert outcomes(plan) == {"nickname": Outcome.CANNOT_CHECK}
    assert plan.blocked and plan.payload is None


def test_dates_compare_as_civil_dates():
    """A provider date-time and an ISO date for the same day are equal."""
    plan = plan_entity(
        "member",
        1,
        [proposal("birth_date", "1970-05-01", None)],
        payload(dateOfBirth="1970-05-01T00:00:00"),
    )
    assert outcomes(plan) == {"birth_date": Outcome.ALREADY_IN_PARISHSOFT}


def test_a_death_date_is_never_planned_even_if_stored_as_automatic():
    """Defence in depth (#1596): writing dateOfDeath leaves the Member Active,
    so a row stored as api before migration 0113 is still By hand."""
    plan = plan_entity(
        "member", 1, [proposal("death_date", "2026-05-01", None)], payload()
    )
    assert outcomes(plan) == {"death_date": Outcome.BY_HAND}
    assert plan.payload is None and not plan.blocked


@pytest.mark.parametrize(
    ("field", "row"),
    [
        ("birth_date", proposal("birth_date", None, "1970-05-01")),
        ("email", proposal("email", "", "a@example.org")),
        ("home_phone", proposal("home_phone", "", None)),
        # An Administrator's edit is what would be written, so it decides.
        (
            "nickname",
            proposal("nickname", "Pat", None, admin_value_set=True, admin_value=""),
        ),
    ],
)
def test_a_value_that_would_clear_a_field_is_never_planned(field, row):
    """The API cannot clear a field (#1596): a blank value to write is By
    hand, whatever handling the row was stored with."""
    plan = plan_entity("member", 1, [row], payload(dateOfBirth="1970-05-01"))
    assert outcomes(plan) == {field: Outcome.BY_HAND}
    assert plan.payload is None and not plan.blocked


def test_family_and_new_member_changes_are_by_hand():
    """Family addresses wait for a verified address read; new Members are manual."""
    address = proposal("home_address", {"line1": "x"}, None)
    plan = plan_entity("family", 9, [address], payload())
    assert outcomes(plan) == {"home_address": Outcome.BY_HAND}
    assert plan.payload is None and not plan.blocked
    new = proposal("new_member", {}, None, handling="manual")
    assert outcomes(plan_entity("proposed_member", "u", [new])) == {
        "new_member": Outcome.BY_HAND
    }


def test_rows_no_publication_may_act_on_are_left_out():
    """Queued and terminal rows are not planned; duplicate open fields fail."""
    rows = [
        proposal("nickname", "A", None, execution=state)
        for state in ("queued", "published", "superseded", "cancelled")
    ]
    assert plan_entity("member", 1, rows, payload()).fields == ()
    with pytest.raises(ValueError):
        plan_entity(
            "member",
            1,
            [proposal("nickname", "A", None), proposal("nickname", "B", None)],
            payload(),
        )


def test_dry_run_summary_holds_counts_only():
    """The summary reports counts by outcome and field, never values."""
    plans = [
        plan_entity("member", 1, [proposal("nickname", "Patty", None)], payload()),
        plan_entity(
            "member",
            2,
            [proposal("first_name", "Patricia", "Pat")],
            payload(firstName="Patrick"),
        ),
    ]
    summary = dry_run_summary(plans)
    assert summary == {
        "entities": 2,
        "entities_to_write": 1,
        "entities_blocked": 1,
        "outcomes": {"write": 1, "conflict": 1},
        "fields": {"nickname:write": 1, "first_name:conflict": 1},
    }
    assert "Patty" not in json.dumps(summary)
