"""The LOCAL fake clock's offset file, mode marker and forward-only jumps (#476)."""

from datetime import UTC, datetime, timedelta

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.local import clock as module
from parishkit.stewardship.local.clock import (
    CACHE_WAIT_SECONDS,
    UP_OFFSET_SECONDS,
    FakeClock,
    fake_now,
    format_offset,
    parse_offset,
    read_offset,
    write_offset,
)

REAL = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


def test_up_offset_is_seventeen_days_in_libfaketime_relative_form():
    """The value the operator script writes at ``up`` (-1468800) is this constant."""
    assert UP_OFFSET_SECONDS == -1_468_800 == -17 * 86400
    assert format_offset(UP_OFFSET_SECONDS) == "-1468800\n"
    assert parse_offset("-1468800\n") == UP_OFFSET_SECONDS
    assert module.CACHE_SECONDS == 1 and CACHE_WAIT_SECONDS == 2


@pytest.mark.parametrize("text", ["0", "1468800", "", " ", "-1.5", "+-1", "abc", "- 1"])
def test_offset_file_must_be_a_signed_whole_number(text):
    """libfaketime's relative form only: a bare number would be an absolute time."""
    with pytest.raises(ConfigError, match="signed whole number"):
        parse_offset(text)


@pytest.mark.parametrize("value", [1.5, "1", True, None])
def test_offset_must_be_whole_seconds(value):
    with pytest.raises(ConfigError, match="whole number of seconds"):
        format_offset(value)


def test_offset_file_round_trips_and_is_replaced_atomically(tmp_path):
    """Written through a sibling and renamed, so a reader never sees a torn file."""
    write_offset(tmp_path, -5)
    assert (tmp_path / "offset").read_text() == "-5\n"
    assert read_offset(tmp_path) == -5
    write_offset(tmp_path, 0)
    assert (tmp_path / "offset").read_text() == "+0\n"
    assert read_offset(tmp_path) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["offset"]


def test_fake_now_shifts_real_time_by_the_offset():
    assert fake_now(UP_OFFSET_SECONDS, real_now=REAL) == REAL - timedelta(days=17)
    assert fake_now(0, real_now=REAL) == REAL
    assert isinstance(fake_now(0), datetime)


def clock_at(tmp_path, offset, *, real=REAL):
    """A FakeClock over ``tmp_path`` with a fixed real time and a recorded sleep."""
    write_offset(tmp_path, offset)
    slept = []
    clock = FakeClock(tmp_path, now=lambda: real, sleep=slept.append)
    return clock, slept


def test_jump_computes_target_minus_real_now_and_waits_out_the_cache(tmp_path):
    """The new offset puts fake time exactly at the target, then the cache wait."""
    clock, slept = clock_at(tmp_path, UP_OFFSET_SECONDS)
    assert clock.real_now() == REAL
    assert clock.now() == REAL - timedelta(days=17)
    target = REAL - timedelta(days=10, hours=6)
    assert clock.jump_to(target) == -(10 * 86400 + 6 * 3600)
    assert read_offset(tmp_path) == -(10 * 86400 + 6 * 3600)
    assert clock.now() == target
    assert slept == [CACHE_WAIT_SECONDS]


def test_jump_is_forward_only_and_never_ahead_of_real_time(tmp_path):
    """A passed instant is not a jump; a future instant is refused outright."""
    clock, slept = clock_at(tmp_path, -3600)
    assert clock.jump_to(REAL - timedelta(hours=2)) is None
    assert clock.jump_to(REAL - timedelta(hours=1)) is None
    assert read_offset(tmp_path) == -3600 and slept == []
    with pytest.raises(ConfigError, match="ahead of real time"):
        clock.jump_to(REAL + timedelta(seconds=1))
    assert read_offset(tmp_path) == -3600
    # Up to real now itself is allowed: offset zero is normal time.
    assert clock.jump_to(REAL) == 0
    assert clock.now() == REAL


def test_jump_requires_an_aware_instant(tmp_path):
    clock, _ = clock_at(tmp_path, -10)
    for target in (datetime(2026, 10, 20, 14, 0), "2026-10-20T14:00:00+00:00", None):
        with pytest.raises(ConfigError, match="aware instant"):
            clock.jump_to(target)


def test_default_real_now_undoes_the_offset_of_a_faked_process(tmp_path, monkeypatch):
    """Inside a faked container datetime.now already carries the offset."""
    write_offset(tmp_path, -3600)
    faked = REAL - timedelta(hours=1)

    class FakedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return faked

    monkeypatch.setattr(module, "datetime", FakedDateTime)
    clock = FakeClock(tmp_path, sleep=lambda s: None)
    assert clock.real_now() == REAL
    assert clock.now() == faked
