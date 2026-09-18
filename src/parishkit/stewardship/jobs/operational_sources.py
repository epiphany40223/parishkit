"""Compiled producer classification, never caller-authored notification content."""

from django.conf import settings

from parishkit.stewardship.observability import Event

from .operational_content import IncidentKind, IncidentLevel
from .operational_policy import IncidentPolicy
from .operational_storage import record_observation


def configured_policy():
    """Online startup supplies validated YAML; standalone tests use safe defaults."""
    value = getattr(settings, "STEWARDSHIP_OPERATIONAL_POLICY", IncidentPolicy())
    if not isinstance(value, IncidentPolicy):
        raise ValueError("Operational notification policy is not configured.")
    return value


def critical_log(event):
    """Retain every typed CRITICAL log as fixed-content notification intent.

    The collector consumes each immutable log once in its own fenced transaction.
    No provider errors, free-form context or subject IDs are
    copied to the notification. Generic typed critical events remain visible even
    when their later-phase owner has not supplied a more specific classification.
    """
    if not isinstance(event, Event):
        raise TypeError("Operational classification requires a typed event.")
    kind = {
        Event.TASK_FAILED: IncidentKind.TASK_FAILED,
        Event.FACT_DRIFT: IncidentKind.STORAGE_INTEGRITY,
        Event.SOURCE_INVALID: IncidentKind.SOURCE_REFRESH_FAILED,
        Event.SOURCE_TENANT_MISMATCH: IncidentKind.SOURCE_TENANT_MISMATCH,
        Event.SOURCE_DESTRUCTIVE_CHANGE: IncidentKind.SOURCE_DESTRUCTIVE_CHANGE,
        Event.SOURCE_HELD: IncidentKind.SOURCE_REFRESH_FAILED,
        Event.SOURCE_CREDENTIAL_FAILED: IncidentKind.SOURCE_REFRESH_FAILED,
        Event.SOURCE_PROVIDER_FAILED: IncidentKind.SOURCE_REFRESH_FAILED,
        Event.BOUNDARY_LAG: IncidentKind.SCHEDULER_LAG,
        Event.PRODUCTION_CLEANUP_FAILED: IncidentKind.PRODUCTION_CLEANUP_FAILED,
    }.get(event, IncidentKind.SYSTEM_FAILURE)
    return record_observation(kind, IncidentLevel.CRITICAL, policy=configured_policy())


def critical_auth(kind):
    """Use the existing detector's sustained verdict, not elapsed gaps in samples.

    WARNING auth windows already have their own durable evidence. Only the
    detector's CRITICAL verdict opens notification work; disconnected low-level
    warning windows must not be reinterpreted as continuous abuse here.
    """
    allowed = {
        "limiter_unavailable",
        "limiter_state_lost",
        "admin_abuse",
        "family_abuse",
    }
    if kind not in allowed:
        raise ValueError("Unknown critical authentication signal.")
    return record_observation(
        IncidentKind(kind), IncidentLevel.CRITICAL, policy=configured_policy()
    )
