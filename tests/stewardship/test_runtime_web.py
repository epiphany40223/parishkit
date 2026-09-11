"""Runtime settings must satisfy the actual consumer, not a parallel test fixture."""

from allauth.socialaccount.adapter import _build_apps_from_settings

from parishkit.stewardship.runtime_web import (
    google_provider_settings,
    parse_google_client,
)


def test_operator_oauth_document_populates_allauth_secret_without_database(settings):
    """Keep the operator file format while translating the library's secret field."""
    oauth = parse_google_client(
        b'{"client_id":"synthetic-client","client_secret":"synthetic-secret"}'
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
