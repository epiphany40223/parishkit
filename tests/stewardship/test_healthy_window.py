"""Recovery requires observed continuity, independently of service wall clocks."""

from datetime import UTC, datetime, timedelta

import pytest

from parishkit.stewardship.accounts.healthy_window import advance_window

NOW = datetime(2026, 9, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    "since,previous,healthy,failure,expected,resolved",
    [
        (None, None, True, None, NOW, False),
        (NOW - timedelta(seconds=300), None, True, None, NOW, False),
        (
            NOW - timedelta(seconds=300),
            NOW - timedelta(seconds=30),
            True,
            NOW - timedelta(seconds=400),
            NOW - timedelta(seconds=300),
            True,
        ),
        (
            NOW - timedelta(seconds=300),
            NOW - timedelta(seconds=30),
            True,
            None,
            NOW - timedelta(seconds=300),
            True,
        ),
        (
            NOW - timedelta(seconds=299),
            NOW - timedelta(seconds=30),
            True,
            None,
            NOW - timedelta(seconds=299),
            False,
        ),
        (
            NOW - timedelta(seconds=300),
            NOW - timedelta(seconds=91),
            True,
            None,
            NOW,
            False,
        ),
        (
            NOW - timedelta(seconds=300),
            NOW - timedelta(seconds=30),
            False,
            None,
            None,
            False,
        ),
        (
            NOW - timedelta(seconds=300),
            NOW - timedelta(seconds=30),
            True,
            NOW - timedelta(seconds=20),
            NOW,
            False,
        ),
        (
            NOW - timedelta(seconds=300),
            NOW - timedelta(seconds=30),
            True,
            NOW,
            None,
            False,
        ),
        (
            NOW - timedelta(seconds=300),
            NOW,
            True,
            None,
            NOW - timedelta(seconds=300),
            False,
        ),
    ],
)
def test_observed_window(since, previous, healthy, failure, expected, resolved):
    """Silence, missing baseline, new failures and older samples cannot resolve."""
    assert advance_window(since, previous, NOW, healthy=healthy, failure=failure) == (
        expected,
        resolved,
    )
