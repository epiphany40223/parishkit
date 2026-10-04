"""Source reads learn their deployment profile from runtime assembly, or fail closed."""

import pytest
from django.conf import settings

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.source.transport import runtime_profile


@pytest.mark.parametrize("profile", list(DeploymentProfile))
def test_runtime_profile_reads_the_recorded_setting(monkeypatch, profile):
    """Assembly records the admitted profile's value; reads give the enum back."""
    monkeypatch.setattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", profile.value)
    assert runtime_profile() is profile


def test_test_settings_module_names_its_own_profile():
    """Standalone settings modules carry a profile; there is no base default."""
    from importlib import import_module

    assert (
        DeploymentProfile.TEST.value
        == import_module(
            "parishkit.stewardship.settings.test"
        ).STEWARDSHIP_DEPLOYMENT_PROFILE
    )
    assert (
        DeploymentProfile.DEVELOPMENT.value
        == import_module(
            "parishkit.stewardship.settings.development"
        ).STEWARDSHIP_DEPLOYMENT_PROFILE
    )
    assert not hasattr(
        import_module("parishkit.stewardship.settings.base"),
        "STEWARDSHIP_DEPLOYMENT_PROFILE",
    )


@pytest.mark.parametrize("value", ["bogus", "", None, "LOCAL"])
def test_runtime_profile_fails_closed_on_an_invalid_setting(monkeypatch, value):
    """An unassembled or misrecorded role cannot pick a ParishSoft base URL."""
    monkeypatch.setattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", value)
    with pytest.raises(ConfigError):
        runtime_profile()


def test_runtime_profile_fails_closed_when_the_setting_is_missing(monkeypatch):
    """No default anywhere: a missing setting is a configuration error."""
    monkeypatch.delattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE")
    with pytest.raises(ConfigError):
        runtime_profile()
