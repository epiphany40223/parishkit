"""Bounded real aggregate samples and serialized, durable recovery windows."""

from django.db import connection
from django.db.models import Count, F, Max, Q
from redis.exceptions import RedisError

from parishkit.stewardship.jobs.operational_content import IncidentKind
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.operational_storage import record_recovery

from .auth_models import AuthenticationIncident
from .healthy_window import HEALTHY_WINDOW, advance_window

COUNTS = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local result = {redis.call('GET', KEYS[1]) or ''}
for i=2,9 do
    result[i] = math.min(1000, redis.call('ZCOUNT', KEYS[i], '('..(now-300), '+inf'))
end
return result
"""


def sample_counts(client, namespace, marker):
    """Read both real windows without creating failures or extending any TTL.

    The marker and counts share one atomic sample. A canary transition between
    the INFO/marker probe and this script is not evidence of healthy continuity.
    """
    keys = [namespace + ":health:marker"] + [
        f"{namespace}:aggregate:{kind}:{part}"
        for kind in ("admin", "family")
        for part in ("attempts", "sources", "identities", "candidates")
    ]
    result = client.register_script(COUNTS)(keys=keys)
    if len(result) != 9 or any(
        type(value) is not int or not 0 <= value <= 1000 for value in result[1:]
    ):
        raise RedisError("Limiter recovery evidence is unavailable.")
    coherent = result[0] == marker or not result[0] and not marker
    return {"admin": result[1:5], "family": result[5:9]}, bool(coherent)


def update_windows(row, observed, *, continuous, counts, limits):
    """Resolve only from current healthy samples under the auth incident lock.

    The caller owns the health lock in a short transaction. Acquiring the same
    auth lock used by failure writers orders new failures against recovery;
    samples started before such a failure cannot resolve it.
    """
    if row.observed_at is not None and observed <= row.observed_at:
        return {
            "observed_at": row.observed_at,
            **{
                f"{name}_healthy_since": getattr(row, f"{name}_healthy_since")
                for name in ("admin", "family", "store")
            },
        }
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", [736225, 1])
    values = {"observed_at": observed}
    failures = {
        item["kind"]: item
        for item in AuthenticationIncident.objects.filter(
            Q(resolved_at__isnull=True)
            | Q(created_at__gte=observed - HEALTHY_WINDOW)
            | Q(updated_at__gte=observed - HEALTHY_WINDOW)
        )
        .values("kind")
        .annotate(
            latest=Max("created_at"),
            changed=Max("updated_at"),
            pending=Count("id", filter=Q(resolved_at__isnull=True)),
        )
    }
    outage = failures.get("limiter_unavailable", {}).get("changed")
    # Recovery updates of already closed outages may restart a proof once. This
    # conservative fence also includes reobservations of an existing outage,
    # whose creation timestamp deliberately stays unchanged.
    episodes = dict(
        OperationalIncident.objects.filter(
            kind__in=("admin_abuse", "family_abuse", "limiter_state_lost"),
            resolved_at__isnull=True,
        ).values_list("kind", "last_seen")
    )
    for name, kind in (
        ("admin", "admin_abuse"),
        ("family", "family_abuse"),
        ("store", "limiter_state_lost"),
    ):
        healthy = continuous
        if name != "store":
            attempts, sources, identities, _ = counts[name]
            # Keep the inverse predicate aligned with limiting.AGGREGATE; real
            # count-only regressions cover both source and Admin identity paths.
            diversity = (
                sources >= limits.admin_sources or identities >= limits.admin_identities
                if name == "admin"
                else sources >= limits.family_sources
            )
            healthy = healthy and not (
                attempts >= limits.aggregate_attempts and diversity
            )
        latest = max(
            (
                value
                for value in (
                    outage,
                    failures.get(kind, {}).get("latest"),
                    episodes.get(kind),
                )
                if value is not None
            ),
            default=None,
        )
        field = f"{name}_healthy_since"
        since, recovered = advance_window(
            getattr(row, field),
            row.observed_at,
            observed,
            healthy=healthy,
            failure=latest,
        )
        values[field] = since
        if recovered and (kind in episodes or failures.get(kind, {}).get("pending", 0)):
            AuthenticationIncident.objects.filter(
                kind=kind,
                resolved_at__isnull=True,
                created_at__lt=since,
            ).update(resolved_at=observed, version=F("version") + 1)
            record_recovery(IncidentKind(kind), healthy_since=since)
    return values
