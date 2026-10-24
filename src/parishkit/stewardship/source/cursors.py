"""Successful source observation boundaries, not provider log-event watermarks."""

import re
from datetime import UTC, datetime
from uuid import UUID

from .canonical import InvalidSourcePayload, canonical_payload

SCHEMA = "source-refresh-v1"


def _instant(value):
    """Require explicit, representable instants without inferring a server timezone."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise InvalidSourcePayload("A source observation requires an aware instant.")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise InvalidSourcePayload(
            "Source observation instant is out of range."
        ) from None


def _parse(value):
    """Persisted cursor instants use one exact UTC spelling."""
    if type(value) is not str or len(value) > 40:
        raise ValueError("Invalid source cursor instant.")
    instant = _instant(datetime.fromisoformat(value))
    if instant.isoformat() != value:
        raise ValueError("Invalid source cursor instant.")
    return instant


def refresh_cursor(*, snapshot_id, started_at, window_digest, evidence):
    """Describe this exact full observation; only its promoted manifest advances truth.

    ``started_at`` is the database-owned manifest start before any network read,
    not the finish time. Rejected staging keeps this forensic cursor but is never
    a scheduling input. The ``full_snapshot_id`` and ``full_started_at`` fields
    name this observation itself: they once let a quick update (delta refresh,
    removed by #1147) name the full refresh it read on from, and the database's
    manifest guard still requires them.
    """
    if (
        not isinstance(snapshot_id, UUID)
        or type(window_digest) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", window_digest)
    ):
        raise InvalidSourcePayload("Source cursor bindings are invalid.")
    started_at = _instant(started_at)
    canonical_payload(evidence)
    return {
        "schema": SCHEMA,
        "watermark": started_at.isoformat(),
        "window_digest": window_digest,
        "full_snapshot_id": str(snapshot_id),
        "full_started_at": started_at.isoformat(),
        "load": evidence,
    }
