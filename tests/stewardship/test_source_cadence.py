"""Source slots are deterministic across restarts, DST and bounded catch-up."""

from datetime import UTC, datetime

import pytest

from parishkit.stewardship.source.cadence import due_slots


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
