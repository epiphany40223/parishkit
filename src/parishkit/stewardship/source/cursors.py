"""Successful source observation boundaries, not provider log-event watermarks."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from parishkit.parishsoft_changes import ChangeFeedIncomplete

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


def delta_dates(cursor, *, started_at, window_digest):
    """Overlap UTC boundary days and fall back to full on stale/ambiguous coverage.

    The feed's timestamp/date-filter timezone is not documented. One day on
    either side covers every civil UTC offset. No indication is discarded as
    supposedly older than the durable boundary. Nightly full refresh remains
    the complete source path; a gap longer than seven days requires it first.
    """
    started_at = _instant(started_at)
    try:
        if (
            type(cursor) is not dict
            or cursor.get("schema") != SCHEMA
            or cursor.get("window_digest") != window_digest
        ):
            raise ValueError("Invalid source cursor binding.")
        boundary = _parse(cursor["watermark"])
        full = _parse(cursor["full_started_at"])
        UUID(cursor["full_snapshot_id"])
        if not full <= boundary <= started_at or started_at - boundary > timedelta(
            days=7
        ):
            raise ValueError("Invalid source cursor ordering.")
        return boundary.date() - timedelta(days=1), started_at.date() + timedelta(
            days=1
        )
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError):
        raise ChangeFeedIncomplete(
            "The source cursor requires a complete refresh."
        ) from None


def refresh_cursor(
    *, snapshot_id, kind, started_at, window_digest, evidence, base_cursor=None
):
    """Describe this exact attempt; only its promoted manifest advances truth.

    ``started_at`` is the database-owned manifest start before any network read,
    not the finish time or the latest event seen in an incomplete Family feed.
    Rejected staging keeps this forensic cursor but is never a scheduling input.
    """
    if (
        not isinstance(snapshot_id, UUID)
        or kind not in {"full", "delta"}
        or type(window_digest) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", window_digest)
    ):
        raise InvalidSourcePayload("Source cursor bindings are invalid.")
    started_at = _instant(started_at)
    canonical_payload(evidence)
    if kind == "full":
        full_id, full_started = str(snapshot_id), started_at.isoformat()
    else:
        delta_dates(base_cursor, started_at=started_at, window_digest=window_digest)
        full_id, full_started = (
            base_cursor["full_snapshot_id"],
            base_cursor["full_started_at"],
        )
    return {
        "schema": SCHEMA,
        "watermark": started_at.isoformat(),
        "window_digest": window_digest,
        "full_snapshot_id": full_id,
        "full_started_at": full_started,
        "load": evidence,
    }
