"""Real idle-time recovery and failures through the periodic web-owned observer."""

from datetime import timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from django.db import DatabaseError, connection
from redis.exceptions import RedisError

from parishkit.stewardship import runtime_auth_health as health
from parishkit.stewardship.accounts.auth_models import (
    AuthenticationIncident,
    LimiterStoreHealth,
)
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
)

from .test_limiter_recovery_postgresql import prior_episode, prior_healthy_window
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "kind", ["admin_abuse", "family_abuse", "limiter_state_lost", "limiter_unavailable"]
)
def test_no_login_needed_for_periodic_recovery_with_real_web_grants(
    real_limiter, monkeypatch, kind
):
    """A real child thread completes retained proof using only web SQL authority."""
    now = prior_healthy_window(real_limiter)
    episode = prior_episode(kind, earlier=now - timedelta(minutes=6))
    complete, retired = Event(), Mock()
    observe = health.observe_once
    failures = []

    def restricted(limiter):
        """Use a separate real SQL connection and the exact restricted web login."""
        try:
            with web_login():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_user")
                    assert cursor.fetchone() == ("pk_stewardship_web",)
                observe(limiter)
                assert connection.connection is None
        except Exception as error:
            failures.append(error)
            raise
        finally:
            connection.close()
            observer.stop.set()
            complete.set()

    monkeypatch.setattr(health, "observe_once", restricted)
    monkeypatch.setattr(health, "INTERVAL_SECONDS", 0.01)
    observer = health.PeriodicAuthenticationHealth(
        real_limiter, check=lambda: None, active=lambda: True, retire=retired
    )
    observer.start()
    try:
        assert complete.wait(10)
    finally:
        observer.close()
    assert not observer.thread.is_alive()
    assert not failures
    retired.assert_not_called()
    episode.refresh_from_db()
    assert episode.resolved_at is not None
    assert AuthenticationIncident.objects.get().resolved_at is not None
    assert OperationalNotice.objects.filter(phase="resolved").count() == 1
    # No interactive request or synthetic attempt is needed for recovery.
    assert list(real_limiter.client.scan_iter(real_limiter.namespace + ":*")) == [
        (real_limiter.namespace + ":health:marker").encode()
    ]


@pytest.mark.parametrize("error_type", [RedisError, DatabaseError])
def test_periodic_failure_persists_outage_without_resetting_old_proof(
    real_limiter, monkeypatch, error_type
):
    """Failure retains one durable outage; later actual success resolves it."""
    prior_healthy_window(real_limiter)
    prior = LimiterStoreHealth.objects.get().observed_at
    with monkeypatch.context() as patch:
        patch.setattr(
            real_limiter,
            "check_health",
            Mock(side_effect=error_type("private-provider-canary")),
        )
        for _ in range(2):
            with pytest.raises(LimiterUnavailable) as error:
                health.observe_once(real_limiter)
            assert "private" not in str(error.value)
            assert connection.connection is None
    assert real_limiter.outage
    assert (
        AuthenticationIncident.objects.filter(kind="limiter_unavailable").count() == 1
    )
    assert OperationalNotice.objects.count() == 1
    assert LimiterStoreHealth.objects.get().observed_at == prior
    health.observe_once(real_limiter)
    assert not real_limiter.outage
    assert AuthenticationIncident.objects.get().resolved_at is not None
    assert OperationalNotice.objects.filter(phase="resolved").count() == 1


def test_failed_incident_write_is_not_reported_as_recovery(real_limiter, monkeypatch):
    """Unavailable durable storage keeps local outage state and generic diagnostics."""
    monkeypatch.setattr(
        real_limiter, "check_health", Mock(side_effect=RedisError("private-canary"))
    )
    real_limiter.incident = Mock(side_effect=DatabaseError("private-SQL-canary"))
    with pytest.raises(LimiterUnavailable) as error:
        health.observe_once(real_limiter)
    assert "private" not in str(error.value)
    assert real_limiter.outage and connection.connection is None
    assert not OperationalNotice.objects.exists()


@pytest.mark.parametrize("pending", [False, True])
def test_periodic_peer_contention_preserves_health_and_outage(real_limiter, pending):
    """A sibling's health lock is not a failed store or manufactured healthy proof."""
    now = prior_healthy_window(real_limiter)
    if pending:
        episode = prior_episode(
            "limiter_unavailable", earlier=now - timedelta(minutes=6)
        )
        real_limiter.outage = True
    prior = LimiterStoreHealth.objects.values().get()
    incidents = list(AuthenticationIncident.objects.values())
    episodes = list(OperationalIncident.objects.values())
    notices = list(OperationalNotice.objects.values())
    other = connection.copy(alias="synthetic_periodic_peer")
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(736227,1)")
        health.observe_once(real_limiter)
        assert real_limiter.outage is pending
        assert LimiterStoreHealth.objects.values().get() == prior
        assert list(AuthenticationIncident.objects.values()) == incidents
        assert list(OperationalIncident.objects.values()) == episodes
        assert list(OperationalNotice.objects.values()) == notices
        if pending:
            episode.refresh_from_db()
            assert episode.resolved_at is None
    finally:
        other.close()
    health.observe_once(real_limiter)
    assert LimiterStoreHealth.objects.get().observed_at > prior["observed_at"]
    assert not real_limiter.outage
    if pending:
        episode.refresh_from_db()
        assert episode.resolved_at is not None
        assert AuthenticationIncident.objects.get().resolved_at is not None
        assert OperationalIncident.objects.count() == 1
        assert OperationalNotice.objects.filter(phase="resolved").count() == 1
    else:
        assert not AuthenticationIncident.objects.exists()
        assert not OperationalIncident.objects.exists()
        assert not OperationalNotice.objects.exists()
