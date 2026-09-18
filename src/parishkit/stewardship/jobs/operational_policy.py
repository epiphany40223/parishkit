"""Deterministic incident transitions; database ownership supplies the clock/lock.

Notification decisions mean durable occurrence allocation, not provider success.
An owning transaction must persist the returned state and occurrence together.
Do not use process-local state for deduplication or caller time for admission.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from .operational_content import AlertPhase, IncidentLevel

MAX_OCCURRENCES = 9_223_372_036_854_775_807


def _utc(value):
    """Accept equivalent database UTC objects without inferring a missing zone."""
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("Incident transitions require a UTC instant.")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class IncidentPolicy:
    """Bounded configuration defaults; active state never lives in this policy."""

    suppression_seconds: int = 900
    escalation_seconds: int = 900
    source_stale_seconds: int = 1800

    def __post_init__(self):
        """Prevent zero-delay notification storms and unbounded escalation delays."""
        if any(
            type(value) is not int or not 60 <= value <= 86400
            for value in (
                self.suppression_seconds,
                self.escalation_seconds,
                self.source_stale_seconds,
            )
        ):
            raise ValueError("Operational incident windows must be 60–86,400 seconds.")


@dataclass(frozen=True)
class IncidentState:
    """A non-personal episode snapshot, detached from any ORM instance or recipient."""

    level: IncidentLevel
    first_seen: datetime
    last_seen: datetime
    occurrences: int
    last_notice_at: datetime | None = None
    resolved_at: datetime | None = None

    def __post_init__(self):
        """Reject inconsistent restored/current state before deciding any new effect."""
        if (
            not isinstance(self.level, IncidentLevel)
            or type(self.occurrences) is not int
            or not 1 <= self.occurrences <= MAX_OCCURRENCES
        ):
            raise ValueError("Invalid operational incident state.")
        for name in ("first_seen", "last_seen", "last_notice_at", "resolved_at"):
            value = getattr(self, name)
            if value is not None or name in {"first_seen", "last_seen"}:
                object.__setattr__(self, name, _utc(value))
        end = self.resolved_at or self.last_seen
        if (
            self.first_seen > self.last_seen
            or self.last_seen > end
            or (
                self.last_notice_at is not None
                and not self.first_seen <= self.last_notice_at <= end
            )
            or (self.level is IncidentLevel.CRITICAL and self.last_notice_at is None)
            or (self.level is IncidentLevel.WARNING and self.last_notice_at is not None)
        ):
            raise ValueError("Invalid operational incident ordering.")


@dataclass(frozen=True)
class IncidentDecision:
    """The database owner must persist state and any notification as one effect."""

    state: IncidentState
    notification: AlertPhase | None


def observe_incident(current, level, instant, policy):
    """Count a new observation and decide initial, escalated or suppressed notice.

    Escalation bypasses repeat suppression; continued WARNING observations may
    not downgrade an already critical episode. A recovered condition gets a new
    episode rather than reopening resolved history. The storage owner supplies
    None only after proving no active episode exists under its deduplication lock.
    """
    instant = _utc(instant)
    if not isinstance(level, IncidentLevel) or not isinstance(policy, IncidentPolicy):
        raise TypeError("Typed incident severity and policy are required.")
    if current is None:
        critical = level is IncidentLevel.CRITICAL
        return IncidentDecision(
            IncidentState(level, instant, instant, 1, instant if critical else None),
            AlertPhase.OPENED if critical else None,
        )
    if not isinstance(current, IncidentState):
        raise TypeError("Typed incident state is required.")
    if current.resolved_at is not None or instant < current.last_seen:
        raise ValueError("Incident observations require a current ordered episode.")
    critical = (
        current.level is IncidentLevel.CRITICAL
        or level is IncidentLevel.CRITICAL
        or instant - current.first_seen >= timedelta(seconds=policy.escalation_seconds)
    )
    notification = None
    if critical:
        if current.level is IncidentLevel.WARNING:
            notification = AlertPhase.ESCALATED
        elif instant - current.last_notice_at >= timedelta(
            seconds=policy.suppression_seconds
        ):
            notification = AlertPhase.REPEATED
    state = replace(
        current,
        level=IncidentLevel.CRITICAL if critical else IncidentLevel.WARNING,
        last_seen=instant,
        occurrences=min(MAX_OCCURRENCES, current.occurrences + 1),
        last_notice_at=instant if notification is not None else current.last_notice_at,
    )
    return IncidentDecision(state, notification)


def resolve_incident(current, instant):
    """Emit one recovery only for a previously notified episode; repeats do nothing."""
    instant = _utc(instant)
    if not isinstance(current, IncidentState):
        raise TypeError("Typed incident state is required.")
    if current.resolved_at is not None:
        return IncidentDecision(current, None)
    if instant < current.last_seen:
        raise ValueError("Recovery cannot precede the last incident observation.")
    notified = current.last_notice_at is not None
    return IncidentDecision(
        replace(
            current,
            resolved_at=instant,
            last_notice_at=instant if notified else None,
        ),
        AlertPhase.RESOLVED if notified else None,
    )
