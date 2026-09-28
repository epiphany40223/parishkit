"""Bounded source-refresh slots with stable identities and canonical DST rules."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from parishkit.stewardship.campaigns.intervals import resolve_local

from .canonical import canonical_payload

SLOT_NAMESPACE = UUID("ec9c1e19-b158-454d-96fd-1cfe6e721d11")
# How often a scheduled full refresh runs. "daily" uses the configured local
# time; the others use UTC hour or quarter-hour boundaries. The scheduled full
# cause stays "nightly" in stored commands and slot identities.
FREQUENCIES = ("daily", "hourly", "quarter_hour")


@dataclass(frozen=True)
class RefreshSlot:
    """An exact due slot; its request may coalesce but its provenance does not."""

    cause: str
    due_at: datetime
    slot_key: str
    command_id: UUID


def due_slots(*, now, timezone, nightly_time, scope_fingerprint, frequency="daily"):
    """Select the latest full-refresh and 15-minute slot, never an unbounded backlog.

    ``frequency`` chooses the full refresh's slot: the latest local
    ``nightly_time`` for "daily", else the latest UTC hour or quarter hour.

    Full comes first so a simultaneously due delta can coalesce into it. After
    downtime, one current coherent observation covers stale refresh slots;
    unlike delivery obligations, missed read-only polls need no individual replay.
    Identities ignore unrelated configuration edits but include the actual source
    scope, timezone and cadence. Persist them before emitting broker hints.
    """
    if (
        type(now) is not datetime
        or now.tzinfo is None
        or now.utcoffset() is None
        or type(timezone) is not str
        or type(nightly_time) is not str
        or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", nightly_time) is None
        or type(scope_fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", scope_fingerprint) is None
        or frequency not in FREQUENCIES
    ):
        raise ValueError("Refresh scheduling requires canonical scope and time inputs.")
    now = now.astimezone(UTC)
    local = now.astimezone(ZoneInfo(timezone))
    wall = time.fromisoformat(nightly_time)
    day = local.date()
    nightly = resolve_local(datetime.combine(day, wall), timezone)
    if nightly > now:
        nightly = resolve_local(
            datetime.combine(day - timedelta(days=1), wall), timezone
        )
    delta = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    if frequency == "hourly":
        nightly = now.replace(minute=0, second=0, microsecond=0)
    elif frequency == "quarter_hour":
        nightly = delta
    slots = [("nightly", nightly)]
    if frequency != "quarter_hour":
        # A full refresh every quarter hour already covers each delta slot.
        slots.append(("delta", delta))
    result = []
    for cause, due in slots:
        _, key = canonical_payload(
            {
                "schema": "source-refresh-slot-v1",
                "scope_fingerprint": scope_fingerprint,
                "timezone": timezone,
                "nightly_time": nightly_time if cause == "nightly" else None,
                "cause": cause,
                "due_at": due.isoformat(),
            }
        )
        result.append(RefreshSlot(cause, due, key, uuid5(SLOT_NAMESPACE, key)))
    return tuple(result)
