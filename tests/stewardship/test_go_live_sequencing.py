"""The go-live hold arithmetic, without a database (#462)."""

from datetime import UTC, datetime, timedelta

from parishkit.stewardship.campaigns.go_live_sequencing import (
    ATTEMPT_CAP,
    REFRESH_HOLD,
    hold_end,
    shift,
)

START = datetime(2054, 10, 3, 14, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def test_the_hold_runs_from_the_later_of_promotion_and_cleanup():
    """Both must have happened; the hour runs from whichever came last."""
    promoted, cleaned = START + 10 * MINUTE, START + 25 * MINUTE
    assert hold_end(START) == START + ATTEMPT_CAP
    assert hold_end(START, promoted_at=promoted) == START + ATTEMPT_CAP
    assert (
        hold_end(START, promoted_at=promoted, cleanup_at=cleaned)
        == cleaned + REFRESH_HOLD
    )
    assert (
        hold_end(START, promoted_at=cleaned, cleanup_at=promoted)
        == cleaned + REFRESH_HOLD
    )


def test_the_cap_and_the_request_end_bound_the_hold():
    """Never past the cap; at once when the request ends; never before start."""
    late = START + ATTEMPT_CAP
    assert hold_end(START, promoted_at=late, cleanup_at=late) == START + ATTEMPT_CAP
    ended = START + 5 * MINUTE
    assert hold_end(START, ended_at=ended) == ended
    assert hold_end(START, ended_at=START - MINUTE) == START


def test_shift_chains_through_overlapping_holds_to_a_fixed_point():
    """A slot inside one hold whose end lies in another moves to the last end."""
    first = (START, START + 60 * MINUTE)
    second = (START + 30 * MINUTE, START + 120 * MINUTE)
    third = (START + 100 * MINUTE, START + 150 * MINUTE)
    slot = START + 10 * MINUTE
    # Order of the windows does not matter.
    assert shift(slot, [third, second, first]) == START + 150 * MINUTE
    assert shift(slot, [first]) == START + 60 * MINUTE
    # Outside every hold, or none: unchanged.
    assert shift(START - MINUTE, [first, second]) == START - MINUTE
    assert shift(slot, []) == slot
    assert shift(None, [first]) is None
    # A hold's end is outside it (half-open), so a slot due then is not moved.
    assert shift(START + 60 * MINUTE, [first]) == START + 60 * MINUTE
