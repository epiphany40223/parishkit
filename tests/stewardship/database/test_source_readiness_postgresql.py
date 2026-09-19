"""Production readiness requires current full-read evidence, not fresh deltas."""

from dataclasses import replace
from datetime import timedelta

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.source.readiness import source_readiness

from .campaign_builders import add_draft
from .test_background_grants_postgresql import task_login
from .test_source_attempts_postgresql import configured
from .test_source_health_postgresql import publish, source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_actual_web_role_accepts_exact_full_anchor_through_subsequent_delta(tmp_path):
    """Full proof survives a delta without granting web source validation payloads."""
    credential, *_ = configured(tmp_path)
    full = publish(credential)
    delta = publish(credential, kind="delta")
    with task_login(ServiceRole.WEB), work_transaction():
        result = source_readiness(_scope(None))
    assert result.ready
    assert result.full_id == full.pk and result.current_id == delta.pk
    assert result.observed_at == full.started_at


def test_exact_staleness_boundary_cannot_be_extended_by_a_new_delta(tmp_path, settings):
    """The full read's start, not its completion or the delta start, owns expiry."""
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(source_stale_seconds=120)
    credential, *_ = configured(tmp_path)
    full = publish(credential)
    publish(credential, kind="delta")
    with work_transaction():
        scope = _scope(None)
        expiry = full.started_at + timedelta(seconds=120)
        before = source_readiness(
            replace(scope, instant=expiry - timedelta(microseconds=1))
        )
        expired = source_readiness(replace(scope, instant=expiry))
    assert before.ready
    assert not expired.ready and expired.reason == "full_refresh_stale"
    assert expired.expires_at == expiry


def test_campaign_window_change_requires_new_full_observation(tmp_path):
    """A recent pre-campaign corpus cannot certify a different selected window."""
    credential, store, version, actor = configured(tmp_path)
    publish(credential)
    result, record, _ = add_draft(store, version, actor)
    assert result.state == "applied"
    from uuid import UUID

    with work_transaction():
        result = source_readiness(_scope(UUID(record["id"])))
    assert not result.ready and result.reason == "source_scope_changed"


def test_missing_or_future_observation_cannot_count_as_readiness(tmp_path):
    credential, *_ = configured(tmp_path)
    with work_transaction():
        missing = source_readiness(_scope(None))
    assert missing.reason == "full_refresh_required"
    full = publish(credential)
    with work_transaction():
        future = source_readiness(
            replace(_scope(None), instant=full.started_at - timedelta(seconds=1))
        )
    assert not future.ready and future.reason == "source_scope_changed"
