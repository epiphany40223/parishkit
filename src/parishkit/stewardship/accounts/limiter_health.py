"""Recognize ephemeral counter loss without persisting addresses or counter keys."""

import hashlib
import re
from dataclasses import dataclass
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import F
from redis.exceptions import RedisError

from parishkit.stewardship.authentication_policy import AuthenticationLimits

from .auth_incidents import record_incident
from .auth_models import LimiterStoreHealth
from .limiter_recovery import sample_counts, update_windows


@dataclass(frozen=True)
class StoreObservation:
    """Accepted counter continuity and the independently fenced outage outcome."""

    lost: bool
    available: bool


def observe_store(client, namespace, limits=None):
    """Detect restart, eviction, or a lost permanent canary against durable state.

    Only safe INFO fields enter PostgreSQL. The canary is repaired after commit:
    a failed audit commit must not erase the only evidence of a flush. Competing
    observers serialize on a low-volume health lock; duplicate loss observations
    within a five-minute alert window produce one notification intent.
    Run outside application transactions; Limiter.check_health enforces that
    precondition with a typed retryable denial before calling this primitive.
    Return None for an older discarded sample, otherwise a StoreObservation
    containing counter loss and whether fenced outage recovery succeeded.
    Accepted samples update durable recovery windows with the supplied typed
    AuthenticationLimits, or the ordinary defaults. Incoherent but successful
    reads interrupt continuity; they are not a store-unavailability verdict.
    """
    fingerprint = hashlib.sha256(namespace.encode("ascii")).hexdigest()
    marker_key = namespace + ":health:marker"
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        observed_after = cursor.fetchone()[0]
    # No network timeout can hold the global PostgreSQL incident lock. An older
    # concurrent sample is discarded below if another observer changed baseline.
    server = client.info("server")
    stats = client.info("stats")
    run_id, evictions = server.get("run_id"), stats.get("evicted_keys")
    if (
        type(run_id) is not str
        or re.fullmatch(r"[0-9a-f]{40}", run_id) is None
        or type(evictions) is not int
        or not 0 <= evictions <= 2**63 - 1
    ):
        raise RedisError("Limiter health evidence is unavailable.")
    marker = client.get(marker_key)
    counts, coherent = sample_counts(client, namespace, marker)
    with transaction.atomic(durable=True), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout='1s'")
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736227, 1])
        row = LimiterStoreHealth.objects.filter(
            namespace_fingerprint=fingerprint
        ).first()
        # Normally updated_at is later than observed_at. The explicit proof
        # check also defends malformed/seeded future samples without relying on
        # bookkeeping order, before any baseline or incident mutation occurs.
        if row is not None and (
            row.updated_at >= observed_after
            or (row.observed_at is not None and row.observed_at >= observed_after)
        ):
            # None is deliberately distinct from an accepted healthy sample.
            return None
        lost = row is not None and (
            not coherent
            or row.run_id != run_id
            or row.evicted_keys != evictions
            or marker not in {str(row.marker), str(row.marker).encode("ascii")}
        )
        continuous = row is not None and not lost
        if row is None:
            row = LimiterStoreHealth.objects.create(
                namespace_fingerprint=fingerprint,
                run_id=run_id,
                marker=uuid4(),
                evicted_keys=evictions,
            )
        elif lost:
            cursor.execute(
                "SELECT floor(extract(epoch FROM statement_timestamp())/300)::bigint"
            )
            record_incident("limiter_state_lost", 2, cursor.fetchone()[0], (0, 0, 0, 0))
        values = update_windows(
            row,
            observed_after,
            continuous=continuous,
            counts=counts,
            limits=AuthenticationLimits() if limits is None else limits,
        )
        LimiterStoreHealth.objects.filter(pk=row.pk).update(
            run_id=run_id,
            evicted_keys=evictions,
            version=F("version") + 1,
            **values,
        )
        # record_incident takes or re-enters the auth lock through this commit.
        # Resolve only failure observations older than this actual probe; never
        # publish an unfenced availability callback after releasing the lock.
        available = record_incident(
            "limiter_available", 0, 0, (0, 0, 0, 0), observed_after=observed_after
        )
        # Successful observations never rewrite the marker or extend counter TTLs.
        if not coherent or marker not in {
            str(row.marker),
            str(row.marker).encode("ascii"),
        }:
            transaction.on_commit(lambda: client.set(marker_key, str(row.marker)))
        return StoreObservation(lost=lost, available=available)
