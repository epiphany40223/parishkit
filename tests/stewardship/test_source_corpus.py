"""Canonical source normalization excludes unrelated PII and preserves availability."""

from datetime import date

import pytest

from parishkit.parishsoft import ParishSoftData
from parishkit.stewardship.source.canonical import (
    InvalidSourcePayload,
    canonical_payload,
)
from parishkit.stewardship.source.corpus import normalize_core

TODAY = date(2026, 9, 11)


def source():
    """Build the shared loader's plain collections without needing its derived links."""
    return ParishSoftData(
        organization_id=5,
        families={
            1: {
                "familyDUID": 1,
                "familyID": 11,
                "registeredOrganizationID": 5,
                "famGroupID": 7,
                "lastName": " Example ",
                "firstName": "Household",
                "primaryAddress1": "1 Example Street",
                "primaryCity": "Town",
                "primaryState": "KY",
                "primaryPostalCode": "40000",
                "primaryPublishAddress": True,
                "eMailAddress": "family@example.org",
            },
            2: {"familyDUID": 2, "registeredOrganizationID": 5, "lastName": "Empty"},
        },
        members={
            3: {
                "memberDUID": 3,
                "familyDUID": 1,
                "firstName": "Member",
                "lastName": "Example",
                "memberType": "Head",
                "memberStatus": "Active",
                "emailAddress": " VALID@EXAMPLE.ORG ; not-an-address ",
                "homePhone": " 202-555-0123 ",
                "family_PublishEMail": False,
                "family_PublishPhone": True,
                "birthdate": date(1960, 1, 1),
            },
        },
        family_groups={7: "Active"},
        family_workgroups={},
        family_workgroup_memberships={},
        member_workgroups={},
        member_workgroup_memberships={},
        member_contactinfos={3: {"memberDUID": 3, "middleName": "Middle"}},
        ministry_types={4: {"id": 4, "name": "Choir"}},
        ministry_type_memberships={
            4: {
                "membership": [
                    {
                        "memberId": 3,
                        "ministryRoleId": 2,
                        "ministryRoleName": "Chairperson",
                        "startDate": "2026-01-01",
                        "endDate": None,
                    },
                ]
            }
        },
        funds={
            9: {
                "fundId": 9,
                "name": "Offertory",
                "active": True,
                "fundStartDate": "2026-01-01T00:00:00",
            }
        },
        pledges={},
        contributions={},
    )


def test_corpus_has_separate_canonical_contacts_addresses_and_all_collections():
    """Contact edits need not copy otherwise unchanged Family/Member payloads."""
    corpus = normalize_core(source(), as_of=TODAY)
    assert set(corpus) == {
        "family",
        "member",
        "contact",
        "address",
        "ministry",
        "roster",
        "fund",
        "pledge",
        "contribution",
    }
    assert corpus["family"]["1"]["lastName"] == "Example"
    assert "eMailAddress" not in corpus["family"]["1"]
    assert "emailAddress" not in corpus["member"]["3"]
    assert corpus["member"]["3"]["family_key"] == "1"
    assert corpus["member"]["3"]["middleName"] == "Middle"
    assert corpus["member"]["3"]["birthdate"] == "1960-01-01"
    assert corpus["fund"]["9"]["fundStartDate"] == "2026-01-01"
    address = corpus["address"]["family:1:primary"]
    assert address["kind"] == "primary"
    assert "country" not in address["fields"]
    assert "registrationDate" not in corpus["family"]["1"]
    assert not any("home" in key or "mailing" in key for key in corpus["address"])
    for collection in corpus.values():
        for payload in collection.values():
            assert payload["schema_version"] == 1
            canonical_payload(payload)


def test_unneeded_provider_pii_cycles_and_pagination_never_enter_source_payloads():
    """Per-Family Member DTOs can contain SSNs; no blanket provider copy is safe."""
    data = source()
    expected = normalize_core(data, as_of=TODAY)
    data.members[3].update(
        ssn="PRIVATE-SSN-OMIT",
        recordCount=10000,
        rowNum=4,
        unrelated_sensitive_detail="PRIVATE-OMIT",
    )
    data.members[3]["py family"] = data.families[1]
    data.families[1]["py members"] = [data.members[3]]
    data.families[1]["dateModified"] = "arbitrary provider modification metadata"
    assert normalize_core(data, as_of=TODAY) == expected
    assert "PRIVATE" not in str(expected)


def test_eligibility_uses_active_shared_heads_not_family_email_or_publishability():
    """Private contact may receive its own campaign invitation, not public listings."""
    data = source()
    corpus = normalize_core(data, as_of=TODAY)
    family = corpus["family"]["1"]
    assert family["portal_eligible"] and family["email_eligible"]
    assert family["active_head_duids"] == [3]
    contact = corpus["contact"]["member:3"]
    assert contact["publish_email"] is False
    assert contact["emails"] == [
        {"value": "not-an-address", "valid": False},
        {"value": "valid@example.org", "valid": True},
    ]
    data.members[3]["memberType"] = "Child"
    assert normalize_core(data, as_of=TODAY)["family"]["1"]["email_eligible"] is False
    data.members[3].update(memberType="Head", memberStatus="Deceased")
    assert normalize_core(data, as_of=TODAY)["family"]["1"]["active_head_duids"] == []


def test_inactive_nonparishioner_and_empty_families_are_retained_but_not_eligible():
    """Their durable identities can be inactivated without erasing campaign history."""
    data = source()
    data.families[1]["registeredOrganizationID"] = 8
    corpus = normalize_core(data, as_of=TODAY)
    assert set(corpus["family"]) == {"1", "2"}
    assert corpus["family"]["1"]["active"] is True
    assert not corpus["family"]["1"]["portal_eligible"]
    assert not corpus["family"]["2"]["portal_eligible"]
    data.family_groups[7] = "Inactive"
    assert normalize_core(data, as_of=TODAY)["family"]["1"]["active"] is False


def test_valid_email_change_does_not_copy_unchanged_member_or_family_payload():
    """Normalized independent contacts are the only changed content-addressed entity."""
    data = source()
    before = normalize_core(data, as_of=TODAY)
    data.members[3]["emailAddress"] = "changed@example.org"
    after = normalize_core(data, as_of=TODAY)
    assert before["family"] == after["family"] and before["member"] == after["member"]
    assert before["contact"] != after["contact"]


def test_explicit_empty_search_value_does_not_fall_back_to_older_contact_email():
    """A source-cleared email cannot be resurrected from a different DTO's value."""
    data = source()
    data.members[3]["emailAddress"] = ""
    data.member_contactinfos[3]["emailAddress"] = "older@example.org"
    corpus = normalize_core(data, as_of=TODAY)
    assert not corpus["family"]["1"]["email_eligible"]
    assert corpus["contact"]["member:3"]["emails"] == []


def test_roster_uses_parish_civil_date_and_preserves_missing_ministry_activity_flag():
    """End dates are exclusive; catalog presence is distinct from an upstream flag."""
    data = source()
    data.ministry_type_memberships[4]["membership"][0]["endDate"] = TODAY.isoformat()
    corpus = normalize_core(data, as_of=TODAY)
    ministry = corpus["ministry"]["4"]
    assert ministry["catalog_present"] is True and "active" not in ministry
    roster = next(iter(corpus["roster"].values()))
    assert roster["member_key"] == "3" and roster["ministry_key"] == "4"
    assert roster["current"] is False
    assert (
        next(iter(normalize_core(data, as_of=date(2026, 9, 10))["roster"].values()))[
            "current"
        ]
        is True
    )


@pytest.mark.parametrize("reference", [0, True, "1", 99])
def test_member_family_reference_must_be_exact_and_retained(reference):
    """No dangling or coerced relationship may enter staging."""
    data = source()
    data.members[3]["familyDUID"] = reference
    with pytest.raises(InvalidSourcePayload):
        normalize_core(data, as_of=TODAY)


@pytest.mark.parametrize("value", [True, "5", -1])
def test_registration_metadata_is_not_coerced_into_parishioner_status(value):
    """Boolean or string aliases cannot become an eligible organization ID."""
    data = source()
    data.families[1]["registeredOrganizationID"] = value
    with pytest.raises(InvalidSourcePayload):
        normalize_core(data, as_of=TODAY)


def test_duplicate_or_dangling_roster_records_fail_before_staging():
    """A second logical participation cannot overwrite a first normalized identity."""
    data = source()
    rows = data.ministry_type_memberships[4]["membership"]
    rows.append(dict(rows[0]))
    with pytest.raises(InvalidSourcePayload, match="repeats"):
        normalize_core(data, as_of=TODAY)
    rows[:] = [{"memberId": 99}]
    with pytest.raises(InvalidSourcePayload, match="no retained Member"):
        normalize_core(data, as_of=TODAY)


@pytest.mark.parametrize(
    "field,value",
    [("emailAddress", True), ("homePhone", 123), ("birthdate", "PRIVATE-INVALID-DATE")],
)
def test_invalid_typed_source_value_has_no_private_diagnostic(field, value):
    """Malformed fields do not get stringified into apparently valid source data."""
    data = source()
    data.members[3][field] = value
    with pytest.raises(InvalidSourcePayload) as error:
        normalize_core(data, as_of=TODAY)
    assert "PRIVATE" not in str(error.value)


def test_unscoped_giving_is_not_silently_included_or_discarded():
    """The core loader cannot accidentally enable all-history giving."""
    data = source()
    data.pledges[1] = {"amount": "100.00"}
    with pytest.raises(InvalidSourcePayload, match="Unscoped giving"):
        normalize_core(data, as_of=TODAY)


def test_blank_contact_and_address_remain_distinct_from_unavailable_fields():
    """Known empty fields must not become a fabricated absence of source coverage."""
    data = source()
    data.members[3].update(emailAddress=None, homePhone="")
    for name in ("primaryAddress1", "primaryCity", "primaryState", "primaryPostalCode"):
        data.families[1][name] = None
    corpus = normalize_core(data, as_of=TODAY)
    contact = corpus["contact"]["member:3"]
    assert contact["available"] == ["email", "home"]
    assert contact["emails"] == [] and contact["phones"] == {}
    address = corpus["address"]["family:1:primary"]
    assert address["fields"]["primaryAddress1"] is None
    assert "primaryAddress2" not in address["fields"]
    assert "family:2" not in corpus["contact"]
    assert "family:2:primary" not in corpus["address"]


def test_email_aliases_are_deduplicated_after_normalization():
    """Different spellings cannot become repeated recipients in one contact record."""
    data = source()
    data.members[3]["emailAddress"] = "SAME@EXAMPLE.ORG; same@example.org"
    contact = normalize_core(data, as_of=TODAY)["contact"]["member:3"]
    assert contact["emails"] == [{"value": "same@example.org", "valid": True}]


@pytest.mark.parametrize(
    "field,value",
    [
        ("firstName", 2),
        ("memberStatus", False),
        ("maritalStatusID", "1"),
        ("family_PublishEMail", "false"),
        ("family_PublishPhone", 1),
    ],
)
def test_typed_provider_fields_cannot_change_meaning_through_coercion(field, value):
    """Names, classifications and publication flags have closed provider types."""
    data = source()
    data.members[3][field] = value
    with pytest.raises(InvalidSourcePayload):
        normalize_core(data, as_of=TODAY)


@pytest.mark.parametrize("value", [[], {"membership": None}, {"membership": [None]}])
def test_malformed_roster_shape_has_a_static_validation_error(value):
    """An unexpected DTO shape never leaks provider content in an exception."""
    data = source()
    data.ministry_type_memberships[4] = value
    with pytest.raises(InvalidSourcePayload):
        normalize_core(data, as_of=TODAY)


def test_invalid_group_and_fallback_types_do_not_affect_eligibility():
    """Secondary lookup DTOs receive the same validation as primary search fields."""
    data = source()
    data.family_groups[7] = 123
    with pytest.raises(InvalidSourcePayload, match="group"):
        normalize_core(data, as_of=TODAY)
    data.family_groups[7] = "Active"
    data.member_contactinfos[3]["middleName"] = False
    with pytest.raises(InvalidSourcePayload, match="type"):
        normalize_core(data, as_of=TODAY)


def test_missing_family_group_lookup_is_not_treated_as_an_active_group():
    """A dropped lookup cannot silently make an inactive Family eligible."""
    data = source()
    data.family_groups.clear()
    with pytest.raises(InvalidSourcePayload, match="group reference"):
        normalize_core(data, as_of=TODAY)
