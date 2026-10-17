"""Source reads learn their deployment profile from runtime assembly, or fail closed."""

import pytest
from django.conf import settings

from parishkit.config import ConfigError
from parishkit.parishsoft import DEFAULT_API_BASE_URL
from parishkit.parishsoft_http_worker import LOCAL_SOURCE_BASE_URL
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.source.transport import runtime_base_url, runtime_profile


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


@pytest.mark.parametrize("profile", list(DeploymentProfile))
def test_runtime_base_url_defaults_to_the_profiles_own(monkeypatch, profile):
    """With no recorded choice, LOCAL reads the fake and every other profile
    the real API, as before the setting existed (#1399)."""
    monkeypatch.setattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", profile.value)
    monkeypatch.delattr(settings, "STEWARDSHIP_PARISHSOFT_BASE_URL", raising=False)
    local = profile is DeploymentProfile.LOCAL
    assert runtime_base_url() == (
        LOCAL_SOURCE_BASE_URL if local else DEFAULT_API_BASE_URL
    )


def test_runtime_base_url_reads_the_recorded_choice(monkeypatch):
    """A LOCAL deployment that chose the real API reads from it."""
    monkeypatch.setattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", "local")
    monkeypatch.setattr(
        settings, "STEWARDSHIP_PARISHSOFT_BASE_URL", DEFAULT_API_BASE_URL, raising=False
    )
    assert runtime_base_url() == DEFAULT_API_BASE_URL


@pytest.mark.parametrize(
    ("profile", "value"),
    [
        ("production", LOCAL_SOURCE_BASE_URL),
        ("test", LOCAL_SOURCE_BASE_URL),
        ("local", "https://other.example/api/v2"),
        ("local", ""),
    ],
)
def test_runtime_base_url_fails_closed_on_a_choice_the_profile_may_not_make(
    monkeypatch, profile, value
):
    """The recorded choice is checked against the recorded profile again."""
    monkeypatch.setattr(settings, "STEWARDSHIP_DEPLOYMENT_PROFILE", profile)
    monkeypatch.setattr(
        settings, "STEWARDSHIP_PARISHSOFT_BASE_URL", value, raising=False
    )
    with pytest.raises(ConfigError):
        runtime_base_url()
