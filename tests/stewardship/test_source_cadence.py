"""Source slots are deterministic across restarts, DST and bounded catch-up."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.source.cadence import (
    catch_up_slot,
    due_slots,
    listed_due_slots,
    longest_gap,
    next_full_at,
    refresh_settings,
    schedule_identity,
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


def test_latest_due_slot_is_bounded_and_only_full():
    """Downtime selects one latest full refresh, not all historical read slots.

    Quick updates were removed (#1147): no quick slot is ever created.
    """
    (full,) = slots("2026-09-11T17:23:42+00:00")
    assert full.cause == "nightly"
    assert full.due_at == datetime(2026, 9, 11, 6, tzinfo=UTC)
    assert (full,) == slots("2026-09-11T17:29:59+00:00")
    assert len(slots("2028-01-01T17:23:42+00:00")) == 1


def test_before_nightly_time_selects_previous_local_day():
    """A restart does not produce a future nightly observation early."""
    (full,) = slots("2026-09-11T05:59:59+00:00")
    assert full.due_at == datetime(2026, 9, 10, 6, tzinfo=UTC)


def test_gap_uses_first_real_instant_and_fold_has_one_nightly_identity():
    """The shared resolver supplies exact gap/fold behavior, not arithmetic guesses."""
    (full,) = slots("2026-03-08T07:00:00+00:00")
    assert full.due_at == datetime(2026, 3, 8, 7, tzinfo=UTC)
    first = slots("2026-11-01T05:30:00+00:00", nightly_time="01:30")
    second = slots("2026-11-01T06:30:00+00:00", nightly_time="01:30")
    assert first == second
    assert first[0].due_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_new_scope_or_schedule_gets_a_new_slot():
    """Slot identity changes only when the slot's actual meaning changes."""
    instant = "2026-09-11T17:23:42+00:00"
    original = slots(instant)
    assert original != slots(instant, scope_fingerprint="b" * 64)
    assert original != slots(instant, nightly_time="03:00")


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


def test_hourly_full_refresh_uses_the_latest_utc_hour():
    """An hourly full load is due on the hour."""
    (full,) = slots("2026-09-11T17:23:42+00:00", frequency="hourly")
    assert full.cause == "nightly"
    assert full.due_at == datetime(2026, 9, 11, 17, tzinfo=UTC)
    assert (
        full.slot_key
        != slots("2026-09-11T18:00:00+00:00", frequency="hourly")[0].slot_key
    )


def test_quarter_hour_full_refresh_uses_the_latest_utc_quarter_hour():
    """A full load every 15 minutes is due on each UTC quarter hour."""
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
    (full,) = slots("2026-09-11T17:23:42+00:00", full_refresh_times=times)
    # 13:23 EDT: the 08:00 refresh is the latest due (12:00 UTC).
    assert full.due_at == datetime(2026, 9, 11, 12, tzinfo=UTC)
    assert full.nightly_time == "08:00"
    (later,) = slots("2026-09-11T18:00:00+00:00", full_refresh_times=times)
    assert later.due_at == datetime(2026, 9, 11, 18, tzinfo=UTC)
    assert later.nightly_time == "14:00" and later.slot_key != full.slot_key
    # The time is part of the identity: the nightly slot of the same day differs.
    assert full.slot_key != slots("2026-09-11T17:23:42+00:00")[0].slot_key


def test_before_the_first_time_selects_the_previous_days_last_time():
    """Just after midnight the latest due refresh is yesterday's evening one."""
    (full,) = slots("2026-09-11T05:00:00+00:00", full_refresh_times=["02:00", "20:00"])
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
    (full,) = slots("2026-03-08T07:00:00+00:00", full_refresh_times=times)
    assert full.due_at == datetime(2026, 3, 8, 7, tzinfo=UTC)
    assert full.nightly_time == "02:00"
    # 2027 springs forward on March 14; the same rule holds.
    (later,) = slots("2027-03-14T07:00:00+00:00", full_refresh_times=times)
    assert later.due_at == datetime(2027, 3, 14, 7, tzinfo=UTC)
    assert later.nightly_time == "02:00"
    (earlier,) = slots("2026-03-08T06:59:59+00:00", full_refresh_times=times)
    # The day before, 02:30 EST is 07:30 UTC.
    assert earlier.due_at == datetime(2026, 3, 7, 7, 30, tzinfo=UTC)
    assert earlier.nightly_time == "02:30"
    (other,) = slots(
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


def test_a_stored_quick_setting_creates_no_quick_slot():
    """Quick updates were removed (#1147): a stored quick setting is ignored.

    ``refresh_settings`` drops it, so the scheduler, which passes on only
    what it returns, creates the full slot alone.
    """
    instant = "2026-09-11T17:23:42+00:00"
    for stored in (
        {},
        {"delta_refresh": "quarter_hour"},
        {"delta_refresh": "hourly"},
        {
            "delta_refresh": "times",
            "quick_refresh_times": ["06:00", "13:00"],
            "full_refresh_times": ["02:00"],
            "refresh_rules": {},
        },
    ):
        schedule = refresh_settings({"nightly_time": "02:00", **stored})
        assert "delta_refresh" not in schedule
        assert "quick_refresh_times" not in schedule
        (only,) = slots(instant, **schedule)
        assert only.cause == "nightly"


def test_schedule_identity_keeps_the_tick_guards_quick_defaults():
    """The catch-up's schedule identity matches the SQL normalization.

    The tick guard (``stewardship_refresh_schedule_v1``) still compares the
    removed quick settings, with the old quarter-hour default, so a change of
    only those settings is a new schedule there and must be here too.
    """
    absent = schedule_identity({"nightly_time": "02:00"})
    assert absent["delta_refresh"] == "quarter_hour"
    assert absent["quick_refresh_times"] == ()
    assert absent == schedule_identity(
        {"nightly_time": "02:00", "delta_refresh": "quarter_hour"}
    )
    assert absent != schedule_identity(
        {"nightly_time": "02:00", "delta_refresh": "off"}
    )
    assert refresh_settings({"nightly_time": "02:00"}) == refresh_settings(
        {"nightly_time": "02:00", "delta_refresh": "off"}
    )


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
    "frequency,times,minutes",
    [
        ("daily", ["02:00"], 24 * 60),
        ("daily", ["02:00", "14:00"], 12 * 60),
        ("daily", ["02:00", "08:00", "20:00"], 12 * 60),
        ("hourly", ["02:00"], 60),
        ("quarter_hour", ["02:00"], 15),
    ],
)
def test_longest_gap_between_refreshes(frequency, times, minutes):
    """The staleness check sees the longest wait any schedule leaves."""
    assert longest_gap(frequency=frequency, full_refresh_times=times) == timedelta(
        minutes=minutes
    )


QUARTER = timedelta(minutes=15)


def test_listed_due_slots_are_every_full_time_of_today_and_yesterday():
    """Each listed full time due today or yesterday, oldest first, once each."""
    found = listed_due_slots(
        now=datetime.fromisoformat("2026-09-11T17:23:42+00:00"),
        timezone="America/New_York",
        nightly_time="02:00",
        scope_fingerprint="a" * 64,
        full_refresh_times=["02:00", "08:00", "20:00"],
    )
    assert [(slot.nightly_time, slot.due_at) for slot in found] == [
        ("02:00", datetime(2026, 9, 10, 6, tzinfo=UTC)),
        ("08:00", datetime(2026, 9, 10, 12, tzinfo=UTC)),
        ("20:00", datetime(2026, 9, 11, 0, tzinfo=UTC)),
        ("02:00", datetime(2026, 9, 11, 6, tzinfo=UTC)),
        ("08:00", datetime(2026, 9, 11, 12, tzinfo=UTC)),
    ]
    assert {slot.cause for slot in found} == {"nightly"}
    assert (
        found[-1]
        == slots(
            "2026-09-11T17:23:42+00:00",
            full_refresh_times=["02:00", "08:00", "20:00"],
            rules=True,
        )[0]
    )


def test_more_than_eight_full_times_are_scheduled():
    """The cap of eight is gone (#632); every quarter hour is the limit."""
    times = [f"{hour:02d}:00" for hour in range(24)]
    (full,) = slots(
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
    inputs: an existing stored schedule must keep exactly these full slots
    (its quick slots are gone since #1147). It imports the live
    ``resolve_local``, the shared daylight-saving resolver, which this
    change does not touch.
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


def full_only(slots):
    """The reference's slots less its quick ("delta") ones (#1147)."""
    return [slot for slot in slots if slot[0] != "delta"]


def assert_parity(now, timezone, frequency, times, delta_refresh):
    """The live scheduler and the frozen reference's full slot agree at ``now``."""
    new = due_slots(
        now=now,
        timezone=timezone,
        nightly_time=times[0],
        scope_fingerprint="c" * 64,
        frequency=frequency,
        full_refresh_times=times,
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
    ] == full_only(old), now


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
    """A parity sweep: every existing schedule gets the full slots it got before #632.

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
            ] == full_only(old), now
