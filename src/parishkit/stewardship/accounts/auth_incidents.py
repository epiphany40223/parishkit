"""PostgreSQL-deduplicated, safe incident/notification intents for auth outages."""

from datetime import timedelta

from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action

from .auth_models import AuthenticationIncident


def record_login_rejection(event_type):
    """Unavailable sampled evidence yields the same typed, private auth outage."""
    from .limiting import LimiterUnavailable

    try:
        _record_login_rejection(event_type)
    except DatabaseError:
        raise LimiterUnavailable() from None


def _record_login_rejection(event_type):
    """At most one signal per public login class per deployment per five minutes.

    Per-attempt keyed source/candidate telemetry belongs only to the ephemeral
    aggregate detector. Public garbage cannot allocate unbounded permanent audit
    rows, even with many sources or when Valkey is unavailable. This signal is
    sampled evidence, not an exact attempt count.
    """
    kinds = ("family_link_invalid", "admin_login_denied", "family_login_failed")
    if event_type not in kinds:
        raise ValueError("Unknown public authentication rejection.")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(%s,%s)",
            [736228, kinds.index(event_type) + 1],
        )
        if not cursor.fetchone()[0]:
            return
        cursor.execute("SELECT statement_timestamp()")
        since = cursor.fetchone()[0] - timedelta(minutes=5)
        if AuditEvent.objects.filter(
            event_type=event_type, created_at__gte=since
        ).exists():
            return
        if event_type == Action.INVALID_LINK.value:
            record_action(
                Action.INVALID_LINK,
                actor_kind=ActorKind.SYSTEM,
                context={"outcome": Outcome.DENIED},
            )
        else:
            AuditEvent.objects.create(event_type=event_type)


def record_link_rejection():
    """Keep the opaque-link caller on the same bounded public-rejection policy."""
    record_login_rejection(Action.INVALID_LINK.value)


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
        cursor.execute("SET LOCAL lock_timeout='1s'")
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736225, 1])
        cursor.execute("SELECT statement_timestamp()")
        now = cursor.fetchone()[0]
        pending = AuthenticationIncident.objects.filter(
            kind="limiter_unavailable", resolved_at__isnull=True
        )
        if kind == "limiter_available":
            if pending.update(resolved_at=now, version=F("version") + 1):
                AuditEvent.objects.create(event_type="limiter_recovered")
            return
        if kind == "limiter_unavailable":
            if pending.exists():
                return
            window = int(now.timestamp() * 1_000_000)
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
