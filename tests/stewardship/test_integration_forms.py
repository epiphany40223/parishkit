"""Integration forms expose only closed public fields and write-only credentials."""

import pytest

from parishkit.stewardship.accounts.integration_forms import (
    CredentialForm,
    IntegrationForm,
)


@pytest.mark.parametrize(
    ("target", "values", "expected"),
    [
        (
            "parishsoft",
            {"organization_id": "123"},
            {"organization_id": "123", "nightly_time": "02:00"},
        ),
        (
            "google_workspace",
            {"delegated_email": "MAIL@example.org"},
            {"delegated_email": "mail@example.org"},
        ),
        (
            "email",
            {"sender": "MAIL@example.org", "reply_to": "STAFF@example.org"},
            {"sender": "mail@example.org", "reply_to": "staff@example.org"},
        ),
        ("slack", {"channel_id": "C123"}, {"channel_id": "C123"}),
    ],
)
def test_public_values_use_exact_yaml_types(target, values, expected):
    """UI normalization does not change the frozen canonical configuration schema."""
    form = IntegrationForm(target, {"base_digest": "a" * 64} | values)
    assert form.is_valid(), form.errors
    assert form.public_settings() == expected


@pytest.mark.parametrize(
    ("target", "values"),
    [
        ("parishsoft", {"organization_id": 0}),
        ("parishsoft", {"organization_id": 2**31}),
        ("slack", {"channel_id": "https://example.org/private"}),
        ("google_workspace", {"delegated_email": "invalid"}),
    ],
)
def test_invalid_public_values_cannot_form_patch(target, values):
    """An invalid form cannot be mistaken for a partial settings dictionary."""
    form = IntegrationForm(target, {"base_digest": "a" * 64} | values)
    assert not form.is_valid()
    with pytest.raises(ValueError):
        form.public_settings()


def test_unsupported_target_is_not_an_arbitrary_configuration_editor():
    """OAuth/bootstrap and backup have separate operational owners."""
    with pytest.raises(ValueError):
        IntegrationForm("google_oauth")


def test_candidate_is_write_only_even_when_another_field_is_invalid():
    """Neither a successful binding nor validation error echoes the submitted key."""
    for intent in ("", "signed-intent"):
        form = CredentialForm(
            {"candidate": "SYNTHETIC-PRIVATE-CREDENTIAL", "intent": intent}
        )
        form.is_valid()
        assert "SYNTHETIC-PRIVATE-CREDENTIAL" not in form.as_div()
        assert form.cleaned_data["candidate"] == "SYNTHETIC-PRIVATE-CREDENTIAL"
