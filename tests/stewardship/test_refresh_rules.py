"""The refresh schedule's rules, skips and spacing (#632).

Quick rules were removed (#1147): a stored one still has a valid shape but
gives no time, and a new or changed schedule may not have one.
"""

import pytest

from parishkit.stewardship.source.refresh_rules import (
    MAX_RULES,
    MAX_SKIPS,
    Problem,
    check_schedule,
    converted_rules,
    daily_times,
    full_rules,
    spacing_conflicts,
    stored_settings,
    valid_shape,
)


def rules(*rows, skips=(), skip=False):
    """A ``refresh_rules`` document from rule rows and skip rows."""
    return {
        "rules": list(rows),
        "skips": list(skips),
        "skip_around_family_emails": skip,
    }


def every(kind, step, start, end):
    """A range rule row."""
    return {"kind": kind, "every": step, "from": start, "to": end}


def at(kind, value):
    """A single-time rule row."""
    return {"kind": kind, "at": value}


def hours(*values):
    """``HH:00`` times for the given hours."""
    return tuple(f"{value:02d}:00" for value in values)


def test_a_stored_quick_rule_gives_no_time():
    """A stored quick rule keeps its shape but schedules nothing (#1147)."""
    stored = rules(at("full", "02:00"), every("quick", 60, "00:00", "23:00"))
    assert valid_shape(stored, stored=True)
    assert not valid_shape(stored)
    assert daily_times(stored) == daily_times(rules(at("full", "02:00")))
    assert daily_times(stored).full == ("02:00",)


def test_business_hours_preset_matches_the_spec():
    """Full at 02:00 and every 2 hours 08:00–18:00."""
    result = daily_times(
        rules(at("full", "02:00"), every("full", 120, "08:00", "18:00"))
    )
    assert result.full == hours(2, 8, 10, 12, 14, 16, 18)


def test_skips_remove_times_and_ranges_exclude_their_end():
    """A from–to skip removes its start up to, not including, its end."""
    result = daily_times(
        rules(
            every("full", 60, "00:00", "23:00"),
            skips=[{"at": "12:00"}, {"from": "21:00", "to": "23:00"}],
        )
    )
    assert "12:00" not in result.full
    assert "21:00" not in result.full and "22:00" not in result.full
    assert "23:00" in result.full


def test_a_skip_that_matches_nothing_is_reported():
    """A mistyped skip is not silently ignored."""
    result = daily_times(rules(at("full", "02:00"), skips=[{"at": "12:30"}]))
    assert result.unmatched_skips == (0,)


def test_spacing_is_measured_around_midnight():
    """23:45 and 00:00 are allowed; a kept 23:50 and 00:00 are not."""
    assert spacing_conflicts(["00:00", "23:45"]) == ()
    assert spacing_conflicts(["00:00", "23:50"]) == (("23:50", "00:00"),)
    assert spacing_conflicts(["08:00", "08:10", "12:00"]) == (("08:00", "08:10"),)
    assert spacing_conflicts(["02:00"]) == ()


def test_the_96_time_maximum_is_every_quarter_hour():
    """Every quarter hour is 96 times and passes; it is the most a day holds."""
    every_quarter = rules(every("full", 15, "00:00", "23:45"))
    result = daily_times(every_quarter)
    assert len(result.full) == 96
    assert check_schedule(stored_settings(every_quarter)) == []


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        rules(),
        rules(every("full", 45, "00:00", "12:00")),
        rules(every("full", True, "00:00", "12:00")),
        rules(every("full", 60, "12:00", "11:00")),
        rules(at("both", "02:00")),
        rules(at("full", "2:00")),
        rules(at("full", "02:00") | {"note": "x"}),
        rules(at("full", "02:00"), skips=[{"from": "10:00", "to": "10:00"}]),
        rules(at("full", "02:00"), skips=[{"at": "10:00", "to": "11:00"}]),
        rules(at("full", "02:00"), skip="yes"),
        rules(at("full", "02:00")) | {"extra": 1},
    ],
)
def test_only_the_closed_rule_shape_is_valid(value):
    """Unknown fields, steps, kinds and crossing ranges are refused."""
    assert not valid_shape(value)
    with pytest.raises(ValueError):
        daily_times(value)


def test_the_row_caps_are_the_editors():
    """One cap (#936): exactly 64 rules and 16 skips are a valid shape, the
    editor's caps too, so any stored document can be saved back from the page."""
    from parishkit.stewardship.accounts.refresh_schedule_forms import (
        RULE_ROWS,
        SKIP_ROWS,
    )

    assert (MAX_RULES, MAX_SKIPS) == (RULE_ROWS, SKIP_ROWS) == (64, 16)
    full = rules(
        *[at("full", "02:00")] * MAX_RULES, skips=[{"at": "10:00"}] * MAX_SKIPS
    )
    assert valid_shape(full)
    for past in (
        rules(*[at("full", "02:00")] * (MAX_RULES + 1)),
        rules(at("full", "02:00"), skips=[{"at": "10:00"}] * (MAX_SKIPS + 1)),
    ):
        assert not valid_shape(past)
        assert valid_shape(past, stored=True)


def test_a_stored_schedule_keeps_the_older_row_bound():
    """A document stored before #936 (96 rows of each kind) still reads and
    computes its times; one more row is refused even then."""
    stored = rules(*[at("full", "02:00")] * 96, skips=[{"at": "10:00"}] * 96)
    assert not valid_shape(stored)
    assert valid_shape(stored, stored=True)
    assert daily_times(stored).full == ("02:00",)
    assert not valid_shape(rules(*[at("full", "02:00")] * 97), stored=True)


def test_a_rule_whose_last_time_equals_its_start_gives_one_time():
    """From 08:00 to 08:00 every hour is just 08:00."""
    assert daily_times(rules(every("full", 60, "08:00", "08:00"))).full == ("08:00",)


def test_stored_settings_list_the_daily_times_and_the_nightly():
    """The writer stores the daily full times, quick updates off, and the rules."""
    document = rules(at("full", "02:00"), every("full", 60, "06:00", "08:00"))
    assert stored_settings(document) == {
        "full_refresh": "daily",
        "nightly_time": "02:00",
        "full_refresh_times": ["02:00", "06:00", "07:00", "08:00"],
        "delta_refresh": "off",
        "refresh_rules": document,
    }
    with pytest.raises(ValueError):
        stored_settings(rules(at("full", "02:00"), skips=[{"at": "02:00"}]))


def test_check_schedule_reports_every_problem_by_name():
    """Close times, off-quarter times and unmatched skips; skipping is allowed."""
    document = rules(
        at("full", "23:50"),
        at("full", "00:00"),
        at("full", "12:10"),
        skips=[{"at": "05:00"}],
        skip=True,
    )
    problems = check_schedule(stored_settings(document))
    assert Problem("too_close", ("23:50", "00:00")) in problems
    assert Problem("off_quarter", ("12:10",)) in problems
    assert Problem("off_quarter", ("23:50",)) in problems
    assert Problem("unmatched_skip", index=0) in problems
    # Skipping around Family emails is honored since #632 step 2b.
    assert {problem.code for problem in problems} == {
        "too_close",
        "off_quarter",
        "unmatched_skip",
    }
    # A kept off-quarter-hour full time is allowed; a new one is not.
    kept = check_schedule(stored_settings(document), kept=("23:50",))
    assert Problem("off_quarter", ("23:50",)) not in kept


def test_check_schedule_refuses_lists_the_rules_do_not_produce():
    """The stored lists must be exactly what the rules give; a full time is needed."""
    settings = stored_settings(rules(at("full", "02:00"), at("full", "06:00")))
    assert check_schedule(settings) == []
    assert Problem("rules_mismatch") in check_schedule(
        settings | {"quick_refresh_times": ["07:00"]}
    )
    assert Problem("rules_mismatch") in check_schedule(
        settings | {"delta_refresh": "times"}
    )
    no_full = {"refresh_rules": rules(at("full", "06:00"), skips=[{"at": "06:00"}])}
    assert check_schedule(no_full) == [Problem("no_full")]


def test_overlapping_and_repeated_skips_are_not_reported_unmatched():
    """Each skip is matched against the times before any skip was applied."""
    result = daily_times(
        rules(
            every("full", 60, "00:00", "12:00"),
            skips=[
                {"from": "08:00", "to": "11:00"},
                {"at": "09:00"},
                {"from": "10:00", "to": "12:00"},
                {"at": "09:00"},
            ],
        )
    )
    assert result.unmatched_skips == ()
    assert result.full == hours(*range(8), 12)


def test_a_kept_time_excuses_an_off_quarter_hour_full_time():
    """A kept off-quarter-hour full time is allowed; a new one is not."""
    full = stored_settings(rules(at("full", "02:10")))
    assert check_schedule(full, kept=("02:10",)) == []
    assert Problem("off_quarter", ("02:10",)) in check_schedule(full)


PRODUCTION = ("00:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00")


def test_listed_full_times_are_converted_one_row_each_with_no_rule_inferred():
    """Decision 20: one "Full at" row per stored time; no quick rule (#1147)."""
    converted = converted_rules(
        {
            "nightly_time": "00:00",
            "full_refresh_times": list(PRODUCTION),
            "delta_refresh": "quarter_hour",
        }
    )
    assert converted == rules(*(at("full", value) for value in PRODUCTION))
    assert daily_times(converted).full == PRODUCTION


def test_an_empty_document_is_the_default_nightly():
    """``refresh_settings``'s defaults are filled first; no quick rule (#1147)."""
    assert converted_rules({}) == rules(at("full", "02:00"))


@pytest.mark.parametrize(
    "full,quick,expected",
    [
        ("hourly", "quarter_hour", [every("full", 60, "00:00", "23:00")]),
        ("hourly", "hourly", [every("full", 60, "00:00", "23:00")]),
        ("quarter_hour", "off", [every("full", 15, "00:00", "23:45")]),
        ("daily", "hourly", [at("full", "02:00")]),
        ("daily", "off", [at("full", "02:00")]),
    ],
)
def test_existing_frequencies_convert_to_rules_through_the_day(full, quick, expected):
    """Hourly and quarter-hour refreshes become rules from 00:00; quick is dropped."""
    converted = converted_rules({"full_refresh": full, "delta_refresh": quick})
    assert converted == rules(*expected)
    # The converted rules are a schedule the validator accepts as derived.
    assert check_schedule(stored_settings(converted)) == []


def test_a_kept_off_quarter_hour_time_converts_alone():
    """A kept 02:10 converts to its own row, with no quick rows beside it."""
    converted = converted_rules(
        {"nightly_time": "02:10", "full_refresh_times": ["02:10"]}
    )
    assert converted == rules(at("full", "02:10"))
    assert check_schedule(stored_settings(converted), kept=("02:10",)) == []


def test_a_saved_schedule_is_shown_without_its_stored_quick_rules():
    """A schedule saved with quick rules shows its full rules only (#1147)."""
    stored = rules(at("full", "02:00"), every("quick", 60, "00:00", "23:00"), skip=True)
    shown = converted_rules({"refresh_rules": stored})
    assert shown == full_rules(stored) == rules(at("full", "02:00"), skip=True)
    assert valid_shape(shown)


def test_a_schedule_saved_with_rules_is_shown_as_saved():
    """Only an existing schedule is converted; skip switch starts off for it."""
    saved = rules(at("full", "00:00"), skip=True)
    assert converted_rules(stored_settings(saved)) is saved
    assert converted_rules({})["skip_around_family_emails"] is False


def test_a_skip_of_only_quick_times_goes_with_the_quick_rules():
    """A skip that matched only a stored quick rule's times would match nothing
    once the rule is gone, and block the next save; it is dropped too (#1147).
    A skip of a full time, and one that already matched nothing, stay."""
    stored = rules(
        at("full", "02:00"),
        every("full", 60, "08:00", "10:00"),
        every("quick", 15, "06:00", "06:45"),
        skips=[
            {"from": "06:00", "to": "07:00"},
            {"at": "09:00"},
            {"at": "23:30"},
        ],
    )
    shown = full_rules(stored)
    assert shown["skips"] == [{"at": "09:00"}, {"at": "23:30"}]
    assert daily_times(shown).unmatched_skips == (1,)
    assert check_schedule(stored_settings(shown)) == [
        Problem("unmatched_skip", index=1)
    ]
