"""The refresh schedule's rules, skips, precedence, coverage and spacing (#632)."""

import pytest

from parishkit.stewardship.source.refresh_rules import (
    Problem,
    check_schedule,
    daily_times,
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


def test_default_preset_covers_the_quick_update_at_the_nightly_hour():
    """Nightly at 02:00 and hourly quick updates: 23 quick updates, 02:00 covered."""
    result = daily_times(
        rules(at("full", "02:00"), every("quick", 60, "00:00", "23:00"))
    )
    assert result.full == ("02:00",)
    assert result.quick == hours(0, 1, *range(3, 24))
    assert len(result.quick) == 23
    # The 02:00 quick time is both kinds, so precedence (not coverage) drops it.
    assert result.covered == ()


def test_business_hours_preset_matches_the_spec():
    """Full at 02:00 and every 2 hours 08:00–18:00; quick hourly 07:00–19:00."""
    result = daily_times(
        rules(
            at("full", "02:00"),
            every("full", 120, "08:00", "18:00"),
            every("quick", 60, "07:00", "19:00"),
        )
    )
    assert result.full == hours(2, 8, 10, 12, 14, 16, 18)
    assert result.quick == hours(7, 9, 11, 13, 15, 17, 19)


def test_a_full_time_within_one_step_after_covers_the_quick_time():
    """Hourly quick at 09:00 with a full refresh at 09:30 is covered by it."""
    result = daily_times(
        rules(at("full", "09:30"), every("quick", 60, "08:00", "10:00"))
    )
    assert result.quick == ("08:00", "10:00")
    assert result.covered == (("09:00", "09:30"),)


def test_a_full_time_less_than_fifteen_minutes_before_covers_the_quick_time():
    """A full refresh at 09:50 covers a quick update at 10:00 but not at 10:05."""
    result = daily_times(
        rules(at("full", "09:50"), every("quick", 60, "10:00", "11:00"))
    )
    # 10:00 is covered from before; 09:50 is not after 11:00 either way.
    assert ("10:00", "09:50") in result.covered
    assert result.quick == ("11:00",)
    kept = daily_times(rules(at("full", "09:45"), every("quick", 60, "10:00", "10:00")))
    # Exactly 15 minutes before is not "less than 15": the quick time stays.
    assert kept.quick == ("10:00",)


def test_coverage_never_wraps_past_midnight():
    """A full refresh at 00:00 does not cover a quick update at 23:00."""
    result = daily_times(
        rules(at("full", "00:00"), every("quick", 60, "22:00", "23:00"))
    )
    assert result.quick == ("22:00", "23:00")


def test_the_rules_last_time_is_covered_by_its_own_step():
    """The bound for the last time uses the rule's step, not a next time."""
    result = daily_times(
        rules(at("full", "20:30"), every("quick", 60, "18:00", "20:00"))
    )
    assert result.covered == (("20:00", "20:30"),)


def test_a_single_quick_time_is_never_covered():
    """A quick time the Administrator listed alone stays, unless both kinds."""
    result = daily_times(rules(at("full", "09:30"), at("quick", "09:00")))
    assert result.quick == ("09:00",)


def test_skips_remove_times_of_either_kind_and_ranges_exclude_their_end():
    """A from–to skip removes its start up to, not including, its end."""
    result = daily_times(
        rules(
            every("full", 120, "00:00", "22:00"),
            every("quick", 60, "01:00", "23:00"),
            skips=[{"at": "12:00"}, {"from": "21:00", "to": "23:00"}],
        )
    )
    assert "12:00" not in result.full
    assert "21:00" not in result.quick and "22:00" not in result.full
    assert "23:00" in result.quick
    # The 11:00 quick update had been covered by 12:00; with 12:00 skipped it
    # is no longer covered.
    assert "11:00" in result.quick


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
        rules(*[at("full", "02:00")] * 97),
    ],
)
def test_only_the_closed_rule_shape_is_valid(value):
    """Unknown fields, steps, kinds and crossing ranges are refused."""
    assert not valid_shape(value)
    with pytest.raises(ValueError):
        daily_times(value)


def test_a_rule_whose_last_time_equals_its_start_gives_one_time():
    """From 08:00 to 08:00 every hour is just 08:00."""
    assert daily_times(rules(every("full", 60, "08:00", "08:00"))).full == ("08:00",)


def test_stored_settings_list_the_daily_times_and_the_nightly():
    """The writer stores daily full times, quick times and the rules."""
    document = rules(at("full", "02:00"), every("quick", 60, "06:00", "08:00"))
    assert stored_settings(document) == {
        "full_refresh": "daily",
        "nightly_time": "02:00",
        "full_refresh_times": ["02:00"],
        "delta_refresh": "times",
        "quick_refresh_times": ["06:00", "07:00", "08:00"],
        "refresh_rules": document,
    }
    nightly_only = stored_settings(rules(at("full", "02:00")))
    assert nightly_only["delta_refresh"] == "off"
    assert "quick_refresh_times" not in nightly_only
    with pytest.raises(ValueError):
        stored_settings(rules(at("quick", "02:00")))


def test_check_schedule_reports_every_problem_by_name():
    """Close times, off-quarter times and unmatched skips; skipping is allowed."""
    document = rules(
        at("full", "23:50"),
        at("full", "00:00"),
        at("quick", "12:10"),
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
    settings = stored_settings(rules(at("full", "02:00"), at("quick", "06:00")))
    assert check_schedule(settings) == []
    assert Problem("rules_mismatch") in check_schedule(
        settings | {"quick_refresh_times": ["07:00"]}
    )
    assert Problem("rules_mismatch") in check_schedule(
        settings | {"delta_refresh": "hourly"}
    )
    no_full = {"refresh_rules": rules(at("quick", "06:00"))}
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


def test_a_kept_time_excuses_only_a_full_time():
    """A kept off-quarter-hour time never excuses a quick time at that time."""
    settings = stored_settings(rules(at("full", "02:00"), at("quick", "12:10")))
    assert Problem("off_quarter", ("12:10",)) in check_schedule(
        settings, kept=("12:10",)
    )
    full = stored_settings(rules(at("full", "02:10")))
    assert check_schedule(full, kept=("02:10",)) == []
