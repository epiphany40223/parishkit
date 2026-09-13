"""Only promoted pre-read observation boundaries authorize overlapping deltas."""

from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.cursors import delta_dates, refresh_cursor

START = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
DIGEST = "1" * 64


def full_cursor():
    """Build one full attempt without inventing a post-read provider watermark."""
    return refresh_cursor(
        snapshot_id=uuid4(),
        kind="full",
        started_at=START,
        window_digest=DIGEST,
        evidence={"requests": 10},
    )


def test_full_watermark_is_the_manifest_observation_start_in_utc():
    """The latest feed event and time spent loading never advance coverage."""
    identifier = uuid4()
    cursor = refresh_cursor(
        snapshot_id=identifier,
        kind="full",
        started_at=START.astimezone(timezone(timedelta(hours=-4))),
        window_digest=DIGEST,
        evidence={"requests": 10},
    )
    assert cursor["watermark"] == START.isoformat()
    assert cursor["full_started_at"] == START.isoformat()
    assert cursor["full_snapshot_id"] == str(identifier)


def test_delta_overlaps_civil_days_and_keeps_last_full_identity():
    """A subsequent full baseline is not fabricated for a delta-only observation."""
    original = full_cursor()
    next_start = START + timedelta(minutes=15)
    assert delta_dates(original, started_at=next_start, window_digest=DIGEST) == (
        date(2026, 9, 10),
        date(2026, 9, 12),
    )
    cursor = refresh_cursor(
        snapshot_id=uuid4(),
        kind="delta",
        started_at=next_start,
        window_digest=DIGEST,
        evidence={"indications": 0},
        base_cursor=original,
    )
    assert cursor["watermark"] == next_start.isoformat()
    assert cursor["full_started_at"] == original["full_started_at"]
    assert cursor["full_snapshot_id"] == original["full_snapshot_id"]
    assert original["watermark"] == START.isoformat()


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unknown"},
        {"window_digest": "2" * 64},
        {"watermark": None},
        {"watermark": "2026-09-11T12:30:00"},
        {"watermark": (START + timedelta(days=1)).isoformat()},
        {"full_started_at": (START + timedelta(seconds=1)).isoformat()},
        {"full_snapshot_id": "PRIVATE-INVALID-ID"},
        {"full_snapshot_id": None},
        {"watermark": "2026-09-11T08:30:00-04:00"},
    ],
)
def test_ambiguous_cursor_requires_full_without_private_diagnostics(changes):
    """Legacy, mismatched or malformed cursor data is not a successful boundary."""
    with pytest.raises(ChangeFeedIncomplete) as error:
        delta_dates(full_cursor() | changes, started_at=START, window_digest=DIGEST)
    assert "PRIVATE" not in str(error.value)


def test_long_outage_requires_full_instead_of_expanding_a_delta_indefinitely():
    """The seven-day optimization bound still fits the feed's finite date contract."""
    delta_dates(
        full_cursor(), started_at=START + timedelta(days=7), window_digest=DIGEST
    )
    with pytest.raises(ChangeFeedIncomplete):
        delta_dates(
            full_cursor(),
            started_at=START + timedelta(days=7, seconds=1),
            window_digest=DIGEST,
        )


@pytest.mark.parametrize("value", [None, {}, [], {"schema": "source-refresh-v1"}])
def test_missing_durable_cursor_cannot_skip_the_initial_full_load(value):
    """No best-effort timestamp default can hide missed Family changes."""
    with pytest.raises(ChangeFeedIncomplete):
        delta_dates(value, started_at=START, window_digest=DIGEST)


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_id": "not-a-uuid"},
        {"kind": "publication"},
        {"window_digest": "short"},
        {"started_at": START.replace(tzinfo=None)},
        {"evidence": {"amount": 1.1}},
    ],
)
def test_cursor_creation_requires_canonical_owned_inputs(changes):
    """An internal caller cannot create a guessed or lossy scheduling boundary."""
    arguments = dict(
        snapshot_id=uuid4(),
        kind="full",
        started_at=START,
        window_digest=DIGEST,
        evidence={},
    )
    with pytest.raises(InvalidSourcePayload):
        refresh_cursor(**(arguments | changes))
