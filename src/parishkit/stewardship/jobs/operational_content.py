"""Fixed operational alert content, never an arbitrary-text notification API.

This compiler establishes content shape and privacy, not authorization to send.
The durable incident owner and isolated dispatch consumer must independently
bind the alert to its occurrence, current Admin recipient and configuration.
Campaign templates, Family data, credentials and access URLs have no input slot.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from html import escape
from types import MappingProxyType
from uuid import UUID

from parishkit.stewardship.campaigns.domain import SystemMode


class IncidentKind(StrEnum):
    """Reviewed conditions; later-phase producers do not activate by naming one."""

    DATABASE_UNAVAILABLE = "database_unavailable"
    STORAGE_INTEGRITY = "storage_integrity"
    TASK_FAILED = "task_failed"
    SYSTEM_FAILURE = "system_failure"
    SOURCE_REFRESH_FAILED = "source_refresh_failed"
    SOURCE_STALE = "source_stale"
    SOURCE_TENANT_MISMATCH = "source_tenant_mismatch"
    SOURCE_DESTRUCTIVE_CHANGE = "source_destructive_change"
    MAIL_PROVIDER_UNAVAILABLE = "mail_provider_unavailable"
    SCHEDULER_LAG = "scheduler_lag"
    WORKER_UNAVAILABLE = "worker_unavailable"
    ADMIN_ABUSE = "admin_abuse"
    FAMILY_ABUSE = "family_abuse"
    LIMITER_UNAVAILABLE = "limiter_unavailable"
    LIMITER_STATE_LOST = "limiter_state_lost"
    PUBLICATION_AMBIGUOUS = "publication_ambiguous"
    PRODUCTION_CLEANUP_FAILED = "production_cleanup_failed"
    BACKUP_RPO_BREACH = "backup_rpo_breach"
    PURGE_INCONSISTENCY = "purge_inconsistency"
    PURGE_CLEANUP_FAILED = "purge_cleanup_failed"


class IncidentLevel(StrEnum):
    """Durable incident severity is distinct from delivery outcome."""

    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertPhase(StrEnum):
    """A recovery notification cannot silently masquerade as a fresh failure."""

    OPENED = "opened"
    ESCALATED = "escalated"
    REPEATED = "repeated"
    RESOLVED = "resolved"


TITLES = MappingProxyType(
    {
        IncidentKind.DATABASE_UNAVAILABLE: "Database unavailable",
        IncidentKind.STORAGE_INTEGRITY: "Storage integrity requires attention",
        IncidentKind.TASK_FAILED: "Background task failed",
        IncidentKind.SYSTEM_FAILURE: "System operation requires attention",
        IncidentKind.SOURCE_REFRESH_FAILED: "Parish data refresh failed",
        IncidentKind.SOURCE_STALE: "Parish data refresh is overdue",
        IncidentKind.SOURCE_TENANT_MISMATCH: "Parish data organization mismatch",
        IncidentKind.SOURCE_DESTRUCTIVE_CHANGE: "Unexpected parish data loss",
        IncidentKind.MAIL_PROVIDER_UNAVAILABLE: "Email provider unavailable",
        IncidentKind.SCHEDULER_LAG: "Scheduled work is overdue",
        IncidentKind.WORKER_UNAVAILABLE: "Background worker unavailable",
        IncidentKind.ADMIN_ABUSE: "Sustained administration login abuse",
        IncidentKind.FAMILY_ABUSE: "Sustained Family login abuse",
        IncidentKind.LIMITER_UNAVAILABLE: "Login rate limiter unavailable",
        IncidentKind.LIMITER_STATE_LOST: "Login rate limiter state was lost",
        IncidentKind.PUBLICATION_AMBIGUOUS: "Parish data publication is uncertain",
        IncidentKind.PRODUCTION_CLEANUP_FAILED: "Campaign preparation cleanup failed",
        IncidentKind.BACKUP_RPO_BREACH: "Required backup is overdue",
        IncidentKind.PURGE_INCONSISTENCY: "Campaign purge is inconsistent",
        IncidentKind.PURGE_CLEANUP_FAILED: "Campaign purge cleanup failed",
    }
)


@dataclass(frozen=True)
class OperationalAlert:
    """Only bounded, non-personal incident facts may reach the content compiler."""

    incident_id: UUID
    kind: IncidentKind
    level: IncidentLevel
    phase: AlertPhase
    mode: SystemMode
    first_seen: datetime
    observed_at: datetime
    occurrences: int

    def __post_init__(self):
        """Reject untyped/free-text values and impossible counters or UTC times."""
        if (
            not isinstance(self.incident_id, UUID)
            or not isinstance(self.kind, IncidentKind)
            or not isinstance(self.level, IncidentLevel)
            or not isinstance(self.phase, AlertPhase)
            or not isinstance(self.mode, SystemMode)
            or type(self.occurrences) is not int
            or not 1 <= self.occurrences <= 9_223_372_036_854_775_807
        ):
            raise ValueError("Invalid operational alert facts.")
        for name in ("first_seen", "observed_at"):
            instant = getattr(self, name)
            if not isinstance(instant, datetime) or instant.utcoffset() != timedelta(0):
                raise ValueError("Operational alert times must be canonical UTC.")
            object.__setattr__(self, name, instant.astimezone(UTC))
        if self.observed_at < self.first_seen or (
            self.phase is AlertPhase.ESCALATED
            and self.level is not IncidentLevel.CRITICAL
        ):
            raise ValueError("Invalid operational alert state.")


@dataclass(frozen=True)
class OperationalContent:
    """Compiled alternatives contain no recipient, routing flag or mutable template."""

    subject: str
    html: str
    text: str


def render_alert(alert):
    """Render one fixed-content alert; mode is visible but never changes routing."""
    if not isinstance(alert, OperationalAlert):
        raise TypeError("Typed operational alert facts are required.")
    title = TITLES[alert.kind]
    status = "RESOLVED" if alert.phase is AlertPhase.RESOLVED else alert.level.value
    subject = f"[{alert.mode.value.upper()}] {status}: {title}"
    instruction = (
        "This condition has recovered. "
        "Review the operational log if follow-up is needed."
        if alert.phase is AlertPhase.RESOLVED
        else "Administrator attention is required. "
        "Review the operational log for details."
    )
    rows = (
        ("Status", status),
        ("Notification", alert.phase.value),
        ("Deployment mode", alert.mode.value.title()),
        ("First observed", alert.first_seen.strftime("%m/%d/%Y %H:%M:%S UTC")),
        ("Latest observation", alert.observed_at.strftime("%m/%d/%Y %H:%M:%S UTC")),
        ("Occurrences", f"{alert.occurrences:,}"),
        ("Incident reference", str(alert.incident_id)),
    )
    text = title + "\n\n" + instruction + "\n\n"
    text += "\n".join(f"{label}: {value}" for label, value in rows)
    html = f"<h2>{escape(title)}</h2><p>{escape(instruction)}</p><dl>"
    html += "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows
    )
    return OperationalContent(subject, html + "</dl>", text)
