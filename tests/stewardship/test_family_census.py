"""Country-aware household values, unknown source truth and complete answers."""

from copy import deepcopy

import pytest

from parishkit.stewardship.responses.census import (
    ADDRESS_LIMITS,
    InvalidHousehold,
    blank_address,
    country_choices,
    us_regions,
    validate_address,
    validate_household,
)
from parishkit.stewardship.responses.merge import KnownValue

from .census_factory import address, household, sources


def test_unicode_expansion_cannot_exceed_normalized_address_limit():
    """NFC may expand a code point, so enforce the persisted value's bound too."""
    with pytest.raises(InvalidHousehold):
        validate_address(address(line1="\u0344" * 200))


def test_country_and_region_choices_are_stable_immutable_public_data():
    """Country names are ordered reproducibly and foreign states are not US regions."""
    choices = country_choices()
    assert type(choices) is tuple
    assert len(choices) >= 249
    assert choices == tuple(
        sorted(choices, key=lambda item: (item[1].casefold(), item[0]))
    )
    assert dict(choices)["US"] == "United States"
    assert dict(choices)["FR"] == "France"
    assert len(set(code for code, _ in choices)) == len(choices)
    assert type(us_regions()) is frozenset
    assert {"KY", "DC", "PR", "AA", "AE", "AP"} <= us_regions()
    assert "ON" not in us_regions()


def test_address_text_normalization_preserves_meaningful_display():
    """NFC and outer trim do not uppercase street names or remove punctuation."""
    raw = address(line1="  Cafe\u0301 d'Example  ", country="us", region="ky")
    original = deepcopy(raw)
    normalized = validate_address(raw)
    assert normalized == address(line1="Caf\u00e9 d'Example")
    assert raw == original


@pytest.mark.parametrize("postal", ["40223", "40223-1234"])
@pytest.mark.parametrize("region", ["KY", "DC", "PR", "AA", "AE", "AP"])
def test_us_addresses_accept_postal_and_military_regions(postal, region):
    """Postal syntax is checked without claiming an address exists or is deliverable."""
    raw = address(postal_code=postal, region=region)
    assert validate_address(raw) == raw


@pytest.mark.parametrize(
    "country,region,postal",
    [
        ("FR", "", "75004"),
        ("GB", "Greater London", "SW1A 1AA"),
        ("HK", "", ""),
        ("CA", "Ontario", "K1A 0B1"),
        ("DE", "Berlin", "10115"),
    ],
)
def test_international_addresses_do_not_require_us_state_or_zip(
    country, region, postal
):
    """International postal codes/regions are bounded text, and may be absent."""
    raw = address(country=country, region=region, postal_code=postal)
    assert validate_address(raw) == raw


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"line1": ""}, "line1"),
        ({"city": ""}, "city"),
        ({"country": ""}, "country"),
        ({"country": "ZZ"}, "country"),
        ({"country": "USA"}, "country"),
        ({"region": ""}, "region"),
        ({"region": "ON"}, "region"),
        ({"postal_code": ""}, "postal_code"),
        ({"postal_code": "4022"}, "postal_code"),
        ({"postal_code": "40223 1234"}, "postal_code"),
        ({"postal_code": "40223-123"}, "postal_code"),
    ],
)
def test_partial_and_invalid_addresses_return_only_known_field_errors(changes, field):
    """No malformed country/region or private address is echoed in an exception."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_address(address(**changes))
    assert field in denied.value.fields
    assert "Sample" not in str(denied.value)


@pytest.mark.parametrize("field", tuple(ADDRESS_LIMITS))
@pytest.mark.parametrize(
    "value", [None, True, 3, [], {}, "a\nb", "a\tb", "a\u2028b", "a\0b"]
)
def test_address_components_reject_wrong_types_and_multiline_input(field, value):
    """All components are explicit bounded single-line strings, even optional ones."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_address(address(**{field: value}))
    assert field in denied.value.fields


@pytest.mark.parametrize("field", tuple(ADDRESS_LIMITS))
def test_address_components_enforce_raw_bounds(field):
    """Whitespace cannot bypass bounded request fields before normalization."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_address(address(**{field: " " * (ADDRESS_LIMITS[field] + 1)}))
    assert field in denied.value.fields


@pytest.mark.parametrize("payload", [None, [], {}, {"line1": "123 Sample Street"}])
def test_address_requires_complete_explicit_shape(payload):
    """Missing components never silently become a submitted blank address."""
    with pytest.raises(InvalidHousehold):
        validate_address(payload)


def test_no_change_unavailable_household_manufactures_no_boolean_or_address():
    """An untouched unknown is not False, nor a fabricated all-empty source object."""
    payload = household()
    original = deepcopy(payload)
    assert validate_household(payload, sources()) == {
        "home_address": None,
        "mailing_address": None,
        "mailing_same_as_home": False,
        "email_opt_out": None,
    }
    assert payload == original
    assert blank_address() is not blank_address()


@pytest.mark.parametrize("value", [True, False])
def test_explicit_opt_out_choice_is_retained(value):
    """Explicit boolean choices stay distinct from untouched unavailable source."""
    assert (
        validate_household(household(email_opt_out=value), sources())["email_opt_out"]
        is value
    )


def test_known_null_opt_out_and_explicit_address_clear_are_not_false_defaults():
    """Null preserves a known-null preference and can explicitly clear an address."""
    result = validate_household(
        household(),
        sources(
            email_opt_out=KnownValue(True, None),
            home_address=KnownValue(True, address()),
        ),
    )
    assert result["email_opt_out"] is None
    assert result["home_address"] is None


def test_household_collects_both_addresses_errors_without_echoing_input():
    """One response identifies every malformed address rather than dropping a side."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_household(
            household(
                home_address=address(postal_code="private invalid zip"),
                mailing_address=address(country="ZZ"),
            ),
            sources(),
        )
    assert set(denied.value.fields) == {
        "home_address.postal_code",
        "mailing_address.country",
    }
    assert "private" not in str(denied.value.fields)


@pytest.mark.parametrize("source", [KnownValue(True, False), KnownValue(True, True)])
def test_known_opt_out_cannot_be_replaced_by_unknown(source):
    """A client cannot omit a known boolean by claiming its availability is missing."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_household(household(), sources(email_opt_out=source))
    assert "email_opt_out" in denied.value.fields


@pytest.mark.parametrize("value", [0, 1, "false", "true", {}, []])
def test_opt_out_does_not_coerce_other_types(value):
    """String/number truthiness never authorizes a parish email preference."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_household(household(email_opt_out=value), sources())
    assert "email_opt_out" in denied.value.fields


def test_same_as_home_requires_complete_matching_addresses():
    """The server validates both submitted values rather than silently copying one."""
    payload = household(
        home_address=address(),
        mailing_address=address(line1="123 SAMPLE STREET"),
        mailing_same_as_home=True,
    )
    result = validate_household(payload, sources())
    assert result["mailing_same_as_home"] is True
    assert result["mailing_address"]["line1"] == "123 SAMPLE STREET"
    for mailing in (blank_address(), address(line1="456 Different Street")):
        with pytest.raises(InvalidHousehold) as denied:
            validate_household(payload | {"mailing_address": mailing}, sources())
        assert "mailing_same_as_home" in denied.value.fields


def test_same_as_home_cannot_mean_two_unavailable_addresses():
    """Selecting this convenience requires an actual home address to share."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_household(household(mailing_same_as_home=True), sources())
    assert "mailing_same_as_home" in denied.value.fields


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_same_as_home_choice_is_an_exact_boolean(value):
    """A missing or coerced convenience flag is a malformed complete answer."""
    with pytest.raises(InvalidHousehold) as denied:
        validate_household(household(mailing_same_as_home=value), sources())
    assert "mailing_same_as_home" in denied.value.fields


def test_household_shapes_and_source_owner_are_closed():
    """Extra/omitted answers and browser-provided availability are not accepted."""
    with pytest.raises(InvalidHousehold):
        validate_household(household(extra="private"), sources())
    with pytest.raises(InvalidHousehold):
        validate_household({}, sources())
    with pytest.raises(TypeError):
        validate_household(household(), {})
    with pytest.raises(TypeError):
        validate_household(household(), sources(email_opt_out=False))
