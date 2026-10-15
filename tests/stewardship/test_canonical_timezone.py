"""Canonical time zone names for new choices; stored aliases keep working (#1286)."""

import re
from importlib.resources import files
from zoneinfo import ZoneInfo

import pytest
from django import forms

from parishkit.stewardship.accounts.setup_forms import SetupParishForm
from parishkit.stewardship.accounts.timezone_field import (
    TimezoneField,
    canonical_choices,
)
from parishkit.stewardship.schema_primitives import (
    canonical_timezone,
    timezone_links,
    timezone_names,
)


def baseline_table():
    """The alias table of the baseline ``stewardship_timezone_name_v1``."""
    text = files("parishkit.stewardship").joinpath("schema/functions.sql").read_text()
    start = text.index("CREATE FUNCTION public.stewardship_timezone_name_v1(")
    body = text[start : text.index("$$;", start)]
    return dict(re.findall(r"WHEN '([^']+)' THEN '([^']+)'", body))


def test_the_tzdata_links_equal_the_database_helpers_table():
    """Python's mapping and the SQL table hold the same backward set.

    The PostgreSQL test compares the installed function; this compares the
    baseline file, so a tzdata update that adds or moves a link fails here
    first and needs a forward migration replacing the helper.
    """
    assert timezone_links() == baseline_table()


def test_every_alias_maps_to_a_canonical_catalog_zone():
    """Each target is itself canonical, in the catalog and loadable."""
    links = timezone_links()
    assert len(links) >= 250
    for alias, zone in links.items():
        assert alias in timezone_names() and zone in timezone_names()
        assert zone not in links
        ZoneInfo(zone)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("US/Eastern", "America/New_York"),
        ("US/Central", "America/Chicago"),
        ("Canada/Atlantic", "America/Halifax"),
        ("Brazil/East", "America/Sao_Paulo"),
        ("Chile/Continental", "America/Santiago"),
        ("Cuba", "America/Havana"),
        ("Egypt", "Africa/Cairo"),
        ("Navajo", "America/Denver"),
        ("America/Indianapolis", "America/Indiana/Indianapolis"),
        ("America/New_York", "America/New_York"),
        ("Mars/Base", "Mars/Base"),
    ],
)
def test_canonical_timezone(given, expected):
    """An alias maps to its zone; anything else is returned unchanged."""
    assert canonical_timezone(given) == expected


class ZoneForm(forms.Form):
    """One time zone field, as the Parish and campaign forms declare it."""

    timezone = TimezoneField(label="Zone")


def options(form):
    """The rendered select's (value, label) options."""
    html = str(form["timezone"])
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', html)


def test_the_field_offers_canonical_names_only():
    """No alias is offered as a choice of its own."""
    offered = [value for value, _ in canonical_choices()]
    assert "America/New_York" in offered
    assert not set(offered) & set(timezone_links())
    assert [value for value, _ in options(ZoneForm())] == offered


def test_a_stored_alias_is_shown_selected_with_its_canonical_name():
    """An unchanged form keeps the stored alias, labelled for the reader."""
    form = ZoneForm(initial={"timezone": "US/Eastern"})
    found = dict(options(form))
    assert found["US/Eastern"] == "US/Eastern (older name for America/New_York)"
    assert 'value="US/Eastern" selected' in str(form["timezone"])
    # Only the stored alias is added.
    assert len(found) == len(canonical_choices()) + 1


@pytest.mark.parametrize(
    ("posted", "valid"),
    [("US/Eastern", True), ("America/New_York", True), ("Mars/Base", False)],
)
def test_the_field_accepts_any_catalog_name(posted, valid):
    """A stored alias posted back is valid; a name outside the catalog is not."""
    form = ZoneForm(data={"timezone": posted})
    assert form.is_valid() is valid
    if valid:
        assert form.cleaned_data["timezone"] == posted


@pytest.mark.parametrize(
    ("disabled", "expected"), [(False, "America/New_York"), (True, "US/Eastern")]
)
def test_setup_stores_a_new_zone_canonically(disabled, expected):
    """The setup Parish step stores the canonical name until the load fixes it."""
    form = SetupParishForm(
        data={"timezone": "US/Eastern"}, initial={"timezone": "US/Eastern"}
    )
    form.fields["timezone"].disabled = disabled
    form.is_valid()
    assert form.cleaned_data["timezone"] == expected


def test_a_first_setup_zone_starts_on_the_browser_zone_or_a_blank_choice():
    """With no zone saved the setup Parish step's menu is marked for the
    browser's zone and starts on a blank required entry, never the first
    catalog zone (Africa/Abidjan was stored that way on pk-local, #1330)."""
    form = SetupParishForm()
    menu = str(form["timezone"])
    assert "data-browser-default" in menu
    assert options(form)[0] == ("", "Choose a time zone")
    assert 'value="" selected' in menu or "selected" not in menu
    assert 'value="Africa/Abidjan" selected' not in menu
    refused = SetupParishForm(data={"timezone": ""})
    assert not refused.is_valid() and "timezone" in refused.errors
    # A form drawn again keeps the posted zone; the browser does not reset it.
    assert "data-browser-default" not in str(refused["timezone"])


def test_a_saved_setup_zone_is_kept():
    """A zone already saved in the setup draft is shown as saved."""
    form = SetupParishForm(initial={"timezone": "America/Chicago"})
    menu = str(form["timezone"])
    assert "data-browser-default" not in menu
    assert 'value="America/Chicago" selected' in menu
    assert options(form)[0][0] != ""
