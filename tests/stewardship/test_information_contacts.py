"""How an Additional information item lists one owner's contact (#1595)."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.information_contacts import (
    _details,
    family_contacts,
)


def test_invalid_addresses_are_marked_and_phones_follow_the_shown_order():
    """Source text kept for correction is marked; empty values are left out."""
    details = _details(
        "Family record",
        {
            "emails": [
                {"value": "bad-text", "valid": False},
                {"value": "a@example.org", "valid": True},
                {"value": "", "valid": False},
            ],
            "phones": {"work": "555-0102", "home": "555-0101", "mobile": ""},
        },
    )
    assert details == {
        "label": "Family record",
        "emails": [
            "bad-text (not a valid address; fix in ParishSoft)",
            "a@example.org",
        ],
        "phones": [("Home", "555-0101"), ("Work", "555-0102")],
    }
    assert _details("Head", None) == {"label": "Head", "emails": [], "phones": []}


@pytest.mark.parametrize("roles", [frozenset({"ministry_leader"}), frozenset()])
def test_readers_without_staff_follow_up_are_refused_before_any_read(roles):
    """Leaders and role-less readers never reach the query (no database here)."""
    reader = Principal(uuid4(), roles, frozenset({4}))
    with pytest.raises(PermissionError):
        family_contacts(reader, uuid4(), 1)
