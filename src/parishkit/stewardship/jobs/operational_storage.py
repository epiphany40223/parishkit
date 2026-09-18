"""Serialized internal incident observations; no external I/O or routing flags."""

from django.db import connection, transaction

from parishkit.stewardship.observability import current_correlation

from .operational_content import IncidentKind, IncidentLevel
from .operational_models import OperationalIncident
from .operational_policy import IncidentPolicy


def record_observation(kind, level, *, policy):
    """Count one producer observation, atomically retaining any SQL-created notice.

    Policy is pinned for the episode; configuration changes affect the next
    episode. The caller must consume its own durable input exactly once in this
    transaction. Replaying an unacknowledged source event is not a new observation.
    This internal primitive does not classify arbitrary exception messages.
    """
    if not isinstance(kind, IncidentKind) or not isinstance(level, IncidentLevel):
        raise TypeError("Typed operational incident kind and level are required.")
    if not isinstance(policy, IncidentPolicy):
        raise TypeError("Typed operational incident policy is required.")
    with transaction.atomic(), connection.cursor() as cursor:
        _lock(cursor, kind)
        row = (
            OperationalIncident.objects.select_for_update()
            .filter(kind=kind.value, resolved_at__isnull=True)
            .first()
        )
        if row is None:
            row = OperationalIncident.objects.create(
                kind=kind.value,
                signal_level=level.value,
                suppression_seconds=policy.suppression_seconds,
                escalation_seconds=policy.escalation_seconds,
            )
        else:
            OperationalIncident.objects.filter(pk=row.pk).update(
                signal_level=level.value,
                action="observe",
                version=row.version + 1,
                actor_id=None,
                correlation_id=current_correlation(),
            )
        row.refresh_from_db()
        return row


def record_recovery(kind, *, healthy_since=None):
    """Resolve once, optionally fencing against failures after healthy proof began.

    The optional cutoff is checked under the same episode lock as observation;
    an older healthy sample cannot erase a newly recorded failure.
    """
    if not isinstance(kind, IncidentKind):
        raise TypeError("Typed operational incident kind is required.")
    with transaction.atomic(), connection.cursor() as cursor:
        _lock(cursor, kind)
        row = (
            OperationalIncident.objects.select_for_update()
            .filter(kind=kind.value, resolved_at__isnull=True)
            .first()
        )
        if row is not None and (healthy_since is None or row.last_seen < healthy_since):
            OperationalIncident.objects.filter(pk=row.pk).update(
                action="resolve",
                version=row.version + 1,
                actor_id=None,
                correlation_id=current_correlation(),
            )
            row.refresh_from_db()
        return row


def _lock(cursor, kind):
    """Serialize absent-row creation and recovery without a process-local cache."""
    # Use PostgreSQL's two-int advisory namespace, separate from campaign locks.
    # Stable sorted enum values are not needed: this key hashes the persisted kind.
    cursor.execute("SELECT pg_advisory_xact_lock(736245, hashtext(%s))", [kind.value])
