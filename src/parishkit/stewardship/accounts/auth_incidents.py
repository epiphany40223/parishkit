"""PostgreSQL-deduplicated, safe incident/notification intents for auth outages."""

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.audit.models import AuditEvent

from .auth_models import AuthenticationIncident


def record_incident(kind, severity, window, counts):
    """Persist safe counts only; BG notification consumers acknowledge delivery."""
    if kind not in {
        "limiter_unavailable",
        "limiter_available",
        "limiter_state_lost",
        "admin_abuse",
        "family_abuse",
    }:
        raise ValueError("Unknown authentication incident.")
    if len(counts) != 4 or any(
        type(n) is not int or not 0 <= n <= 1000 for n in counts
    ):
        raise ValueError("Authentication incident counts must be bounded integers.")
    if severity not in {0, 1, 2} or type(window) is not int or window < 0:
        raise ValueError("Invalid authentication incident level/window.")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736225, 1])
        pending = AuthenticationIncident.objects.filter(
            kind="limiter_unavailable", resolved_at__isnull=True
        )
        if kind == "limiter_available":
            if pending.update(resolved_at=timezone.now(), version=F("version") + 1):
                AuditEvent.objects.create(event_type="limiter_recovered")
            return
        if kind == "limiter_unavailable":
            if pending.exists():
                return
            window = int(timezone.now().timestamp() * 1_000_000)
        incident, created = AuthenticationIncident.objects.get_or_create(
            kind=kind,
            window=window,
            defaults=dict(
                level="CRITICAL" if severity == 2 else "WARNING",
                attempts=counts[0],
                sources=counts[1],
                identities=counts[2],
                candidates=counts[3],
            ),
        )
        if created:
            AuditEvent.objects.create(event_type=kind, subject_id=incident.pk)
