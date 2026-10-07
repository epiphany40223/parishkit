"""Source slots are deterministic across restarts, DST and bounded catch-up."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.source.cadence import (
    catch_up_slot,
    due_slots,
    longest_gap,
    next_full_at,
)


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
        # More than eight times: only a schedule saved with its rules may.
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


def test_longest_gap_counts_listed_quick_times():
    """With listed quick times the gap is the longest between any two times."""
    assert longest_gap(
        frequency="daily",
        full_refresh_times=["02:00"],
        delta_refresh="times",
        quick_refresh_times=["06:00", "18:00"],
    ) == timedelta(hours=12)
    # Quick times listed beside "off" would be a malformed document; they are
    # not counted.
    assert longest_gap(
        frequency="daily",
        full_refresh_times=["02:00"],
        delta_refresh="off",
        quick_refresh_times=["06:00"],
    ) == timedelta(hours=24)


QUARTER = timedelta(minutes=15)
HOURLY_QUICK = [f"{hour:02d}:00" for hour in range(24) if hour != 2]


def test_listed_quick_times_choose_the_latest_local_time():
    """The quick slot is the latest listed local time due, resolved like full ones."""
    full, quick = slots(
        "2026-09-11T17:23:42+00:00",
        delta_refresh="times",
        rules=True,
        quick_refresh_times=HOURLY_QUICK,
    )
    assert full == slots("2026-09-11T17:23:42+00:00")[0]
    assert quick.cause == "delta" and quick.nightly_time is None
    # 13:23 EDT: the 13:00 quick time, 17:00 UTC.
    assert quick.due_at == datetime(2026, 9, 11, 17, tzinfo=UTC)
    # Just after midnight local time, yesterday's 23:00 is the latest.
    _, early = slots(
        "2026-09-11T04:30:00+00:00",
        delta_refresh="times",
        rules=True,
        quick_refresh_times=["08:00", "23:00"],
    )
    assert early.due_at == datetime(2026, 9, 11, 3, tzinfo=UTC)


def test_a_listed_quick_slot_keeps_the_old_quarter_hour_identity():
    """At a local quarter hour the quick slot is the old quarter-hour delta slot.

    In a zone whose offset is a whole number of quarter hours, so the
    stored schedule can switch without running one slot twice (#632).
    """
    every_quarter = [
        f"{minute // 60:02d}:{minute % 60:02d}"
        for minute in range(0, 24 * 60, 15)
        if minute != 120
    ]
    for instant in (
        "2026-09-11T17:23:42+00:00",
        "2026-09-11T17:30:00+00:00",
        "2026-12-24T23:59:59+00:00",
    ):
        listed = slots(
            instant,
            delta_refresh="times",
            rules=True,
            quick_refresh_times=every_quarter,
        )[1]
        assert listed == slots(instant)[1]
    # Kolkata is UTC+05:30: still whole quarter hours.
    listed = slots(
        "2026-09-11T17:23:42+00:00",
        timezone="Asia/Kolkata",
        delta_refresh="times",
        rules=True,
        quick_refresh_times=every_quarter,
    )[1]
    assert listed == slots("2026-09-11T17:23:42+00:00", timezone="Asia/Kolkata")[1]


def test_listed_quick_times_follow_daylight_saving_days():
    """A repeated quick time runs once; times in the missing hour coincide."""
    quick = ["00:00", "01:00", "01:30", "02:15", "02:30", "03:00"]
    common = {"nightly_time": "12:00", "delta_refresh": "times", "rules": True}
    # Fall back (2026-11-01): 01:30 EDT is 05:30 UTC; the repeated 01:30
    # EST (06:30 UTC) is not a new slot: 01:30 keeps its earlier instant.
    first = slots("2026-11-01T05:45:00+00:00", quick_refresh_times=quick, **common)[1]
    second = slots("2026-11-01T06:45:00+00:00", quick_refresh_times=quick, **common)[1]
    assert first.due_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second == first
    # Spring forward (2026-03-08): 02:15 and 02:30 do not exist and resolve to
    # 03:00 EDT (07:00 UTC), where 03:00 also falls: one slot.
    gap = slots("2026-03-08T07:05:00+00:00", quick_refresh_times=quick, **common)[1]
    assert gap.due_at == datetime(2026, 3, 8, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    "settings",
    [
        {"delta_refresh": "times"},
        {"delta_refresh": "times", "quick_refresh_times": []},
        {"delta_refresh": "off", "quick_refresh_times": ["06:00"]},
        {"delta_refresh": "times", "quick_refresh_times": ["02:00"]},
        {"delta_refresh": "times", "quick_refresh_times": ["07:00", "06:00"]},
        {
            "frequency": "hourly",
            "delta_refresh": "times",
            "quick_refresh_times": ["06:00"],
        },
    ],
)
def test_malformed_listed_quick_times_are_refused(settings):
    """Listed quick times need "times", daily full times and no full time among them."""
    with pytest.raises(ValueError):
        slots("2026-09-11T17:23:42+00:00", rules=True, **settings)


def test_listed_quick_times_need_a_schedule_saved_with_its_rules():
    """Without its rules a schedule behaves as before #632: no listed quick times."""
    with pytest.raises(ValueError):
        slots(
            "2026-09-11T17:23:42+00:00",
            delta_refresh="times",
            quick_refresh_times=["06:00"],
        )


def test_more_than_eight_full_times_are_scheduled():
    """The cap of eight is gone (#632); every quarter hour is the limit."""
    times = [f"{hour:02d}:00" for hour in range(24)]
    full, _ = slots(
        "2026-09-11T17:23:42+00:00",
        nightly_time="00:00",
        full_refresh_times=times,
        rules=True,
    )
    # Without its rules a schedule keeps the old limit of eight.
    with pytest.raises(ValueError):
        slots(
            "2026-09-11T17:23:42+00:00", nightly_time="00:00", full_refresh_times=times
        )
    assert full.nightly_time == "13:00"
    assert full.due_at == datetime(2026, 9, 11, 17, tzinfo=UTC)


def test_catch_up_slot_is_due_when_the_schedule_took_effect():
    """The catch-up has its own identity, due at the whole second, with no time."""
    effective = datetime(2026, 10, 6, 14, 5, 12, 345678, tzinfo=UTC)
    slot = catch_up_slot(
        effective_at=effective, timezone="America/New_York", scope_fingerprint="a" * 64
    )
    assert slot.cause == "catch_up" and slot.nightly_time is None
    assert slot.due_at == datetime(2026, 10, 6, 14, 5, 12, tzinfo=UTC)
    again = catch_up_slot(
        effective_at=effective.replace(microsecond=0),
        timezone="America/New_York",
        scope_fingerprint="a" * 64,
    )
    assert again == slot
    other = catch_up_slot(
        effective_at=effective, timezone="America/New_York", scope_fingerprint="b" * 64
    )
    assert other.slot_key != slot.slot_key
    with pytest.raises(ValueError):
        catch_up_slot(
            effective_at=effective.replace(tzinfo=None),
            timezone="America/New_York",
            scope_fingerprint="a" * 64,
        )


def legacy_due_slots(
    *, now, timezone, nightly_time, scope_fingerprint, frequency, times, delta_refresh
):
    """The scheduler's slot choice before #632, frozen here as the parity reference.

    A copy of ``cadence.due_slots`` as released in v1.4.0, reduced to valid
    inputs: an existing stored schedule must keep exactly these slots. It
    imports the live ``resolve_local``, the shared daylight-saving resolver,
    which this change does not touch.
    """
    from uuid import uuid5
    from zoneinfo import ZoneInfo

    from parishkit.stewardship.campaigns.intervals import resolve_local
    from parishkit.stewardship.source.cadence import SLOT_NAMESPACE
    from parishkit.stewardship.source.canonical import canonical_payload

    hour, quarter = timedelta(hours=1), timedelta(minutes=15)

    def floor(step):
        size = int(step.total_seconds()) // 60
        return now.replace(minute=now.minute // size * size, second=0, microsecond=0)

    now = now.astimezone(UTC)
    if frequency == "daily":
        day = now.astimezone(ZoneInfo(timezone)).date()
        due, _, chosen = max(
            (instant, value == nightly_time, value)
            for value in times
            for instant in (
                resolve_local(
                    datetime.combine(
                        day + timedelta(days=offset),
                        datetime.strptime(value, "%H:%M").time(),
                    ),
                    timezone,
                )
                for offset in (-1, 0)
            )
            if instant <= now
        )
    else:
        due, chosen = floor(hour if frequency == "hourly" else quarter), nightly_time
    result = [("nightly", due, chosen)]
    covered = frequency == "quarter_hour" or (
        frequency == "hourly" and delta_refresh == "hourly"
    )
    if delta_refresh != "off" and not covered:
        result.append(
            ("delta", floor(hour if delta_refresh == "hourly" else quarter), None)
        )
    out = []
    for cause, instant, value in result:
        _, key = canonical_payload(
            {
                "schema": "source-refresh-slot-v1",
                "scope_fingerprint": scope_fingerprint,
                "timezone": timezone,
                "nightly_time": value,
                "cause": cause,
                "due_at": instant.isoformat(),
            }
        )
        out.append((cause, instant, key, uuid5(SLOT_NAMESPACE, key), value))
    return out


LEGACY_SCHEDULES = [
    ("daily", ["02:00"], "quarter_hour"),
    ("daily", ["02:00"], "hourly"),
    ("daily", ["02:00"], "off"),
    (
        "daily",
        ["00:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"],
        "quarter_hour",
    ),
    ("daily", ["01:30", "02:10", "13:00"], "hourly"),
    ("hourly", ["02:00"], "quarter_hour"),
    ("hourly", ["02:00"], "hourly"),
    ("quarter_hour", ["02:00"], "off"),
]


def assert_parity(now, timezone, frequency, times, delta_refresh):
    """The live scheduler and the frozen reference agree at ``now``."""
    new = due_slots(
        now=now,
        timezone=timezone,
        nightly_time=times[0],
        scope_fingerprint="c" * 64,
        frequency=frequency,
        full_refresh_times=times,
        delta_refresh=delta_refresh,
    )
    old = legacy_due_slots(
        now=now,
        timezone=timezone,
        nightly_time=times[0],
        scope_fingerprint="c" * 64,
        frequency=frequency,
        times=times,
        delta_refresh=delta_refresh,
    )
    assert [
        (s.cause, s.due_at, s.slot_key, s.command_id, s.nightly_time) for s in new
    ] == old, now


DST_DAYS = (date(2026, 3, 8), date(2026, 11, 1), date(2025, 11, 2))


@pytest.mark.parametrize("frequency,times,delta_refresh", LEGACY_SCHEDULES)
@pytest.mark.parametrize("timezone", ["America/New_York", "Asia/Kolkata"])
def test_existing_schedules_keep_identical_slots_at_every_boundary(
    frequency, times, delta_refresh, timezone
):
    """At every boundary of the daylight-saving days, and just before it.

    Each UTC quarter hour from the day before a change to the day after,
    exactly and one microsecond earlier, and each listed full time resolved
    on those local days, exactly and one microsecond earlier: the instants
    where a slot changes. The reference imports the live ``resolve_local``
    (the shared resolver is not part of this change), so this proves the
    slot choice, not the resolver.
    """
    for day in DST_DAYS:
        start = datetime.combine(day - timedelta(days=1), time(), tzinfo=UTC)
        instants = [start + QUARTER * step for step in range(3 * 96)]
        for offset in (-1, 0, 1):
            for value in times:
                instants.append(
                    resolve_local(
                        datetime.combine(
                            day + timedelta(days=offset), time.fromisoformat(value)
                        ),
                        timezone,
                    )
                )
        for instant in instants:
            for now in (instant, instant - timedelta(microseconds=1)):
                assert_parity(now, timezone, frequency, times, delta_refresh)


@pytest.mark.parametrize("frequency,times,delta_refresh", LEGACY_SCHEDULES)
@pytest.mark.parametrize("timezone", ["America/New_York", "Asia/Kolkata"])
def test_existing_schedules_keep_identical_slots(
    frequency, times, delta_refresh, timezone
):
    """A parity sweep: every existing schedule gets the slots it got before #632.

    Every 37 minutes over the weeks around both 2026 daylight-saving changes
    and an ordinary week: same causes, due times, slot keys, command
    identities and recorded times. The reference imports the live
    ``resolve_local``; the boundary test below samples the exact instants.
    """
    starts = (
        datetime(2026, 3, 5, tzinfo=UTC),
        datetime(2026, 6, 10, tzinfo=UTC),
        datetime(2026, 10, 29, tzinfo=UTC),
    )
    for start in starts:
        for step in range(0, 7 * 24 * 60, 37):
            now = start + timedelta(minutes=step, seconds=step % 60)
            new = due_slots(
                now=now,
                timezone=timezone,
                nightly_time=times[0],
                scope_fingerprint="c" * 64,
                frequency=frequency,
                full_refresh_times=times,
                delta_refresh=delta_refresh,
            )
            old = legacy_due_slots(
                now=now,
                timezone=timezone,
                nightly_time=times[0],
                scope_fingerprint="c" * 64,
                frequency=frequency,
                times=times,
                delta_refresh=delta_refresh,
            )
            assert [
                (s.cause, s.due_at, s.slot_key, s.command_id, s.nightly_time)
                for s in new
            ] == old, now
