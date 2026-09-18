"""Real store probes resolve only persisted, continuously observed episodes."""

from datetime import timedelta

import pytest
from django.db import DatabaseError, transaction
from django.db.models import F

from parishkit.stewardship.accounts.auth_incidents import record_incident
from parishkit.stewardship.accounts.auth_models import (
    AuthenticationIncident,
    LimiterStoreHealth,
)
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
)
from parishkit.stewardship.jobs.ownership import database_now

pytestmark = pytest.mark.django_db(transaction=True)


def instant():
    """Capture the SQL clock without keeping a transaction around provider I/O."""
    with transaction.atomic():
        return database_now()


def prior_episode(kind, *, earlier):
    """Seed a valid earlier observation, not a rewritten clock or disabled guard."""
    AuthenticationIncident.objects.create(
        kind=kind,
        window=1,
        level="CRITICAL",
        attempts=100,
        sources=20,
        identities=0,
        candidates=1,
        created_at=earlier,
        updated_at=earlier,
    )
    return OperationalIncident.objects.create(
        kind=kind,
        signal_level="CRITICAL",
        suppression_seconds=900,
        escalation_seconds=900,
        first_seen=earlier,
        last_seen=earlier,
    )


def prior_healthy_window(limiter):
    """Seed the persisted earlier samples; leave the final real probe to the test."""
    limiter.check_health(force=True)
    now = instant()
    LimiterStoreHealth.objects.update(
        observed_at=now - timedelta(seconds=30),
        admin_healthy_since=now - timedelta(seconds=301),
        family_healthy_since=now - timedelta(seconds=301),
        store_healthy_since=now - timedelta(seconds=301),
        version=F("version") + 1,
    )
    return now


@pytest.mark.parametrize("kind", ["admin_abuse", "family_abuse", "limiter_state_lost"])
def test_real_healthy_probe_resolves_once_across_process_restart(real_limiter, kind):
    """Current INFO, canary and real counters complete retained healthy proof."""
    from parishkit.stewardship.accounts.limiting import Limiter

    limiter = real_limiter
    now = prior_healthy_window(limiter)
    episode = prior_episode(kind, earlier=now - timedelta(minutes=6))
    replacement = Limiter(
        limiter.client,
        limiter.key,
        incident=record_incident,
        namespace=limiter.namespace,
    )
    replacement.check_health(force=True)
    episode.refresh_from_db()
    assert episode.resolved_at is not None
    assert AuthenticationIncident.objects.get(kind=kind).resolved_at is not None
    replacement.check_health(force=True)
    assert list(
        OperationalNotice.objects.filter(incident=episode)
        .order_by("incident_version")
        .values_list("phase", flat=True)
    ) == ["opened", "resolved"]


@pytest.mark.parametrize("interruption", ["gap", "canary", "new_failure", "abuse"])
def test_missing_continuity_never_resolves(real_limiter, interruption):
    """Old proof cannot hide missing samples, counter loss or current failures."""
    limiter = real_limiter
    now = prior_healthy_window(limiter)
    episode = prior_episode("family_abuse", earlier=now - timedelta(minutes=6))
    if interruption == "gap":
        LimiterStoreHealth.objects.update(
            observed_at=now - timedelta(seconds=91),
            version=F("version") + 1,
        )
    elif interruption == "canary":
        limiter.client.delete(limiter.namespace + ":health:marker")
    elif interruption == "new_failure":
        record_incident("family_abuse", 2, 2, (100, 20, 0, 1))
    else:
        # Actual application failures populate the counters. The probe itself
        # neither extends their TTL nor adds synthetic attempts.
        for index in range(100):
            limiter.failed("family", f"192.0.2.{index % 20 + 1}")
    limiter.check_health(force=True)
    episode.refresh_from_db()
    assert episode.resolved_at is None
    assert not OperationalNotice.objects.filter(phase="resolved").exists()


def test_continuing_outage_is_bounded_but_not_silently_forgotten(real_limiter):
    """One old outage is reobserved once; a request flood cannot storm notices."""
    earlier = instant() - timedelta(minutes=16)
    episode = prior_episode("limiter_unavailable", earlier=earlier)
    for _ in range(20):
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    episode.refresh_from_db()
    assert episode.occurrences == 2
    assert AuthenticationIncident.objects.count() == 1
    assert list(
        OperationalNotice.objects.order_by("incident_version").values_list(
            "phase", flat=True
        )
    ) == ["opened", "repeated"]


def test_probe_preserves_real_counter_contents_and_ttl(real_limiter):
    """Recovery reads are observations, not artificial traffic or counter resets."""
    limiter = real_limiter
    limiter.failed("family", "192.0.2.1")
    key = limiter.namespace + ":aggregate:family:attempts"
    values = limiter.client.zrange(key, 0, -1, withscores=True)
    ttl = limiter.client.pttl(key)
    limiter.check_health(force=True)
    assert limiter.client.zrange(key, 0, -1, withscores=True) == values
    assert 0 < limiter.client.pttl(key) <= ttl


def test_fenced_recovery_does_not_resolve_a_newer_observation(real_limiter):
    """The shared incident owner rechecks the proof cutoff under its own lock."""
    from parishkit.stewardship.jobs.operational_content import IncidentKind
    from parishkit.stewardship.jobs.operational_storage import record_recovery

    record_incident("family_abuse", 2, 1, (100, 20, 0, 1))
    row = OperationalIncident.objects.get()
    record_recovery(IncidentKind.FAMILY_ABUSE, healthy_since=row.last_seen)
    row.refresh_from_db()
    assert row.resolved_at is None
    assert OperationalNotice.objects.count() == 1


@pytest.mark.parametrize("kind", ["admin", "family", "store"])
def test_sql_rejects_window_start_after_its_observation(real_limiter, kind):
    """Direct application SQL cannot retain internally contradictory proof."""
    real_limiter.check_health(force=True)
    row = LimiterStoreHealth.objects.get()
    with pytest.raises(DatabaseError) as rejected, transaction.atomic():
        LimiterStoreHealth.objects.update(
            **{f"{kind}_healthy_since": row.observed_at + timedelta(seconds=1)},
            version=F("version") + 1,
        )
    assert rejected.value.__cause__.sqlstate == "23514"
    row.refresh_from_db()
    assert getattr(row, f"{kind}_healthy_since") is None


def test_failure_during_probe_resets_old_proof(real_limiter, monkeypatch):
    """A later failure commit wins even when the sampled counters look quiet."""
    limiter = real_limiter
    now = prior_healthy_window(limiter)
    episode = prior_episode("family_abuse", earlier=now - timedelta(minutes=6))
    original = limiter.client.info

    def failure(section):
        """Inject the concurrent durable failure, not a false healthy clock."""
        if section == "server":
            record_incident("family_abuse", 2, 2, (100, 20, 0, 1))
        return original(section)

    monkeypatch.setattr(limiter.client, "info", failure)
    limiter.check_health(force=True)
    episode.refresh_from_db()
    assert episode.resolved_at is None
    assert LimiterStoreHealth.objects.get().family_healthy_since is None


@pytest.mark.parametrize(
    "kind,attempts,sources,identities,healthy",
    [
        ("family", 100, 20, 0, False),
        ("family", 99, 20, 0, True),
        ("family", 100, 19, 10, True),
        ("admin", 100, 10, 0, False),
        ("admin", 100, 1, 10, False),
        ("admin", 100, 1, 9, True),
    ],
)
def test_counter_only_recovery_uses_detector_thresholds(
    real_limiter,
    kind,
    attempts,
    sources,
    identities,
    healthy,
):
    """Isolate actual count predicates from the independently tested failure fence."""
    limiter = real_limiter
    now = prior_healthy_window(limiter)
    episode = prior_episode(kind + "_abuse", earlier=now - timedelta(minutes=6))
    seconds, micros = limiter.client.time()
    score = seconds + micros / 1_000_000
    for part, count in (
        ("attempts", attempts),
        ("sources", sources),
        ("identities", identities),
    ):
        if count:
            limiter.client.zadd(
                f"{limiter.namespace}:aggregate:{kind}:{part}",
                {str(index): score for index in range(count)},
            )
    limiter.check_health(force=True)
    episode.refresh_from_db()
    assert (episode.resolved_at is not None) is healthy
    assert AuthenticationIncident.objects.count() == 1


def test_canary_change_during_successful_probe_is_not_an_outage(
    real_limiter,
    monkeypatch,
):
    """Counter loss interrupts proof without pretending a working store is down."""
    limiter = real_limiter
    now = prior_healthy_window(limiter)
    episode = prior_episode("family_abuse", earlier=now - timedelta(minutes=6))
    original = limiter.client.get

    def lose_marker(key):
        """Remove only this test namespace's canary between the two actual reads."""
        value = original(key)
        limiter.client.delete(key)
        return value

    monkeypatch.setattr(limiter.client, "get", lose_marker)
    limiter.health_checked_at = None
    assert limiter.bucket("admin", "192.0.2.1") == 0
    episode.refresh_from_db()
    assert episode.resolved_at is None
    assert AuthenticationIncident.objects.filter(kind="limiter_state_lost").exists()
    assert not AuthenticationIncident.objects.filter(
        kind="limiter_unavailable"
    ).exists()
    assert LimiterStoreHealth.objects.get().family_healthy_since is None


def test_reobserved_old_outage_restarts_proof(real_limiter):
    """A new failure observation matters even though its incident identity is old."""
    limiter = real_limiter
    now = prior_healthy_window(limiter)
    episode = prior_episode("family_abuse", earlier=now - timedelta(minutes=6))
    prior_episode("limiter_unavailable", earlier=now - timedelta(minutes=16))
    record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    limiter.check_health(force=True)
    episode.refresh_from_db()
    assert episode.resolved_at is None
    assert LimiterStoreHealth.objects.get().family_healthy_since > now


def test_discarded_probe_cannot_advance_proof_or_notify_recovery(
    real_limiter,
    monkeypatch,
):
    """A newer accepted sample remains the complete retained window state."""
    from parishkit.stewardship.accounts.limiter_health import observe_store

    limiter = real_limiter
    limiter.check_health(force=True)
    original = limiter.client.get
    newer = []

    def overlap(key):
        """Run one newer full probe while the older probe is outside SQL locks."""
        value = original(key)
        if not newer:
            newer.append(None)
            assert observe_store(limiter.client, limiter.namespace).lost is False
            newer[0] = LimiterStoreHealth.objects.values().get()
        return value

    monkeypatch.setattr(limiter.client, "get", overlap)
    assert observe_store(limiter.client, limiter.namespace) is None
    assert LimiterStoreHealth.objects.values().get() == newer[0]
    assert not AuthenticationIncident.objects.exists()

    # Separately pin the public caller's tri-state handling; a discarded sample
    # must not invoke its ordinary limiter_available incident callback.
    from unittest.mock import Mock

    from parishkit.stewardship.accounts import limiter_health

    monkeypatch.setattr(limiter_health, "observe_store", lambda *args: None)
    notify = Mock()
    monkeypatch.setattr(limiter, "incident", notify)
    assert limiter.check_health(force=True) is False
    notify.assert_not_called()


def test_new_outage_after_probe_commit_is_not_resolved(real_limiter, monkeypatch):
    """A successful probe cannot emit a second, unfenced recovery after commit."""
    from parishkit.stewardship.accounts import limiter_health

    limiter = real_limiter
    original = limiter_health.observe_store

    def later_failure(*args):
        """Commit a new failure exactly after accepted health releases its lock."""
        result = original(*args)
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        return result

    monkeypatch.setattr(limiter_health, "observe_store", later_failure)
    limiter.check_health(force=True)
    assert AuthenticationIncident.objects.get().resolved_at is None
    assert OperationalIncident.objects.get().resolved_at is None
    assert not OperationalNotice.objects.filter(phase="resolved").exists()


def test_outage_during_probe_cannot_be_resolved_by_its_older_start(
    real_limiter,
    monkeypatch,
):
    """Even resolution under the lock must retain an observation-time fence."""
    limiter = real_limiter
    original = limiter.client.info

    def later_failure(section):
        """The failure commits after SQL start but before the probe takes locks."""
        if section == "server":
            record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        return original(section)

    monkeypatch.setattr(limiter.client, "info", later_failure)
    limiter.check_health(force=True)
    assert AuthenticationIncident.objects.get().resolved_at is None
    assert OperationalIncident.objects.get().resolved_at is None

    assert limiter.outage is True
    monkeypatch.setattr(limiter.client, "info", original)
    limiter.check_health(force=True)
    assert limiter.outage is False
    assert AuthenticationIncident.objects.get().resolved_at is not None
    assert OperationalIncident.objects.get().resolved_at is not None


def test_successful_counter_recovery_has_the_same_observation_fence(
    real_limiter,
    monkeypatch,
):
    """An INFO-throttled real counter cannot erase a concurrently newer outage."""
    limiter = real_limiter
    limiter.check_health(force=True)
    limiter.outage = True
    original = limiter.bucket_script

    def later_failure(**kwargs):
        """Commit after the counter's SQL start, without failing the counter."""
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        return original(**kwargs)

    monkeypatch.setattr(limiter, "bucket_script", later_failure)
    assert limiter.bucket("admin", "192.0.2.1") == 0
    assert AuthenticationIncident.objects.get().resolved_at is None
    assert OperationalIncident.objects.get().resolved_at is None
    assert limiter.outage is True
    monkeypatch.setattr(limiter, "bucket_script", original)
    assert limiter.bucket("admin", "192.0.2.1") == 0
    assert limiter.outage is False
    assert AuthenticationIncident.objects.get().resolved_at is not None
    assert OperationalIncident.objects.get().resolved_at is not None


def test_window_helper_itself_preserves_a_newer_sample(real_limiter):
    """The helper cannot return a timestamp rollback even without its outer guard."""
    from parishkit.stewardship.accounts.limiter_recovery import update_windows

    limiter = real_limiter
    limiter.check_health(force=True)
    row = LimiterStoreHealth.objects.get()
    result = update_windows(
        row,
        row.observed_at - timedelta(seconds=1),
        continuous=True,
        counts={"admin": [0, 0, 0, 0], "family": [0, 0, 0, 0]},
        limits=limiter.limits,
    )
    assert result == {
        "observed_at": row.observed_at,
        "admin_healthy_since": None,
        "family_healthy_since": None,
        "store_healthy_since": None,
    }


def test_failure_transaction_straddling_probe_keeps_both_records_open(
    real_limiter,
    monkeypatch,
):
    """Capture the real SQL clock between auth and operational failure writes."""
    from parishkit.stewardship.jobs import operational_sources

    original = operational_sources.critical_auth
    observed = []

    def capture_between_statements(kind):
        """Model exactly when a concurrent probe can read its start timestamp."""
        observed.append(database_now())
        return original(kind)

    monkeypatch.setattr(
        operational_sources, "critical_auth", capture_between_statements
    )
    record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    failure = AuthenticationIncident.objects.get()
    episode = OperationalIncident.objects.get()
    assert failure.updated_at < observed[0] <= episode.last_seen
    assert (
        record_incident(
            "limiter_available",
            0,
            0,
            (0, 0, 0, 0),
            observed_after=observed[0],
        )
        is False
    )
    failure.refresh_from_db()
    episode.refresh_from_db()
    assert failure.resolved_at is None and episode.resolved_at is None
    assert (
        record_incident(
            "limiter_available",
            0,
            0,
            (0, 0, 0, 0),
            observed_after=instant(),
        )
        is True
    )
    failure.refresh_from_db()
    episode.refresh_from_db()
    assert failure.resolved_at is not None and episode.resolved_at is not None
    assert list(
        OperationalNotice.objects.order_by("incident_version").values_list(
            "phase", flat=True
        )
    ) == ["opened", "resolved"]


def test_suppressed_repeat_failure_still_advances_recovery_fence(
    real_limiter,
    monkeypatch,
):
    """Do not create per-request alerts, but retain every newer failure cutoff."""
    limiter = real_limiter
    limiter.check_health(force=True)
    record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    limiter.outage = True
    original = limiter.bucket_script

    def repeated_failure(**kwargs):
        """The same pending outage fails again inside the one-minute suppression."""
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        return original(**kwargs)

    monkeypatch.setattr(limiter, "bucket_script", repeated_failure)
    assert limiter.bucket("admin", "192.0.2.1") == 0
    assert limiter.outage
    failure = AuthenticationIncident.objects.get()
    episode = OperationalIncident.objects.get()
    assert failure.version == 2 and failure.resolved_at is None
    assert episode.occurrences == 1 and episode.resolved_at is None
    assert OperationalNotice.objects.count() == 1
    monkeypatch.setattr(limiter, "bucket_script", original)
    assert limiter.bucket("admin", "192.0.2.1") == 0
    assert not limiter.outage
    failure.refresh_from_db()
    episode.refresh_from_db()
    assert failure.resolved_at is not None and episode.resolved_at is not None


@pytest.mark.parametrize("path", ["probe", "counter"])
def test_later_thread_failure_keeps_retry_state_after_success(
    real_limiter,
    monkeypatch,
    path,
):
    """A committed success cannot clear a failure signaled before local settlement."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    from parishkit.stewardship.accounts import limiter_health

    limiter = real_limiter
    limiter.check_health(force=True)

    def fail_in_other_thread():
        """Use an independent SQL connection, with no socket leak after the race."""
        try:
            limiter.outage = True
            record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        if path == "probe":
            original = limiter_health.observe_store

            def accepted_then_failure(*args):
                """Race after the SQL probe commit, before processing its result."""
                result = original(*args)
                pool.submit(fail_in_other_thread).result(timeout=5)
                return result

            monkeypatch.setattr(limiter_health, "observe_store", accepted_then_failure)
            limiter.check_health(force=True)
            monkeypatch.setattr(limiter_health, "observe_store", original)
        else:
            limiter.outage = True
            record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
            original = limiter.incident

            def recovered_then_failure(*args, **kwargs):
                """Race after counter recovery commits, before clearing its flag."""
                result = original(*args, **kwargs)
                pool.submit(fail_in_other_thread).result(timeout=5)
                return result

            monkeypatch.setattr(limiter, "incident", recovered_then_failure)
            assert limiter.bucket("admin", "192.0.2.1") == 0
            monkeypatch.setattr(limiter, "incident", original)
    assert limiter.outage is True
    assert AuthenticationIncident.objects.filter(resolved_at__isnull=True).count() == 1
    assert OperationalIncident.objects.filter(resolved_at__isnull=True).count() == 1
    # A subsequent actual counter, still inside the INFO throttle interval,
    # observes the later outage and completes both durable and local recovery.
    assert limiter.bucket("admin", "192.0.2.1") == 0
    assert limiter.outage is False
    assert not AuthenticationIncident.objects.filter(resolved_at__isnull=True).exists()
    assert not OperationalIncident.objects.filter(resolved_at__isnull=True).exists()
