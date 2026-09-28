"""Telephone numbers read as "+1 (502) 555-1234" everywhere; storage stays E.164."""

import re
from datetime import date
from pathlib import Path

import pytest
from django.template import engines

from parishkit.stewardship.accounts.parish_views import ParishForm
from parishkit.stewardship.jobs.campaign_mail_values import campaign_values
from parishkit.stewardship.responses.member_census import (
    MEMBER_FIELDS,
    validate_member_value,
)
from parishkit.stewardship.responses.merge import KnownValue
from parishkit.stewardship.web.presentation import parse_us_phone, phone

TEMPLATES = Path(__file__).parents[2] / "src/parishkit/stewardship"
PHONE_CONTEXT = re.compile(r"phone", re.IGNORECASE)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("+15025551234", "+1 (502) 555-1234"),
        ("502-555-1234", "+1 (502) 555-1234"),
        ("(502) 555-1234", "+1 (502) 555-1234"),
        ("502.555.1234", "+1 (502) 555-1234"),
        ("+1 502 555 1234", "+1 (502) 555-1234"),
        ("+15025551234;ext=12", "+1 (502) 555-1234 ext. 12"),
        ("270-994-4385 x5", "+1 (270) 994-4385 ext. 5"),
        ("+442071838750", "+44 20 7183 8750"),
        ("+33 1 42 68 53 00", "+33 1 42 68 53 00"),
    ],
)
def test_phone_display_format(value, expected):
    assert phone(value) == expected


@pytest.mark.parametrize(
    "value",
    ["junk", "12345", "+1 800 FLOWERS x", "555-1234", "+1 555 1234", "02071838750"],
)
def test_unrecognized_text_is_shown_unchanged(value):
    assert phone(value) == value


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_phone_is_blank(value):
    assert phone(value) == ""


@pytest.mark.parametrize(
    "value",
    [
        "(502) 555-1234",
        "502-555-1234",
        "502.555.1234",
        "+1 502 555 1234",
        "15025551234",
        "+1 (502) 555-1234",
    ],
)
def test_lenient_us_input_becomes_e164(value):
    assert parse_us_phone(value) == "+15025551234"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "junk",
        "123-456-7890",
        "502-155-1234",
        "+442071838750",
        "502-555-1234 x5",
        "555-1234",
    ],
)
def test_parish_phone_rejects_non_us_or_invalid(value):
    with pytest.raises(ValueError):
        parse_us_phone(value)


def _parish_form(phone_value):
    """A parish profile form with one varying telephone value."""
    return ParishForm(
        {
            "name": "Sample Parish",
            "website": "https://example.org",
            "timezone": "America/New_York",
            "phone": phone_value,
            "base_digest": "0" * 64,
        }
    )


def test_parish_form_stores_e164_from_any_common_format():
    form = _parish_form("(212) 555-1234")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == "+12125551234"


def test_parish_form_explains_rejected_phone():
    form = _parish_form("555-1234")
    assert not form.is_valid()
    assert "(212) 555-1234" in str(form.errors["phone"])


def test_parish_form_displays_stored_value_formatted():
    html = str(ParishForm(initial={"phone": "+12125551234"})["phone"])
    assert 'value="+1 (212) 555-1234"' in html


def test_phone_template_filter():
    template = engines["django"].from_string("{% load stewardship %}{{ value|phone }}")
    assert template.render({"value": "+15025551234"}) == "+1 (502) 555-1234"


def test_parish_phone_placeholder_is_formatted_in_mail():
    values = campaign_values(
        parish={"name": "P", "website": "https://example.org", "phone": "+15025551234"},
        campaign={
            "name": "C",
            "start_date": "2026-10-03",
            "end_date": "2026-11-02",
            "timezone": "America/New_York",
            "financial": None,
        },
    )
    assert values["parish_phone"] == "+1 (502) 555-1234"


def test_family_census_accepts_formatted_phone_input():
    """The Family form may send the display format; the server normalizes it."""
    field = next(field for field in MEMBER_FIELDS if field.name == "home_phone")
    result = validate_member_value(
        field, "+1 (502) 555-1234", KnownValue(False), today=date(2026, 9, 28)
    )
    assert result["normalized"] == "+15025551234"


def test_no_template_prints_a_phone_without_the_filter():
    """Phone values in templates go through |phone so every page matches."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for line in path.read_text().splitlines():
            if not PHONE_CONTEXT.search(line):
                continue
            for match in re.finditer(r"\{\{ ?([^}]*?) ?\}\}", line):
                expression = match.group(1)
                name = expression.split("|")[0].split(".")[-1]
                is_phone = (
                    name == "phone"
                    or name.endswith("_phone")
                    or (name == "value" and "phones" in line)
                )
                if is_phone and "|phone" not in expression:
                    offenders.append(f"{path.relative_to(TEMPLATES)}: {expression}")
    assert offenders == []
