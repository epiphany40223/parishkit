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
    BACKUP_OFFSITE_FAILED = "backup_offsite_failed"
    # The newest backup sealed to a different public key than the one before.
    BACKUP_KEY_CHANGED = "backup_key_changed"
    # Source snapshot retention was skipped by several refreshes in a row.
    SOURCE_RETENTION_FAILING = "source_retention_failing"
    PURGE_INCONSISTENCY = "purge_inconsistency"
    PURGE_CLEANUP_FAILED = "purge_cleanup_failed"
    # Admin automation sessions (ADM-11): fixed text naming no session, label
    # or Family; the detail is in the automation notices on the dashboard.
    AUTOMATION_APPROVED = "automation_approved"
    AUTOMATION_IRREVERSIBLE = "automation_irreversible"
    AUTOMATION_POLICY_CHANGE = "automation_policy_change"
    AUTOMATION_REFUSED = "automation_refused"


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
        IncidentKind.BACKUP_OFFSITE_FAILED: "Off-site backup copy failed",
        IncidentKind.BACKUP_KEY_CHANGED: "Backup encryption key changed",
        IncidentKind.SOURCE_RETENTION_FAILING: "Parish data cleanup keeps failing",
        IncidentKind.PURGE_INCONSISTENCY: "Campaign purge is inconsistent",
        IncidentKind.PURGE_CLEANUP_FAILED: "Campaign purge cleanup failed",
        IncidentKind.AUTOMATION_APPROVED: "An automation session was approved",
        IncidentKind.AUTOMATION_IRREVERSIBLE: (
            "An automation session took an irreversible action"
        ),
        IncidentKind.AUTOMATION_POLICY_CHANGE: (
            "An automation session changed user access, integration keys or "
            "notification settings"
        ),
        IncidentKind.AUTOMATION_REFUSED: "An automation session was refused",
    }
)

# The automation kinds, which the web login may observe and the maintenance
# task resolves after an hour without events (automation_sessions.py).
AUTOMATION_KINDS = (
    IncidentKind.AUTOMATION_APPROVED,
    IncidentKind.AUTOMATION_IRREVERSIBLE,
    IncidentKind.AUTOMATION_POLICY_CHANGE,
    IncidentKind.AUTOMATION_REFUSED,
)
_AUTOMATION_INSTRUCTION = (
    "Review the automation notices on the Admin dashboard. They name the "
    "automation session and what it did."
)
_AUTOMATION_RESOLVED = (
    "No further automation events of this kind in the last hour. Review the "
    "automation notices on the Admin dashboard if you have not already."
)


# What to do, for kinds where the operational log alone cannot say it (the
# evidence is outside the log, or the fix is the operator's). Mirrored word for
# word in stewardship_ops_content_v1; other kinds use the generic sentence.
INSTRUCTIONS = MappingProxyType(
    {
        IncidentKind.BACKUP_KEY_CHANGED: (
            "A backup in the last two days was sealed to a different "
            "encryption key than the backup before it. Unless the server "
            "operator installed a new key on purpose, new backups may not open "
            "with the kept private key. Ask the operator to open the "
            "newest backup with each kept copy of the private key, as the "
            "backup runbook describes."
        ),
        IncidentKind.SOURCE_RETENTION_FAILING: (
            "Removing old ParishSoft copies was skipped by the last three "
            "refreshes, so the database keeps growing. Refreshes still "
            "work. Ask the server operator to check the worker log for "
            "the cause."
        ),
        **dict.fromkeys(AUTOMATION_KINDS, _AUTOMATION_INSTRUCTION),
    }
)


# A resolved notice that must not read as "recovered": the key-change episode
# ends when the change is no longer recent, not when anyone confirmed the key.
# Mirrored word for word in stewardship_ops_content_v1.
RESOLVED_INSTRUCTIONS = MappingProxyType(
    {
        IncidentKind.BACKUP_KEY_CHANGED: (
            "The backup encryption key change is no longer recent. If you "
            "have not already, ask the server operator to confirm that each "
            "kept copy of the private key opens a new backup, as the backup "
            "runbook describes."
        ),
        **dict.fromkeys(AUTOMATION_KINDS, _AUTOMATION_RESOLVED),
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
        RESOLVED_INSTRUCTIONS.get(
            alert.kind,
            "This condition has recovered. "
            "Review the operational log if follow-up is needed.",
        )
        if alert.phase is AlertPhase.RESOLVED
        else INSTRUCTIONS.get(
            alert.kind,
            "Administrator attention is required. "
            "Review the operational log for details.",
        )
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
