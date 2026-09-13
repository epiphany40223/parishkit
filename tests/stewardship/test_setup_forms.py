"""Typed public wizard steps neither accept credentials nor activate configuration."""

from copy import deepcopy

import pytest

from parishkit.stewardship.accounts.setup_forms import (
    FORMS,
    form_values,
    initial_values,
    validate_values,
)

VALUES = {
    "parish": {
        "name": "Sample Parish",
        "website": "https://parish.example.org/",
        "timezone": "America/New_York",
        "phone": "+12125551234",
    },
    "access": {
        "staff_domains": ["example.org"],
        "ministry_domains": [],
        "staff_addresses": ["staff@example.net"],
        "ministry_addresses": ["chair@example.net"],
        "admin_addresses": ["another@example.net"],
    },
    "mail": {
        "delegated_email": "mail@example.org",
        "sender": "mail@example.org",
        "reply_to": "office@example.org",
    },
    "slack": {"enabled": False, "channel_id": ""},
    "testing": {"testing_recipient": "testing@example.org"},
}


@pytest.mark.parametrize("step", VALUES)
def test_public_form_round_trip_is_detached_and_canonical(step):
    """A validated draft contains exactly the same closed data on later visits."""
    values = deepcopy(VALUES[step])
    form = FORMS[step](initial_values(step, values))
    assert form.is_valid(), form.errors
    assert form_values(form) == values
    restored = validate_values(step, values)
    assert restored == values and restored is not values


@pytest.mark.parametrize(
    "step,fields",
    [
        ("parish", {"website": "https://user:password@parish.example.org"}),
        ("parish", {"website": "https://parish.example.org?token=secret"}),
        ("parish", {"timezone": "Mars/Nowhere"}),
        ("parish", {"phone": "123"}),
        ("access", {"staff_domains": ["gmail.com"]}),
        ("access", {"ministry_domains": ["*.example.org"]}),
        ("access", {"admin_addresses": ["not-an-address"]}),
        ("access", {"staff_domains": "example.org"}),
        ("mail", {"sender": "not-an-address"}),
        ("slack", {"enabled": True}),
        ("slack", {"channel_id": "C123"}),
        ("testing", {"testing_recipient": "no-at"}),
    ],
)
def test_invalid_or_inconsistent_public_settings_are_not_staged(step, fields):
    """Validation happens before SQL, with no submitted value in the exception."""
    with pytest.raises(ValueError):
        validate_values(step, VALUES[step] | fields)


@pytest.mark.parametrize("step", VALUES)
def test_extra_private_or_authority_fields_are_never_ignored(step):
    """A public JSON payload cannot carry a hidden credential to the database."""
    for field in ("api_key", "candidate", "credential_fingerprint", "base_digest"):
        with pytest.raises(ValueError):
            validate_values(step, VALUES[step] | {field: "synthetic-private"})


def test_identity_input_normalizes_case_duplicates_and_empty_lines():
    """Access lists are additive; repeated spelling does not duplicate grants."""
    form = FORMS["access"]({"staff_domains": "EXAMPLE.ORG\n\nexample.org"})
    assert form.is_valid()
    assert form_values(form)["staff_domains"] == ["example.org"]
    assert "admin_domains" not in form.fields


def test_identity_lists_and_unknown_steps_are_bounded():
    """Long lists do not generate an unbounded policy or draft response."""
    form = FORMS["access"](
        {"staff_addresses": "\n".join(f"staff{i}@example.org" for i in range(51))}
    )
    assert not form.is_valid()
    with pytest.raises(ValueError):
        form_values(form)
    for value in (None, "passwords"):
        with pytest.raises(ValueError):
            validate_values(value, {})
        with pytest.raises(ValueError):
            initial_values(value, {})
