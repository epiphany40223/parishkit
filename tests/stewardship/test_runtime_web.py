"""Runtime settings must satisfy the actual consumer, not a parallel test fixture."""

import pytest
from allauth.socialaccount.adapter import _build_apps_from_settings

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.runtime_web import (
    LOCAL_OAUTH_DOCUMENT,
    LOCAL_OAUTH_SENTINEL,
    fake_clock,
    google_provider_settings,
    parse_google_client,
)


def test_operator_oauth_document_populates_allauth_secret_without_database(settings):
    """Keep the operator file format while translating the library's secret field."""
    oauth = parse_google_client(
        b'{"client_id":"synthetic-client","client_secret":"synthetic-secret"}',
        profile=DeploymentProfile.DEVELOPMENT,
    )
    settings.SOCIALACCOUNT_PROVIDERS = google_provider_settings(oauth)
    app = _build_apps_from_settings(provider="google")["google"][0]
    assert app.client_id == "synthetic-client"
    assert app.secret == "synthetic-secret"
    assert app.key == ""
    assert oauth == {
        "client_id": "synthetic-client",
        "client_secret": "synthetic-secret",
    }


# The LOCAL sentinel OAuth client (#476, OPS-10.05) and a real one (#1399).
@pytest.mark.parametrize("profile", list(DeploymentProfile))
def test_sentinel_oauth_client_is_admitted_only_in_local(profile):
    """Every profile takes a real client; only LOCAL takes the sentinel."""
    real = b'{"client_id":"synthetic-client","client_secret":"synthetic-secret"}'
    assert parse_google_client(real, profile=profile) == {
        "client_id": "synthetic-client",
        "client_secret": "synthetic-secret",
    }
    if profile is DeploymentProfile.LOCAL:
        assert parse_google_client(LOCAL_OAUTH_DOCUMENT, profile=profile) == (
            LOCAL_OAUTH_SENTINEL
        )
    else:
        with pytest.raises(ConfigError, match="only in the local profile"):
            parse_google_client(LOCAL_OAUTH_DOCUMENT, profile=profile)
    # A malformed document is refused the same way under every profile.
    with pytest.raises(ConfigError, match="file is invalid"):
        parse_google_client(b'{"client_id": 1}', profile=profile)


def test_fake_clock_is_read_from_the_libfaketime_variables():
    """Only libfaketime's own variables mark the fake clock, not any preload."""
    assert fake_clock({"FAKETIME_TIMESTAMP_FILE": "/run/parishkit-clock/offset"})
    assert not fake_clock({})
    assert not fake_clock({"LD_PRELOAD": "/usr/lib/libjemalloc.so.2", "PATH": "/"})


def test_sentinel_holds_no_real_client_and_the_parser_needs_a_profile():
    """The sentinel's values are visibly not Google's, and no profile means no parse."""
    assert LOCAL_OAUTH_SENTINEL["client_id"].endswith(".invalid")
    assert "not-a-secret" in LOCAL_OAUTH_SENTINEL["client_secret"]
    assert "googleusercontent" not in LOCAL_OAUTH_SENTINEL["client_id"]
    with pytest.raises(ConfigError, match="requires a deployment profile"):
        parse_google_client(LOCAL_OAUTH_DOCUMENT, profile=None)
