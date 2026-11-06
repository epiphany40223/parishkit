"""PostgreSQL-deduplicated, safe incident/notification intents for auth outages."""

from datetime import datetime, timedelta

from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action

from .auth_models import AuthenticationIncident

# How often one refused account is named in the audit log at most (#953).
ACCOUNT_REFUSAL_INTERVAL = timedelta(minutes=10)
# How many named refusals, across all accounts, one deployment writes per
# ACCOUNT_REFUSAL_INTERVAL at most (#968). Anyone can create Google accounts,
# so the per-account bound alone does not bound the log; past this ceiling a
# refusal falls back to the anonymous admin_login_denied sample. Twenty is
# far above a parish's legitimate refusals (a handful of people a day) and
# still caps an attack at under three thousand rows a day.
ACCOUNT_REFUSAL_CEILING = 20


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


def record_account_refusal(user_id):
    """Name a refused verified Google account, bounded per account and overall.

    Unlike the anonymous samples above, this follows a sign-in Google has
    already verified, which policy then refused (no rule, a rule with no
    role, a disabled identity, or the SQL session guard's late refusal), so
    the ``admin_login_refused`` entry names the account: its actor is the
    account's ``PortalUser``, which the sign-in has just recorded, and
    System logs shows its address (#953). It carries no context, so nothing
    new is stored about the person.

    Two bounds apply, both checked under one deployment-wide transaction
    advisory lock (the samples' lock namespace), so concurrent refusals
    cannot both pass either check: one entry per account per
    ``ACCOUNT_REFUSAL_INTERVAL``, and at most ``ACCOUNT_REFUSAL_CEILING``
    entries across all accounts in that interval. Google accounts are cheap
    to create, so past the ceiling the refusal is offered to the anonymous
    ``admin_login_denied`` sample instead. Database failure, including a
    lock wait past one second, is the same typed, retryable outage as the
    samples. Returns whether a named entry was written.
    """
    from .limiting import LimiterUnavailable

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='1s'")
            cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", [736228, 4])
            cursor.execute("SELECT statement_timestamp()")
            since = cursor.fetchone()[0] - ACCOUNT_REFUSAL_INTERVAL
            recent = AuditEvent.objects.filter(
                event_type="admin_login_refused", created_at__gte=since
            )
            if recent.filter(actor_id=user_id).exists():
                return False
            if recent.count() >= ACCOUNT_REFUSAL_CEILING:
                _record_login_rejection("admin_login_denied")
                return False
            AuditEvent.objects.create(
                event_type="admin_login_refused", actor_id=user_id
            )
            return True
    except DatabaseError:
        raise LimiterUnavailable() from None


def record_link_rejection():
    """Keep the opaque-link caller on the same bounded public-rejection policy."""
    record_login_rejection(Action.INVALID_LINK.value)


def record_incident(kind, severity, window, counts, *, observed_after=None):
    """Persist safe counts and atomic critical intent; delivery stays asynchronous.

    Availability returns True when both retained episodes resolve or neither is
    pending, and False if a newer observation refuses recovery. Other kinds
    return None. Callers use the boolean to retain pending local recovery work.
    """
    from parishkit.stewardship.jobs.operational_content import IncidentKind
    from parishkit.stewardship.jobs.operational_models import OperationalIncident
    from parishkit.stewardship.jobs.operational_sources import critical_auth
    from parishkit.stewardship.jobs.operational_storage import record_recovery

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
    if observed_after is not None and (
        kind != "limiter_available"
        or not isinstance(observed_after, datetime)
        or observed_after.tzinfo is None
        or observed_after.utcoffset() is None
    ):
        raise ValueError("Availability requires an aware observation timestamp.")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout='1s'")
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736225, 1])
        cursor.execute("SELECT statement_timestamp()")
        now = cursor.fetchone()[0]
        pending = AuthenticationIncident.objects.filter(
            kind="limiter_unavailable", resolved_at__isnull=True
        )
        if kind == "limiter_available":
            current = pending.first()
            if observed_after is not None:
                if observed_after > now:
                    raise ValueError("Availability cannot precede its observation.")
                if current is not None and current.updated_at >= observed_after:
                    return False
            # The operational statement may be later than the auth statement in
            # the same failure transaction. Check both fences before resolving
            # either record, retaining both locks until this atomic commit.
            episode = record_recovery(
                IncidentKind.LIMITER_UNAVAILABLE, healthy_since=observed_after
            )
            if episode is not None and episode.resolved_at is None:
                return False
            if current is not None:
                pending.update(resolved_at=now, version=F("version") + 1)
                AuditEvent.objects.create(event_type="limiter_recovered")
            return True
        if kind == "limiter_unavailable":
            current = pending.first()
            if current is not None:
                # Update one fixed-size fence on every actual failure, without
                # allocating per-request rows/notices. Only operational episode
                # observations are throttled; recovery must see newer failures.
                pending.update(version=F("version") + 1)
                episode = OperationalIncident.objects.filter(
                    kind=kind, resolved_at__isnull=True
                ).first()
                if episode is None or now - episode.last_seen >= timedelta(minutes=1):
                    critical_auth(kind)
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
            if severity == 2:
                critical_auth(kind)
