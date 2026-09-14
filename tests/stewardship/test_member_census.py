"""Typed ordinary Member input, with explicit unknown and private static errors."""

from datetime import date

import pytest

from parishkit.stewardship.responses.comparison import (
    ValueKind,
    canonical_value,
    phone_record,
)
from parishkit.stewardship.responses.member_census import (
    GENDERS,
    MARITAL_STATUSES,
    MEMBER_FIELDS,
    InvalidMemberSource,
    InvalidMemberValue,
    browser_value,
    source_value,
    validate_member_value,
)
from parishkit.stewardship.responses.merge import KnownValue

FIELDS = {field.name: field for field in MEMBER_FIELDS}
TODAY = date(2026, 9, 13)
UNAVAILABLE = KnownValue(False)


def validate(name, value, source=UNAVAILABLE):
    """Keep tests deterministic and independent of host timezone and wall time."""
    return validate_member_value(FIELDS[name], value, source, today=TODAY)


@pytest.mark.parametrize(
    "name",
    [
        "prefix",
        "first_name",
        "middle_name",
        "last_name",
        "suffix",
        "nickname",
        "maiden_name",
        "language",
    ],
)
def test_unicode_text_preserves_meaning_and_trims(name):
    assert validate(name, "  Jose\u0301 O’Reilly-Smith  ") == "José O’Reilly-Smith"


@pytest.mark.parametrize("name", list(FIELDS))
@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        42,
        {},
        [],
        "secret\0",
        "secret\ud800",
        "a\nb",
        "a\rb",
        "a\tb",
        "x" * 255,
    ],
)
def test_private_malformed_input_only_yields_static_error(name, value):
    with pytest.raises(InvalidMemberValue) as caught:
        validate(name, value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "2026-09-14",
        "2025-02-29",
        "2024-2-29",
        "0000-01-01",
        "2026-09-13T00:00:00Z",
        "Unknown",
    ],
)
def test_birth_date_rejects_missing_impossible_future_and_ambiguous(value):
    with pytest.raises(InvalidMemberValue):
        validate("birth_date", value)


@pytest.mark.parametrize(
    "value", ["0001-01-01", "1900-01-01", "2024-02-29", "2026-09-13"]
)
def test_birth_date_is_civil_and_explicit_unknown_is_distinct(value):
    assert validate("birth_date", value) == value
    assert validate("birth_date", "unknown") is None


def test_unknown_presentation_preserves_availability_and_prior_attestation():
    field = FIELDS["birth_date"]
    assert browser_value(field, KnownValue(False)) == ""
    assert browser_value(field, KnownValue(True)) == ""
    assert browser_value(field, KnownValue(False), previous_unknown=True) == "unknown"
    assert (
        browser_value(field, KnownValue(True, "2000-01-01"), previous_unknown=True)
        == "2000-01-01"
    )


@pytest.mark.parametrize("value", GENDERS)
def test_gender_choices_are_explicit_and_canonical(value):
    assert validate("gender", value) == value
    assert source_value(FIELDS["gender"], " " + value.upper() + " ") == value


@pytest.mark.parametrize("value", MARITAL_STATUSES)
def test_marital_unknown_and_named_choices(value):
    assert validate("marital_status", value, KnownValue(True, "")) == value


@pytest.mark.parametrize("name", ["gender", "marital_status"])
def test_legacy_enum_can_only_be_retained_not_forged(name):
    assert validate(name, "Legacy", KnownValue(True, "Legacy")) == "Legacy"
    assert validate(name, " Legacy ", KnownValue(True, " Legacy ")) == "Legacy"
    with pytest.raises(InvalidMemberValue):
        validate(name, "Legacy")
    with pytest.raises(InvalidMemberValue):
        validate(name, "Different", KnownValue(True, "Legacy"))


@pytest.mark.parametrize("name", ["gender", "language", "first_name", "last_name"])
def test_missing_required_value_never_silently_becomes_unknown(name):
    with pytest.raises(InvalidMemberValue):
        validate(name, "")


@pytest.mark.parametrize("name", ["home_phone", "mobile_phone", "work_phone"])
@pytest.mark.parametrize(
    "display,normalized",
    [
        ("(202) 555-0123", "+12025550123"),
        ("1-202-555-0123", "+12025550123"),
        ("+44 20 8366 1177", "+442083661177"),
        ("+43 1 234", "+431234"),
        ("+33 1 42 68 53 00 ext. 123", "+33142685300;ext=123"),
        ("+1 (202) 555-0123 x2", "+12025550123;ext=2"),
    ],
)
def test_phone_display_and_normalized_number_are_both_retained(
    name, display, normalized
):
    result = validate(name, display)
    assert result == {"normalized": normalized, "display": display}
    assert source_value(FIELDS[name], display) == result
    assert browser_value(FIELDS[name], KnownValue(True, result)) == display
    assert canonical_value(ValueKind.PHONE, result) == canonical_value(
        ValueKind.PHONE, normalized
    )


@pytest.mark.parametrize(
    "value",
    [
        "not a phone",
        "555",
        "+0 123456789",
        "020 8366 1177",
        "+1 202 555 0123 x1234567890123",
        "+1 800 FLOWERS",
    ],
)
def test_invalid_new_phone_is_rejected(value):
    with pytest.raises(InvalidMemberValue):
        validate("home_phone", value)


def test_invalid_legacy_phone_remains_opaque_but_cannot_be_injected():
    record = phone_record("ask at office")
    assert record == {"normalized": None, "display": "ask at office"}
    assert validate("home_phone", "ask at office", KnownValue(True, record)) == record
    assert canonical_value(ValueKind.PHONE, record) == ("opaque", "ask at office")
    with pytest.raises(InvalidMemberValue):
        validate("home_phone", "ask at office")


def test_phone_formatting_is_not_a_change_but_extension_is():
    first = phone_record("2025550123")
    second = phone_record("+1 (202) 555-0123")
    assert canonical_value(ValueKind.PHONE, first) == canonical_value(
        ValueKind.PHONE, second
    )
    assert canonical_value(ValueKind.PHONE, first) != canonical_value(
        ValueKind.PHONE, phone_record("2025550123 x1")
    )


@pytest.mark.parametrize(
    "name", [field.name for field in MEMBER_FIELDS if not field.required]
)
def test_optional_blank_does_not_manufacture_unavailable_or_null_proposal(name):
    assert validate(name, "") is None
    assert validate(name, "", KnownValue(True)) is None
    assert validate(name, "", KnownValue(True, "")) == ""


def test_email_multiple_address_normalization_and_optional_clear():
    assert (
        validate("email", "One@EXAMPLE.ORG; two@example.org; one@example.org")
        == "one@example.org, two@example.org"
    )
    assert validate("email", "", KnownValue(True, "one@example.org")) == ""
    with pytest.raises(InvalidMemberValue):
        validate("email", "one@example.org;invalid")


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize(
    "name", ["first_name", "nickname", "language", "email", "home_phone"]
)
def test_member_line_separators_are_rejected_before_sql(name, separator):
    with pytest.raises(InvalidMemberValue):
        validate(name, "before" + separator + "after")


def test_email_length_is_bounded_after_join_expansion():
    addresses = [f"person{index:02d}@example-domain.org" for index in range(9)]
    raw = ",".join(addresses)
    assert len(raw) <= 254 < len(", ".join(addresses))
    with pytest.raises(InvalidMemberValue, match="fewer email addresses"):
        validate("email", raw)


@pytest.mark.parametrize("name", list(FIELDS))
def test_source_bounds_match_editable_answer_contract(name):
    with pytest.raises(InvalidMemberSource):
        source_value(FIELDS[name], "x" * (FIELDS[name].max_length + 1))


def test_invalid_source_civil_date_becomes_a_safe_source_error():
    with pytest.raises(InvalidMemberSource):
        source_value(FIELDS["birth_date"], "2025-02-29")
