"""Deployment thresholds remain bounded and warn without reflecting private input."""

from dataclasses import fields

import pytest
import yaml

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.limiting import LocalBuckets
from parishkit.stewardship.authentication_policy import AuthenticationLimits
from parishkit.stewardship.deployment import load_deployment


@pytest.mark.parametrize("name", [field.name for field in fields(AuthenticationLimits)])
@pytest.mark.parametrize("value", [0, 1001, True, 1.0, "synthetic-private-value"])
def test_invalid_thresholds_never_echo_values(name, value):
    """Limits cannot exceed the fixed telemetry-store bounds or accept booleans."""
    with pytest.raises(ConfigError) as error:
        AuthenticationLimits(**{name: value})
    assert "synthetic-private-value" not in str(error.value)


def test_yaml_environment_and_explicit_threshold_precedence(tmp_path):
    """Thresholds follow the same deterministic precedence as other deployment input."""
    path = tmp_path / "limits.yaml"
    path.write_text(
        yaml.safe_dump({"deployment": {"authentication_limits": {"family_ip": 80}}})
    )
    key = "PARISHKIT_STEWARDSHIP_AUTH_LIMIT_FAMILY_IP"
    assert load_deployment(path, environ={}).authentication_limits.family_ip == 80
    assert (
        load_deployment(path, environ={key: "70"}).authentication_limits.family_ip == 70
    )
    assert (
        load_deployment(
            path, environ={key: "70"}, overrides={key: "60"}
        ).authentication_limits.family_ip
        == 60
    )
    with pytest.raises(ConfigError):
        load_deployment(path, environ={key: "synthetic-private-value"})


def test_production_warns_for_weaker_but_not_stricter_thresholds(caplog):
    """Only validated field names enter startup warnings; values are not echoed."""
    environment = {
        "PARISHKIT_STEWARDSHIP_PROFILE": "production",
        "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": "https://stewardship.example.org",
        "PARISHKIT_STEWARDSHIP_AUTH_LIMIT_ADMIN_CALLBACKS": "11",
        "PARISHKIT_STEWARDSHIP_AUTH_LIMIT_FAMILY_IP": "90",
    }
    configuration = load_deployment(environ=environment)
    assert configuration.authentication_limits.admin_callbacks == 11
    assert len(caplog.records) == 1
    assert caplog.records[0].message.endswith("admin_callbacks")
    caplog.clear()
    environment["PARISHKIT_STEWARDSHIP_AUTH_LIMIT_ADMIN_CALLBACKS"] = "9"
    load_deployment(environ=environment)
    assert not caplog.records


def test_unknown_threshold_key_is_not_reflected(tmp_path):
    """Unknown names do not enter warnings, exception messages or configuration."""
    path = tmp_path / "invalid.yaml"
    path.write_text(
        yaml.safe_dump(
            {"deployment": {"authentication_limits": {"synthetic-private-value": 5}}}
        )
    )
    with pytest.raises(ConfigError) as error:
        load_deployment(path, environ={})
    assert "synthetic-private-value" not in str(error.value)


def test_strict_fallback_rate_does_not_refill_early_by_expiring_the_bucket():
    """A one-per-minute policy must survive the old fixed two-minute TTL."""
    now = [0]
    buckets = LocalBuckets(clock=lambda: now[0])
    for _ in range(5):
        assert buckets.consume("synthetic", rate=1 / 60, burst=5) == 0
    assert buckets.consume("synthetic", rate=1 / 60, burst=5) == 60
    now[0] = 121
    assert buckets.consume("synthetic", rate=1 / 60, burst=5) == 0
    assert buckets.consume("synthetic", rate=1 / 60, burst=5) == 0
    assert buckets.consume("synthetic", rate=1 / 60, burst=5) > 0
