"""A ParishSoft Ministry name is always display-safe, never an outage (#341)."""

import json
import logging
import unicodedata

import pytest

from parishkit.stewardship.observability import Event, SafeJsonFormatter, emit
from parishkit.stewardship.responses.inputs import _digest
from parishkit.stewardship.responses.ministry import MinistryInputs, MinistryOption
from parishkit.stewardship.source import catalog_names
from parishkit.stewardship.source.catalog_names import (
    MAX_MINISTRY_LABEL,
    clean_ministry_name,
    fund_display_name,
    ministry_display_name,
)

from .test_ministry_response_inputs import inputs


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    """Each test starts with no names already warned about in this process."""
    monkeypatch.setattr(catalog_names, "_WARNED", set())


@pytest.mark.parametrize(
    ("name", "shown", "repaired"),
    [
        ("Choir", "Choir", False),
        ("  Choir  ", "Choir", False),
        # A valid name keeps its inner spacing exactly, so deploying the rule
        # changes no existing label or projection digest.
        ("Food   pantry", "Food   pantry", False),
        ("Food\u00a0pantry", "Food\u00a0pantry", False),
        # Only a name that needs repair is also re-spaced (NBSP included).
        ("Food\u00a0\u00a0pantry\x00", "Food pantry", True),
        ("Zero\u200dwidth\u200cjoiners", "Zerowidthjoiners", True),
        ("\u202eevil", "evil", True),
        ("Café", "Café", False),
        ("x" * MAX_MINISTRY_LABEL, "x" * MAX_MINISTRY_LABEL, False),
        ("", "Ministry 7", True),
        ("   ", "Ministry 7", True),
        (None, "Ministry 7", True),
        (42, "Ministry 7", True),
        ("\x00​\x07", "Ministry 7", True),
        ("Choir\x00", "Choir", True),
        ("Food\npantry", "Food pantry", True),
        ("Food\t \r\npantry", "Food pantry", True),
        ("Zero​width", "Zerowidth", True),
        ("x" * (MAX_MINISTRY_LABEL + 1), "x" * (MAX_MINISTRY_LABEL - 1) + "…", True),
        # A cut never leaves a trailing space before the ellipsis.
        (
            "x" * (MAX_MINISTRY_LABEL - 2) + " yz" + "x" * 50,
            "x" * (MAX_MINISTRY_LABEL - 2) + "…",
            True,
        ),
    ],
)
def test_clean_ministry_name(name, shown, repaired):
    """Trim, drop control characters, collapse spaces, cap and fall back."""
    assert clean_ministry_name(7, name) == (shown, repaired)


@pytest.mark.parametrize("name", ["", None, "x" * 9000, "a\x1bb\x7f", "\ud800"])
def test_cleaned_names_always_meet_the_form_label_rule(name):
    """Every result is non-blank, within the limit and free of C* characters."""
    shown, _ = clean_ministry_name(3, name)
    assert shown and shown == shown.strip()
    assert len(shown) <= MAX_MINISTRY_LABEL
    assert not any(unicodedata.category(char).startswith("C") for char in shown)


def test_repair_logs_a_warning_with_the_duid_only(caplog):
    """The structured warning names the Ministry by DUID, never by its name."""
    with caplog.at_level(logging.DEBUG):
        assert ministry_display_name(12, "secret\x00name") == "secretname"
    records = [
        r for r in caplog.records if r.msg is Event.SOURCE_MINISTRY_NAME_REPAIRED
    ]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    output = SafeJsonFormatter().format(records[0])
    payload = json.loads(output)
    assert payload["message"] == "source_ministry_name_repaired"
    assert payload["extra"]["ministry_duid"] == 12
    assert "secret" not in output


def test_usable_name_logs_nothing(caplog):
    """A name the form could show as it was is not reported."""
    with caplog.at_level(logging.DEBUG):
        assert ministry_display_name(12, " Choir ") == "Choir"
    assert not caplog.records


@pytest.mark.parametrize(
    ("event", "duid"),
    [
        (Event.SOURCE_MINISTRY_NAME_REPAIRED, 0),
        (Event.SOURCE_MINISTRY_NAME_REPAIRED, True),
        (Event.SOURCE_MINISTRY_NAME_REPAIRED, "12"),
        (Event.SOURCE_MINISTRY_NAME_REPAIRED, 2**31),
        (Event.TASK_STARTED, 12),
    ],
)
def test_ministry_duid_is_only_a_positive_identity_on_its_event(event, duid):
    """No other event, and no non-identity value, can ride on this field."""
    with pytest.raises(ValueError):
        emit(event, ministry_duid=duid)


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Offertory", "Offertory"),
        (" Building\n fund ", "Building fund"),
        ("", "Fund 9"),
        (None, "Fund 9"),
        ("\x00", "Fund 9"),
        ("x" * 600, "x" * (MAX_MINISTRY_LABEL - 1) + "\u2026"),
    ],
)
def test_fund_display_name(name, shown, caplog):
    """Funds share the cleaning rule, fall back to "Fund <DUID>" and never log."""
    with caplog.at_level(logging.DEBUG):
        assert fund_display_name(9, name) == shown
    assert not caplog.records


def test_valid_double_spaced_name_keeps_its_old_label_and_digest():
    """Deploying the rule does not change a valid label's projection digest."""
    catalog = [{"id": 4, "name": " Food  Pantry ", "catalog_present": True}]
    projection = inputs(catalog=catalog, roster=[])
    # What the form built before #341: NFC(strip()) of the raw name.
    before = MinistryInputs((MinistryOption(4, "Food  Pantry"),), ((3, ()),))
    assert projection.options == before.options
    assert _digest(projection.comparison()) == _digest(before.comparison())


def test_repair_warning_is_logged_once_per_duid_and_name(caplog):
    """Repeated reads stay quiet; a different bad name for the DUID warns again."""
    with caplog.at_level(logging.DEBUG):
        for name in ("bad\x00", "bad\x00", None, None, "bad\x00"):
            ministry_display_name(12, name)
        ministry_display_name(13, None)
    assert [
        record.extra["ministry_duid"]
        for record in caplog.records
        if record.msg is Event.SOURCE_MINISTRY_NAME_REPAIRED
    ] == [12, 12, 13]


def test_warning_memory_is_bounded(monkeypatch, caplog):
    """A full set is cleared rather than growing without limit."""
    monkeypatch.setattr(catalog_names, "_WARNED_LIMIT", 2)
    for duid in (1, 2, 3):
        ministry_display_name(duid, None)
    assert len(catalog_names._WARNED) == 1
