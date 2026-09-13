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
    assert seen == expected.keys()


@pytest.mark.parametrize(
    ("entity", "field", "handling"),
    [
        ("family", "home_address", Handling.API),
        ("family", "mailing_address", Handling.API),
        ("family", "email_opt_out", Handling.MANUAL),
        ("family", "annual_pledge", Handling.REPORT_ONLY),
        ("family", "frequency", Handling.REPORT_ONLY),
        ("family", "share_methods", Handling.REPORT_ONLY),
        ("member", "prefix", Handling.MANUAL),
        ("member", "suffix", Handling.MANUAL),
        ("member", "marital_status", Handling.MANUAL),
        ("member", "moved_household", Handling.MANUAL),
        ("member", "deceased_status", Handling.MANUAL),
        ("member", "death_date", Handling.API),
        ("member", "ministry_join", Handling.REPORT_ONLY),
        ("member", "ministry_leave", Handling.REPORT_ONLY),
        ("proposed_member", "new_member", Handling.MANUAL),
        ("proposed_member", "ministry_join", Handling.REPORT_ONLY),
    ],
)
def test_specified_semantic_routes(entity, field, handling):
    """Death dates do not imply deceased status, and roster requests are not writes."""
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
