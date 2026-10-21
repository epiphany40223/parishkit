"""Proposal routes are backed by captured public shapes, not guessed endpoint names."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from parishkit.stewardship.responses.capabilities import (
    FAMILY_CAPABILITIES,
    MEMBER_CAPABILITIES,
    Handling,
    capability,
    clears_value,
    proposal_handling,
)


def test_api_routes_match_captured_public_contact_shapes():
    """Every declared provider field exists in its exact versioned request DTO."""
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/parishsoft_contact_capabilities.json"
        ).read_text()
    )
    expected = {
        "FamilyContactUpdateRequestModel": "/api/v2/families/{familyId}/contact",
        "MemberContactUpdateRequestModel": "/api/v2/members/{memberId}/contact",
    }
    seen = set()
    # Family addresses are By hand (#1596): only the Member PUT is used.
    for item in [*FAMILY_CAPABILITIES.values(), *MEMBER_CAPABILITIES.values()]:
        if item.handling is not Handling.API:
            assert item.request_schema is None and item.provider_fields == ()
            continue
        schema = fixture["schemas"][item.request_schema]
        assert schema["additionalProperties"] is False
        assert item.provider_fields
        assert set(item.provider_fields) <= schema["properties"].keys()
        path = fixture["paths"][expected[item.request_schema]]
        assert path["put"]["schema"]["$ref"] == (
            "#/components/schemas/" + item.request_schema
        )
        seen.add(item.request_schema)
    assert seen == {"MemberContactUpdateRequestModel"}


@pytest.mark.parametrize(
    ("entity", "field", "handling"),
    [
        ("family", "home_address", Handling.MANUAL),
        ("family", "mailing_address", Handling.MANUAL),
        ("family", "email_opt_out", Handling.MANUAL),
        ("family", "annual_pledge", Handling.REPORT_ONLY),
        ("family", "frequency", Handling.REPORT_ONLY),
        ("family", "share_methods", Handling.REPORT_ONLY),
        ("member", "prefix", Handling.MANUAL),
        ("member", "suffix", Handling.MANUAL),
        ("member", "gender", Handling.MANUAL),
        ("member", "marital_status", Handling.MANUAL),
        ("member", "moved_household", Handling.MANUAL),
        ("member", "deceased_status", Handling.MANUAL),
        ("member", "death_date", Handling.MANUAL),
        ("member", "first_name", Handling.API),
        ("member", "birth_date", Handling.API),
        ("member", "email", Handling.API),
        ("member", "home_phone", Handling.API),
        ("member", "language", Handling.API),
        ("member", "ministry_join", Handling.REPORT_ONLY),
        ("member", "ministry_leave", Handling.REPORT_ONLY),
        ("proposed_member", "new_member", Handling.MANUAL),
        ("proposed_member", "ministry_join", Handling.REPORT_ONLY),
    ],
)
def test_specified_semantic_routes(entity, field, handling):
    """The live API test's registry (#1596): a death date, which leaves the
    Member Active in ParishSoft, and Family addresses, which cannot be read
    back, are By hand, and so is gender, whose PUT never changes the Member
    (#1603); roster requests are not writes."""
    assert capability(entity, field).handling is handling


@pytest.mark.parametrize(
    ("entity", "field"),
    [
        ("unknown", "name"),
        ("member", "secret"),
        ("proposed_member", "first_name"),
        ("family", "death_date"),
        (None, "first_name"),
        ("member", {}),
    ],
)
def test_unknown_capabilities_fail_closed_without_echoing_input(entity, field):
    """Unknown field names cannot silently become publication authority."""
    with pytest.raises(
        ValueError, match="^The proposal field has no registered capability.$"
    ):
        capability(entity, field)


def test_registry_cannot_be_mutated_by_a_caller():
    """All callers consume the same reviewed classification, not request-local edits."""
    with pytest.raises(TypeError):
        MEMBER_CAPABILITIES["death_date"] = capability("member", "deceased_status")
    with pytest.raises(FrozenInstanceError):
        capability("member", "death_date").handling = Handling.MANUAL


@pytest.mark.parametrize(
    ("field", "submitted", "handling"),
    [
        ("first_name", "Anne", Handling.API),
        ("middle_name", "", Handling.MANUAL),
        ("birth_date", "1970-01-02", Handling.API),
        # A birth date of Unknown over a recorded date.
        ("birth_date", None, Handling.MANUAL),
        ("email", "", Handling.MANUAL),
        ("email", "a@example.org", Handling.API),
        ("home_phone", "", Handling.MANUAL),
        (
            "mobile_phone",
            {"normalized": "+15555550100", "display": "(555) 555-0100"},
            Handling.API,
        ),
        # Already manual whatever the value.
        ("death_date", "2026-01-02", Handling.MANUAL),
        ("gender", "Female", Handling.MANUAL),
        ("prefix", "", Handling.MANUAL),
    ],
)
def test_an_answer_that_clears_a_value_is_by_hand(field, submitted, handling):
    """The API cannot clear a field (null ignored, "" refused), so clearing
    answers to API-writable fields are By hand (#1596)."""
    assert proposal_handling("member", field, submitted) is handling


def test_clears_value_reads_only_the_stored_empty_forms():
    """Only the form's stored empty values clear; False or 0 are values."""
    assert clears_value(None) and clears_value("")
    assert not any(clears_value(value) for value in (" x", False, 0, {}, []))


def test_report_only_handling_never_becomes_manual():
    """A report-only change stays report-only even with an empty answer."""
    assert proposal_handling("family", "share_methods", None) is Handling.REPORT_ONLY
