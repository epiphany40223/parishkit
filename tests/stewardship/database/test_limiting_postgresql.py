"""Atomic Valkey limiter boundaries, bounded fallback and durable outage signals."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.test import Client
from redis import Redis

from parishkit.stewardship.accounts.auth_incidents import record_incident
from parishkit.stewardship.accounts.limiting import (
    Counter,
    Limiter,
    LimiterUnavailable,
    LocalBuckets,
)
from parishkit.stewardship.accounts.models import AuthenticationIncident
from parishkit.stewardship.authentication_policy import AuthenticationLimits

from .auth_builders import signed_in

pytestmark = pytest.mark.django_db(transaction=True)


def test_tuned_bucket_uses_refill_horizon_for_expiry(auth_service):
    """An idle custom bucket cannot regain a full burst before its refill time."""
    limiter = auth_service.limiter
    limiter.limits = AuthenticationLimits(access_per_minute=1, access_burst=5)
    for _ in range(5):
        assert limiter.bucket("access", "192.0.2.1") == 0
    assert limiter.bucket("access", "192.0.2.1") > 0
    key = limiter.namespace + ":bucket:access:" + limiter.fingerprint("ip", "192.0.2.1")
    assert 299 <= limiter.client.ttl(key) <= 300


def test_tuned_failure_thresholds_reach_actual_window_and_aggregate_paths(auth_service):
    """Configuration changes the real limiter without weakening candidate accounting."""
    from parishkit.stewardship.accounts.authentication import ip_counter

    limiter = auth_service.limiter
    limiter.limits = AuthenticationLimits(
        admin_starts=2, admin_callbacks=3, aggregate_attempts=2, family_sources=2
    )
    start_counter = ip_counter(limiter, "192.0.2.1", initiation=True)
    assert start_counter.limit == 2
    assert ip_counter(limiter, "192.0.2.1").limit == 3
    assert limiter.counters([start_counter], failure=True) == 0
    assert limiter.counters([start_counter], failure=True) > 0
    assert not limiter.failed("family", "192.0.2.1")
    assert limiter.failed("family", "192.0.2.2")
    incident = AuthenticationIncident.objects.get()
    assert (incident.attempts, incident.sources) == (2, 2)


def test_sliding_window_atomic_capacity_and_expiry(auth_service):
    """Parallel failures cannot lose increments; expiry uses Valkey's own clock."""
    limiter = auth_service.limiter
    limiter.check_health()
    counter = Counter("family_pair", limiter.fingerprint("pair", "synthetic"), 5, 900)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: limiter.counters([counter], failure=True), range(5))
        )
    assert sorted(results) == [0, 0, 0, 0, 30]
    assert limiter.counters([counter]) == 30
    key = limiter.namespace + ":window:family_pair:" + counter.fingerprint
    assert 0 < limiter.client.ttl(key) <= 900
    for item in limiter.client.zrange(key, 0, -1):
        limiter.client.zadd(key, {item: 1})
    assert limiter.counters([counter]) == 0


def test_counters_remain_bounded_and_success_clears_only_pair(auth_service):
    limiter = auth_service.limiter
    pair = Counter("family_pair", limiter.fingerprint("pair", "first"), 5, 900)
    other = Counter("family_pair", limiter.fingerprint("pair", "second"), 5, 900)
    ip = Counter("family_ip", limiter.fingerprint("ip", "127.0.0.1"), 100, 600)
    for _ in range(200):
        assert limiter.counters([pair, other, ip], failure=True) <= 3600
    limiter.clear(pair)
    assert limiter.counters([pair]) == 0
    assert limiter.counters([other, ip]) > 0
    for key in limiter.client.scan_iter(limiter.namespace + ":window:*"):
        assert limiter.client.zcard(key) <= 209


def test_admin_and_link_token_bursts_are_independent(auth_service):
    limiter = auth_service.limiter
    assert [limiter.bucket("admin", "127.0.0.1") for _ in range(20)] == [0] * 20
    assert limiter.bucket("admin", "127.0.0.1") > 0
    assert [limiter.bucket("access", "127.0.0.1") for _ in range(30)] == [0] * 30
    assert limiter.bucket("access", "127.0.0.1") > 0
    assert limiter.bucket("admin", "127.0.0.2") == 0


@pytest.mark.parametrize(("kind", "sources"), [("admin", 10), ("family", 20)])
def test_distributed_threshold_counts_repeated_dictionary_and_deduplicates(
    auth_service, kind, sources
):
    limiter = auth_service.limiter
    for index in range(99):
        limiter.failed(
            kind,
            f"192.0.2.{index % sources + 1}",
            candidate=limiter.fingerprint("code", "SAMECODE"),
        )
    assert not AuthenticationIncident.objects.exists()
    limiter.failed(kind, "192.0.2.1", candidate=limiter.fingerprint("code", "SAMECODE"))
    incident = AuthenticationIncident.objects.get()
    assert incident.kind == kind + "_abuse"
    assert incident.attempts == 100
    assert incident.sources == sources
    assert incident.candidates == 1
    assert incident.notification_pending
    limiter.failed(kind, "192.0.2.1")
    assert AuthenticationIncident.objects.count() == 1
    assert limiter.elevated(kind)


def test_admin_identity_diversity_can_detect_single_ip_attack(auth_service):
    limiter = auth_service.limiter
    for index in range(100):
        limiter.failed(
            "admin",
            "192.0.2.1",
            identity=limiter.fingerprint("identity", str(index % 10)),
        )
    incident = AuthenticationIncident.objects.get()
    assert incident.sources == 1
    assert incident.identities == 10


def test_three_consecutive_windows_escalate_critical(auth_service):
    """Seed the two prior closed window receipts, not wall-clock sleeps."""
    limiter = auth_service.limiter
    now = limiter.client.time()[0]
    limiter.client.hset(
        limiter.namespace + ":aggregate:family:alert",
        mapping={"window": now // 300 - 1, "streak": 2},
    )
    for index in range(100):
        limiter.failed("family", f"192.0.2.{index % 20 + 1}")
    assert AuthenticationIncident.objects.get().level == "CRITICAL"


def test_outage_fail_closed_but_links_and_existing_sessions_work(
    auth_service, google, settings
):
    from parishkit.stewardship.accounts.authentication import AuthRuntime

    browser, _ = signed_in()
    unavailable = Redis(
        host="127.0.0.1", port=56380, socket_timeout=0.1, socket_connect_timeout=0.1
    )
    limiter = Limiter(
        unavailable, b"synthetic-test-outage-key-material", incident=record_incident
    )
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(
        auth_service.store, limiter, auth_service.setup_complete
    )
    assert Client().get("/admin/login").status_code == 503
    assert Client().get("/admin/oauth/callback").status_code == 503
    assert browser.get("/admin/").status_code == 200
    with pytest.raises(LimiterUnavailable):
        limiter.bucket("admin", "192.0.2.1")
    assert limiter.bucket("access", "192.0.2.1") == 0
    assert (
        AuthenticationIncident.objects.filter(kind="limiter_unavailable").count() == 1
    )
    record_incident("limiter_available", 0, 0, (0, 0, 0, 0))
    assert AuthenticationIncident.objects.get().resolved_at is not None
    assert Client().get("/admin/login").status_code == 503
    assert AuthenticationIncident.objects.count() == 2
    unavailable.close()


def test_local_token_buckets_have_fixed_memory_and_refill():
    now = [0]
    buckets = LocalBuckets(capacity=2, clock=lambda: now[0])
    for _ in range(30):
        assert buckets.consume("one") == 0
    assert buckets.consume("one") == 1
    assert buckets.consume("two") == 0
    assert buckets.consume("three") == 60
    now[0] += 1
    assert buckets.consume("one") == 0
    now[0] += 121
    assert buckets.consume("three") == 0
    assert len(buckets.entries) == 1


def test_rate_keys_never_retain_raw_identity_or_candidate(auth_service):
    limiter = auth_service.limiter
    private = "private-person@example.org"
    limiter.failed(
        "admin", "192.0.2.1", identity=limiter.fingerprint("identity", private)
    )
    data = b"".join(limiter.client.scan_iter(limiter.namespace + ":*"))
    assert private.encode() not in data
    assert b"192.0.2.1" not in data


@pytest.mark.parametrize("loss", ["marker", "restart", "eviction", "stats_reset"])
def test_counter_loss_creates_durable_critical_intent(auth_service, loss, monkeypatch):
    """Probe real Valkey; simulate only INFO transitions without resetting services."""
    from parishkit.stewardship.accounts.auth_models import LimiterStoreHealth

    limiter = auth_service.limiter
    assert not limiter.check_health(force=True)
    original = limiter.client.info

    def info(section):
        """No tests restart shared services, evict keys, or flush databases."""
        values = original(section)
        if loss == "restart" and section == "server":
            values["run_id"] = "f" * 40
        if loss in {"eviction", "stats_reset"} and section == "stats":
            values["evicted_keys"] += 1
        return values

    if loss == "marker":
        limiter.client.delete(limiter.namespace + ":health:marker")
    elif loss == "stats_reset":
        from django.db.models import F

        LimiterStoreHealth.objects.update(evicted_keys=99, version=F("version") + 1)
    monkeypatch.setattr(limiter.client, "info", info)
    assert limiter.check_health(force=True)
    incident = AuthenticationIncident.objects.get(kind="limiter_state_lost")
    assert incident.level == "CRITICAL"
    assert incident.notification_pending
    assert not limiter.check_health(force=True)
    replacement = Limiter(
        limiter.client,
        limiter.key,
        incident=record_incident,
        namespace=limiter.namespace,
    )
    assert not replacement.check_health(force=True)
    assert AuthenticationIncident.objects.count() == 1


def test_marker_repair_waits_for_durable_audit(auth_service, monkeypatch):
    """An audit failure leaves the evidence of counter loss observable on retry."""
    from parishkit.stewardship.accounts import limiter_health

    limiter = auth_service.limiter
    limiter.check_health(force=True)
    marker = limiter.namespace + ":health:marker"
    limiter.client.delete(marker)
    original = limiter_health.record_incident

    def fail(*args):
        """Exercise rollback at the durable notification boundary."""
        raise RuntimeError("Synthetic audit unavailable")

    monkeypatch.setattr(limiter_health, "record_incident", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        limiter.check_health(force=True)
    assert not limiter.client.exists(marker)
    monkeypatch.setattr(limiter_health, "record_incident", original)
    assert limiter.check_health(force=True)
    assert limiter.client.exists(marker)
    assert AuthenticationIncident.objects.get().level == "CRITICAL"
