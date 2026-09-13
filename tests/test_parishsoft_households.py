"""Bounded household enumeration and full-search-equivalent Member reads."""

import pytest
from test_parishsoft_source import initialized, source

from parishkit.parishsoft_households import load_family_slice
from parishkit.parishsoft_pagination import IncompleteSourceCollection


def values():
    """Provider DTOs deliberately contain fields that must never become fallbacks."""
    return [
        {"familyDUID": 1},
        [
            {
                "memberDUID": 3,
                "familyDUID": 1,
                "middleName": "Middle",
                "birthdate": "1960-01-01",
                "ssn": "PRIVATE-SSN",
            }
        ],
        {"memberDUID": 3, "familyDUID": 1, "emailAddress": "example@example.org"},
    ]


def test_slice_reads_exact_ids_and_preserves_only_contact_fallbacks(tmp_path):
    """Fallback DTOs retain necessary census fields, never unrelated private data."""
    client = initialized(tmp_path, values())
    result = load_family_slice(client, family_id=1)
    assert set(result.members) == {3}
    assert result.contacts[3] == {
        "memberDUID": 3,
        "middleName": "Middle",
        "dateOfBirth": "1960-01-01",
    }
    assert "PRIVATE" not in str(result)
    assert [call[1].rsplit("/api/v2/", 1)[1] for call in client.session.calls[1:]] == [
        "families/1",
        "families/1/member/list",
        "members/3",
    ]


@pytest.mark.parametrize(
    "position,value",
    [
        (0, []),
        (0, {"familyDUID": 2}),
        (0, {"familyDUID": True}),
        (1, {}),
        (1, [None]),
        (1, [{"memberDUID": 3, "familyDUID": 2}]),
        (1, [{"memberDUID": True, "familyDUID": 1}]),
        (1, [{"memberDUID": 3, "familyDUID": 1}] * 2),
        (2, []),
        (2, {"memberDUID": 4, "familyDUID": 1}),
        (2, {"memberDUID": 3, "familyDUID": 2}),
    ],
)
def test_ambiguous_identity_or_enumeration_rejects_the_slice(tmp_path, position, value):
    """Wrong identities and duplicate Members cannot masquerade as a household."""
    rows = values()
    rows[position] = value
    with pytest.raises(IncompleteSourceCollection):
        load_family_slice(initialized(tmp_path, rows), family_id=1)


def test_membership_at_the_safety_ceiling_is_not_truncated(tmp_path):
    """An unpaginated ceiling is a refusal, never evidence of completeness."""
    client = initialized(tmp_path, values())
    with pytest.raises(IncompleteSourceCollection, match="incomplete"):
        load_family_slice(client, family_id=1, maximum_members=1)
    assert len(client.session.calls) == 3


def test_missing_household_endpoint_requires_full_refresh(tmp_path):
    """An unsupported or missing scoped endpoint cannot authorize a delta."""
    client = initialized(tmp_path, values())
    client.session.responses[0].status_code = 404
    with pytest.raises(IncompleteSourceCollection, match="unavailable"):
        load_family_slice(client, family_id=1)


def test_slice_never_skips_the_uncached_tenant_guard(tmp_path):
    """A configured tenant ID is not proof of the API key's actual organization."""
    client = source(tmp_path, values())
    with pytest.raises(IncompleteSourceCollection, match="validated"):
        load_family_slice(client, family_id=1)
    assert not client.session.calls
