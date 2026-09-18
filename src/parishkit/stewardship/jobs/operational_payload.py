"""Closed private-pipe facts, never serialized HTML, templates or arbitrary text."""

from datetime import datetime
from uuid import UUID

from parishkit.stewardship.campaigns.domain import SystemMode

from .operational_content import (
    AlertPhase,
    IncidentKind,
    IncidentLevel,
    OperationalAlert,
)


def alert_payload(alert):
    """Serialize only facts already validated by the operational content compiler."""
    if not isinstance(alert, OperationalAlert):
        raise ValueError("Typed operational alert facts are required.")
    return {
        "incident_id": str(alert.incident_id),
        "kind": alert.kind.value,
        "level": alert.level.value,
        "phase": alert.phase.value,
        "mode": alert.mode.value,
        "first_seen": alert.first_seen.isoformat(),
        "observed_at": alert.observed_at.isoformat(),
        "occurrences": alert.occurrences,
    }


def decode_alert(value):
    """Reject alternative encodings and private extras without echoing bad values."""
    if type(value) is not dict or set(value) != {
        "incident_id",
        "kind",
        "level",
        "phase",
        "mode",
        "first_seen",
        "observed_at",
        "occurrences",
    }:
        raise ValueError("Invalid operational alert payload.")
    try:
        alert = OperationalAlert(
            UUID(value["incident_id"]),
            IncidentKind(value["kind"]),
            IncidentLevel(value["level"]),
            AlertPhase(value["phase"]),
            SystemMode(value["mode"]),
            datetime.fromisoformat(value["first_seen"]),
            datetime.fromisoformat(value["observed_at"]),
            value["occurrences"],
        )
        if alert_payload(alert) != value:
            raise ValueError("Noncanonical operational facts.")
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid operational alert payload.") from None
    return alert


def canonical_id(value):
    """Permit one exact UUID encoding, never caller-specified keys or paths."""
    try:
        if type(value) is not str:
            raise ValueError("Invalid identifier.")
        identifier = UUID(value)
        if str(identifier) != value:
            raise ValueError("Noncanonical identifier.")
        return identifier
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid operational delivery identity.") from None
