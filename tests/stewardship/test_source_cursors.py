"""A full refresh's cursor records its own pre-read observation boundary."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.cursors import refresh_cursor

START = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
DIGEST = "1" * 64


def test_full_watermark_is_the_manifest_observation_start_in_utc():
    """Time spent loading never advances coverage; the cursor names itself."""
    identifier = uuid4()
    cursor = refresh_cursor(
        snapshot_id=identifier,
        started_at=START.astimezone(timezone(timedelta(hours=-4))),
        window_digest=DIGEST,
        evidence={"requests": 10},
    )
    assert cursor == {
        "schema": "source-refresh-v1",
        "watermark": START.isoformat(),
        "window_digest": DIGEST,
        "full_snapshot_id": str(identifier),
        "full_started_at": START.isoformat(),
        "load": {"requests": 10},
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_id": "not-a-uuid"},
        {"window_digest": "short"},
        {"started_at": START.replace(tzinfo=None)},
        {"evidence": {"amount": 1.1}},
    ],
)
def test_cursor_creation_requires_canonical_owned_inputs(changes):
    """An internal caller cannot create a guessed or lossy scheduling boundary."""
    arguments = dict(
        snapshot_id=uuid4(),
        started_at=START,
        window_digest=DIGEST,
        evidence={},
    )
    with pytest.raises(InvalidSourcePayload):
        refresh_cursor(**(arguments | changes))
