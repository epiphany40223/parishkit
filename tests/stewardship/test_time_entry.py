"""Times of day typed in any common form (#631): the shared parser and fields.

The cases live in ``fixtures/time_entry_cases.json``, which the browser test
(``browser/test_time_entry.py``) runs against ``ui-v1.js`` as well, so the
server's parser and the page's cannot drift.
"""

import json
from datetime import time
from pathlib import Path

import pytest
from django import forms

from parishkit.stewardship.time_entry import (
    MESSAGES,
    TimeEntryError,
    canonical,
    duplicates,
    parse_time,
    parse_times,
    reading,
    split_times,
)
from parishkit.stewardship.web.time_entry_fields import (
    FlexibleTimeField,
    FlexibleTimeListField,
)

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "time_entry_cases.json").read_text()
)


def kept(case):
    """The case's saved time with seconds, as a ``time``, or None."""
    return time.fromisoformat(case["kept"]) if "kept" in case else None


@pytest.mark.parametrize("case", CASES["times"], ids=lambda case: repr(case["entry"]))
def test_every_time_case_reads_or_refuses_as_the_fixture_says(case):
    """One entry: the canonical value and reading, or the code and message."""
    if "error" in case:
        with pytest.raises(TimeEntryError) as caught:
            parse_time(case["entry"], kept=kept(case))
        assert caught.value.code == case["error"]
        assert caught.value.message == case["message"]
        return
    value = parse_time(case["entry"], kept=kept(case))
    assert canonical(value) == case["value"]
    assert reading(value) == case["reading"]


@pytest.mark.parametrize("case", CASES["lists"], ids=lambda case: repr(case["entry"]))
def test_every_list_case_reads_or_refuses_as_the_fixture_says(case):
    """A list: every entry in order with its repeats, or the first refusal.

    A list with no entries (blank, or only separators) is the field's blank
    list, when the case gives one.
    """
    blank = [case["blank"]] if "blank" in case else []
    field = FlexibleTimeListField(
        required=False, blank=blank, max_times=case.get("max")
    )
    if "error" in case:
        with pytest.raises(forms.ValidationError) as caught:
            field.clean(case["entry"])
        assert caught.value.code == case["error"]
        assert caught.value.messages == [case["message"]]
        return
    values = parse_times(case["entry"])
    if blank:
        assert values == []
        values = parse_times(case["blank"])
    assert [canonical(value) for value in values] == case["values"]
    assert duplicates(values) == case["duplicates"]
    assert field.clean(case["entry"]) == sorted(set(case["values"]))


def test_the_fixture_covers_every_refusal_and_the_issues_examples():
    """Each message is pinned, and the issue's named edge cases are present."""
    codes = {
        case["error"]
        for group in CASES.values()
        if isinstance(group, list)
        for case in group
        if "error" in case
    }
    assert codes == set(MESSAGES)
    entries = {case["entry"] for case in CASES["times"]}
    assert {"12 am", "12 pm", "24:00", "7:60", "13pm", ""} <= entries


def test_errors_resolve_their_message_only_when_read(monkeypatch):
    """Raising and catching needs no translation; the message is lazy."""
    from parishkit.stewardship import time_entry

    calls = []
    monkeypatch.setitem(
        time_entry.MESSAGES,
        "hour",
        type("Lazy", (), {"__str__": lambda _: calls.append(1) or "%(entry)s"})(),
    )
    with pytest.raises(TimeEntryError) as caught:
        parse_time("25")
    assert caught.value.code == "hour" and calls == []
    assert str(caught.value) == "25" and calls == [1]


def test_a_lone_suffix_joins_the_entry_before_it():
    """In a list "2 pm" is one entry; a leading suffix stays its own entry."""
    assert split_times("2 pm, 3 a.m.;4\nnoon") == ["2 pm", "3 a.m.", "4", "noon"]
    assert split_times("pm 2") == ["pm", "2"]
    # Not across a comma, semicolon or new line.
    assert split_times("2, pm;3\na") == ["2", "pm", "3", "a"]
    assert split_times(" , ; ") == []


def test_single_field_cleans_to_a_time_and_keeps_a_saved_time_with_seconds():
    """Empty is None (required decides); a kept saved time saves unchanged."""
    field = FlexibleTimeField(required=False)
    assert field.clean("") is None
    assert field.clean("9pm") == time(21, 0)
    assert field.widget.attrs["data-time-entry"] == "time"
    with pytest.raises(forms.ValidationError) as caught:
        field.clean("10:00:37")
    assert caught.value.code == "seconds"
    field.keep("10:00:37")
    assert field.widget.attrs["data-time-kept"] == "10:00:37"
    assert field.clean("10:00:37") == time(10, 0, 37)
    # A saved whole-minute time needs no keeping.
    field.keep("09:00:00")
    assert "data-time-kept" not in field.widget.attrs
    with pytest.raises(forms.ValidationError):
        FlexibleTimeField().clean("")


def test_single_field_shows_saved_times_canonically_and_entries_as_typed():
    """Saved ISO text and times display as HH:MM; a typed entry is unchanged."""
    field = FlexibleTimeField()
    assert field.prepare_value("09:00:00") == "09:00"
    assert field.prepare_value(time(21, 30)) == "21:30"
    assert field.prepare_value("10:00:37") == "10:00:37"
    assert field.prepare_value("9pm") == "9pm"
    assert field.prepare_value("25:00") == "25:00"
    assert field.prepare_value(None) is None


def test_single_field_compares_times_not_text():
    """Typing "9am" for a saved 09:00:00 is no change; a blank row is unchanged."""
    field = FlexibleTimeField(required=False)
    assert not field.has_changed("09:00:00", "9am")
    assert field.has_changed("09:00:00", "9:01")
    assert not field.has_changed(None, "")
    assert field.has_changed(None, "nonsense")


def test_list_field_blank_bound_and_display():
    """Blank is the stated list; the bound counts different times only."""
    field = FlexibleTimeListField(required=False, blank=["02:00"], max_times=2)
    assert field.clean("") == ["02:00"]
    assert field.clean("   ") == ["02:00"]
    assert field.clean("2pm 14:00, 1400; 2am") == ["02:00", "14:00"]
    with pytest.raises(forms.ValidationError) as caught:
        field.clean("1 2 3")
    assert caught.value.messages == ["Enter at most 2 different times."]
    assert field.prepare_value(["02:00", "14:00"]) == "02:00, 14:00"
    assert field.prepare_value("2pm") == "2pm"
    assert field.widget.attrs["data-time-entry"] == "list"
    assert field.widget.attrs["data-time-blank"] == "02:00"
    assert field.widget.attrs["data-time-max"] == "2"
