"""An Administrator's edited census value, without a database (#528, ADM-09.04).

The service and the SQL guard are tested in
``database/test_census_edit_postgresql.py``; these tests cover the value
rules, the row's Edit value control and the page's form parsing.
"""

from datetime import date

import pytest

from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.responses.census_review import (
    InvalidEdit,
    edit_limit,
    edited_value,
)

from .test_census_changes import row

TODAY = date(2054, 10, 6)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("first_name", "  Pat ", "Pat"),
        ("birth_date", "2000-02-29", "2000-02-29"),
        ("email", "B@Example.org; a@example.org", "a@example.org, b@example.org"),
        (
            "mobile_phone",
            "(317) 555-0199",
            {"normalized": "+13175550199", "display": "(317) 555-0199"},
        ),
    ],
)
def test_an_edit_is_read_as_the_family_form_reads_its_answer(field, raw, expected):
    """Stored in the shape of the Family's own answer for that field."""
    assert edited_value(field, raw, today=TODAY) == expected


@pytest.mark.parametrize(
    ("field", "raw", "message"),
    [
        ("first_name", "", "Enter the value to use"),
        ("email", "not-an-address", "separated by commas or semicolons"),
        ("middle_name", "  ", "Enter the value to use"),
        ("first_name", "x" * 31, "at most 30 characters"),
        ("email", f"{'a' * 70}@example.org; {'b' * 70}@example.org", "at most 150"),
        ("birth_date", "2054-10-07", "no later than today"),
        ("home_phone", "12", "complete phone number"),
    ],
)
def test_a_value_publication_would_refuse_is_refused(field, raw, message):
    """Each refusal says why, in static words that never echo the value."""
    with pytest.raises(InvalidEdit, match=message) as refused:
        edited_value(field, raw, today=TODAY)
    assert raw.strip() == "" or raw not in str(refused.value)


@pytest.mark.parametrize(
    "field",
    ["prefix", "suffix", "gender", "marital_status", "home_address", "death_date"],
)
def test_a_by_hand_field_takes_no_edit(field):
    """Only Automatic Member fields may be edited; a death date is By hand
    since the live API test (#1596), and gender since the gender test
    (#1603)."""
    with pytest.raises(ValueError) as refused:
        edited_value(field, "Value", today=TODAY)
    assert not isinstance(refused.value, InvalidEdit)


def test_the_limit_is_parishsofts_where_it_is_shorter():
    """The PUT schema's limit, unless the census field's own is shorter."""
    assert edit_limit("first_name") == 30
    assert edit_limit("email") == 150
    assert edit_limit("home_phone") == 25
    assert edit_limit("gender") is None and edit_limit("birth_date") is None
    assert edit_limit("prefix") is None


def test_rows_offer_edit_value_with_the_value_to_publish():
    """Open Automatic Member rows only, prefilled with the edit when set,
    else the Family's answer, in the form the Family form shows it."""
    phone = {"normalized": "+13175550199", "display": "(317) 555-0199"}
    plain = shape(row(submitted_value=phone))
    assert plain["edit"] == {
        "value": "(317) 555-0199",
        "type": "text",
        "choices": (),
        "maxlength": 25,
        "edited": False,
    }
    edited = shape(
        row(
            submitted_value=phone,
            admin_value_set=True,
            admin_value={"normalized": "+13175550100", "display": "(317) 555-0100"},
        )
    )
    assert edited["edited"] == "(317) 555-0100"
    assert edited["family_answer"] == "(317) 555-0199"
    assert edited["edit"]["value"] == "(317) 555-0100" and edited["edit"]["edited"]
    born = shape(
        row(
            field="birth_date", submitted_value="2000-01-02", current_value="2000-01-01"
        )
    )
    assert born["edit"]["type"] == "date" and born["edit"]["maxlength"] is None
    assert shape(row(field="gender"))["edit"]["choices"] == (
        "Male",
        "Female",
        "Unspecified",
    )
    for values in (
        {"execution": "queued", "decision": "approved"},
        {"execution": "published", "decision": "approved"},
        {"handling": "manual", "field": "suffix"},
        {"entity_kind": "family", "field": "home_address"},
    ):
        assert shape(row(**values))["edit"] is None, values
    # A Conflict and an ignored change may still be edited.
    assert shape(row(execution="conflict"))["edit"] is not None
    assert shape(row(decision="ignored"))["edit"] is not None


def test_an_edited_address_past_the_guards_size_is_refused():
    """Each part within its own limit, the whole edit past the review
    guard's 1,024 bytes (two bytes a character), is refused before SQL;
    the same script within the limit is kept (#1719)."""
    from parishkit.stewardship.responses.census_review import (
        LONG_ADDRESS,
        edited_address,
    )

    from .census_factory import address

    parts = {"line1": "1" + "é" * 199, "city": "é" * 100, "country": "GB"}
    assert edited_address(address(**parts))["city"] == "é" * 100
    longer = address(**parts, line2="é" * 200, region="é" * 100)
    with pytest.raises(InvalidEdit, match=LONG_ADDRESS):
        edited_address(longer)
