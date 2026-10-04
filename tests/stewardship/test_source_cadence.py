"""Source slots are deterministic across restarts, DST and bounded catch-up."""

from datetime import UTC, datetime, timedelta

import pytest

from parishkit.stewardship.source.cadence import due_slots, longest_gap, next_full_at


def slots(instant, **overrides):
    """Use explicit nonsecret scope and parish-local scheduling inputs."""
    return due_slots(
        **dict(
            now=datetime.fromisoformat(instant),
            timezone="America/New_York",
            nightly_time="02:00",
            scope_fingerprint="a" * 64,
        )
        | overrides
    )


def test_latest_due_slots_are_bounded_and_full_precedes_delta():
    """Downtime selects one latest full refresh, not all historical read slots."""
    full, delta = slots("2026-09-11T17:23:42+00:00")
    assert full.cause == "nightly" and delta.cause == "delta"
    assert full.due_at == datetime(2026, 9, 11, 6, tzinfo=UTC)
    assert delta.due_at == datetime(2026, 9, 11, 17, 15, tzinfo=UTC)
    assert (full, delta) == slots("2026-09-11T17:29:59+00:00")
    assert len(slots("2028-01-01T17:23:42+00:00")) == 2


def test_before_nightly_time_selects_previous_local_day():
    """A restart does not produce a future nightly observation early."""
    full, _ = slots("2026-09-11T05:59:59+00:00")
    assert full.due_at == datetime(2026, 9, 10, 6, tzinfo=UTC)


def test_gap_uses_first_real_instant_and_fold_has_one_nightly_identity():
    """The shared resolver supplies exact gap/fold behavior, not arithmetic guesses."""
    full, _ = slots("2026-03-08T07:00:00+00:00")
    assert full.due_at == datetime(2026, 3, 8, 7, tzinfo=UTC)
    first = slots("2026-11-01T05:30:00+00:00", nightly_time="01:30")
    second = slots("2026-11-01T06:30:00+00:00", nightly_time="01:30")
    assert first[0] == second[0]
    assert first[0].due_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert first[1].slot_key != second[1].slot_key


def test_new_scope_or_schedule_gets_new_slot_but_not_a_new_delta_for_time_edit():
    """Cadence identity changes only when that cadence's actual meaning changes."""
    instant = "2026-09-11T17:23:42+00:00"
    original = slots(instant)
    different_scope = slots(instant, scope_fingerprint="b" * 64)
    different_time = slots(instant, nightly_time="03:00")
    assert all(a != b for a, b in zip(original, different_scope, strict=True))
    assert original[0] != different_time[0]
    assert original[1] == different_time[1]


@pytest.mark.parametrize("value", ["2:00", "24:00", "02:60", "02:00:00", None, True])
def test_noncanonical_nightly_time_is_rejected(value):
    """Settings may not smuggle a second cadence or ambiguous wall-time value."""
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42+00:00", nightly_time=value)


def test_naive_now_and_noncanonical_scope_are_rejected():
    """Never infer the machine timezone or coerce scope identity."""
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42")
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42+00:00", scope_fingerprint="PRIVATE")


def test_hourly_full_refresh_uses_the_latest_utc_hour_and_keeps_deltas():
    """An hourly full load is due on the hour; quarter-hour deltas continue."""
    full, delta = slots("2026-09-11T17:23:42+00:00", frequency="hourly")
    assert full.cause == "nightly" and delta.cause == "delta"
    assert full.due_at == datetime(2026, 9, 11, 17, tzinfo=UTC)
    assert delta.due_at == datetime(2026, 9, 11, 17, 15, tzinfo=UTC)
    assert (
        full.slot_key
        != slots("2026-09-11T18:00:00+00:00", frequency="hourly")[0].slot_key
    )


def test_quarter_hour_full_refresh_replaces_the_delta_slot():
    """A full load every 15 minutes already covers each delta slot."""
    (full,) = slots("2026-09-11T17:23:42+00:00", frequency="quarter_hour")
    assert full.cause == "nightly"
    assert full.due_at == datetime(2026, 9, 11, 17, 15, tzinfo=UTC)


@pytest.mark.parametrize("frequency", ["weekly", "", None])
def test_unknown_frequency_is_refused(frequency):
    """The slot calculation accepts only the three stored frequencies."""
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42+00:00", frequency=frequency)


def test_latest_configured_time_is_chosen_and_recorded():
    """Several daily times: the latest one due wins, and the slot names it (#465)."""
    times = ["02:00", "08:00", "14:00", "20:00"]
    full, delta = slots("2026-09-11T17:23:42+00:00", full_refresh_times=times)
    # 13:23 EDT: the 08:00 refresh is the latest due (12:00 UTC).
    assert full.due_at == datetime(2026, 9, 11, 12, tzinfo=UTC)
    assert full.nightly_time == "08:00" and delta.nightly_time is None
    later, _ = slots("2026-09-11T18:00:00+00:00", full_refresh_times=times)
    assert later.due_at == datetime(2026, 9, 11, 18, tzinfo=UTC)
    assert later.nightly_time == "14:00" and later.slot_key != full.slot_key
    # The time is part of the identity: the nightly slot of the same day differs.
    assert full.slot_key != slots("2026-09-11T17:23:42+00:00")[0].slot_key


def test_before_the_first_time_selects_the_previous_days_last_time():
    """Just after midnight the latest due refresh is yesterday's evening one."""
    full, _ = slots("2026-09-11T05:00:00+00:00", full_refresh_times=["02:00", "20:00"])
    # 01:00 EDT on 9/11: 20:00 EDT on 9/10 is 00:00 UTC on 9/11.
    assert full.due_at == datetime(2026, 9, 11, 0, tzinfo=UTC)
    assert full.nightly_time == "20:00"


def test_times_inside_a_dst_gap_share_one_instant_and_the_nightly_wins():
    """Times skipped by spring-forward resolve to one instant; the nightly wins the tie.

    The nightly tick must still exist (a Family send never holds it), so on
    equal instants the nightly time is chosen; among other tied times the
    later wall time wins, so the choice stays deterministic.
    """
    times = ["02:00", "02:30"]
    full, _ = slots("2026-03-08T07:00:00+00:00", full_refresh_times=times)
    assert full.due_at == datetime(2026, 3, 8, 7, tzinfo=UTC)
    assert full.nightly_time == "02:00"
    # 2027 springs forward on March 14; the same rule holds.
    later, _ = slots("2027-03-14T07:00:00+00:00", full_refresh_times=times)
    assert later.due_at == datetime(2027, 3, 14, 7, tzinfo=UTC)
    assert later.nightly_time == "02:00"
    earlier, _ = slots("2026-03-08T06:59:59+00:00", full_refresh_times=times)
    # The day before, 02:30 EST is 07:30 UTC.
    assert earlier.due_at == datetime(2026, 3, 7, 7, 30, tzinfo=UTC)
    assert earlier.nightly_time == "02:30"
    other, _ = slots(
        "2026-03-08T07:00:00+00:00",
        nightly_time="01:00",
        full_refresh_times=["01:00", "02:00", "02:30"],
    )
    assert other.due_at == datetime(2026, 3, 8, 7, tzinfo=UTC)
    assert other.nightly_time == "02:30"
    first = slots(
        "2026-11-01T05:30:00+00:00",
        nightly_time="01:30",
        full_refresh_times=["01:30", "13:00"],
    )
    second = slots(
        "2026-11-01T06:30:00+00:00",
        nightly_time="01:30",
        full_refresh_times=["01:30", "13:00"],
    )
    # The repeated 01:30 of fall-back is one slot, as for a single time.
    assert first[0] == second[0]


def test_single_time_list_matches_the_legacy_nightly_schedule():
    """A list naming only the nightly time keeps every existing slot identity."""
    instant = "2026-09-11T17:23:42+00:00"
    assert slots(instant, full_refresh_times=["02:00"]) == slots(instant)
    assert slots(instant, full_refresh_times=("02:00",)) == slots(instant)


def test_delta_cadence_chooses_the_hour_or_turns_deltas_off():
    """Hourly deltas fall on the UTC hour; "off" leaves only the full slot."""
    full, delta = slots("2026-09-11T17:23:42+00:00", delta_refresh="hourly")
    assert delta.due_at == datetime(2026, 9, 11, 17, tzinfo=UTC)
    assert delta.slot_key != slots("2026-09-11T17:23:42+00:00")[1].slot_key
    (only,) = slots("2026-09-11T17:23:42+00:00", delta_refresh="off")
    assert only == full
    # An hourly full refresh already covers hourly deltas.
    (hourly,) = slots(
        "2026-09-11T17:23:42+00:00", frequency="hourly", delta_refresh="hourly"
    )
    assert hourly.cause == "nightly" and hourly.nightly_time == "02:00"


@pytest.mark.parametrize(
    "times",
    [
        ["08:00", "02:00"],
        ["02:00", "02:00"],
        ["03:00"],
        # The nightly time (02:00 here) must be the earliest listed time.
        ["01:00", "02:00"],
        [],
        "02:00",
        ["02:00", "2:30"],
        ["02:00", None],
        [f"{hour:02d}:00" for hour in range(2, 11)],
    ],
)
def test_noncanonical_time_lists_are_rejected(times):
    """The list must be 1–8 sorted unique canonical times, the nightly one first."""
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42+00:00", full_refresh_times=times)


@pytest.mark.parametrize("delta_refresh", ["weekly", "", None, "daily"])
def test_unknown_delta_cadence_is_refused(delta_refresh):
    """Only the three stored delta cadences are accepted."""
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42+00:00", delta_refresh=delta_refresh)


def test_next_full_uses_the_earliest_upcoming_configured_time():
    """The Admin banner's next full refresh is the next listed local time."""
    times = ["02:00", "14:00"]
    now = datetime.fromisoformat("2026-09-28T16:05:00+00:00")
    assert next_full_at(
        now=now,
        timezone="America/New_York",
        nightly_time="02:00",
        full_refresh_times=times,
    ) == datetime(2026, 9, 28, 18, tzinfo=UTC)
    assert next_full_at(
        now=now.replace(hour=19),
        timezone="America/New_York",
        nightly_time="02:00",
        full_refresh_times=times,
    ) == datetime(2026, 9, 29, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    "frequency,times,delta_refresh,minutes",
    [
        ("daily", ["02:00"], "quarter_hour", 15),
        ("daily", ["02:00"], "hourly", 60),
        ("daily", ["02:00"], "off", 24 * 60),
        ("daily", ["02:00", "14:00"], "off", 12 * 60),
        ("daily", ["02:00", "08:00", "20:00"], "off", 12 * 60),
        ("hourly", ["02:00"], "off", 60),
        ("quarter_hour", ["02:00"], "off", 15),
    ],
)
def test_longest_gap_between_refreshes(frequency, times, delta_refresh, minutes):
    """The staleness check sees the longest wait any schedule leaves."""
    assert longest_gap(
        frequency=frequency, full_refresh_times=times, delta_refresh=delta_refresh
    ) == timedelta(minutes=minutes)
