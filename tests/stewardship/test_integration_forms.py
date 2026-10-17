"""Integration forms expose only closed public fields and write-only credentials."""

import pytest

from parishkit.stewardship.accounts.integration_forms import (
    ORGANIZATION_LOCKED,
    ORGANIZATION_LOCKED_TIP,
    CredentialForm,
    IntegrationForm,
)


@pytest.mark.parametrize(
    ("target", "values", "expected"),
    [
        (
            "parishsoft",
            {"organization_id": "123"},
            # No schedule rows posted: the stored schedule (none) is kept and
            # nothing new is written (#632).
            {"organization_id": "123"},
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
        (
            "backup",
            {
                "target": "https://drive.google.com/drive/u/0/folders/"
                "1AbCdEfGhIjKlMnOpQrStUv?usp=sharing"
            },
            {
                "target": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv"
            },
        ),
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
        ("backup", {"target": "https://docs.google.com/document/d/1AbCdEfGhIjKl"}),
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


def test_loaded_organization_is_disabled_with_its_reason_and_cannot_change():
    """Once ParishSoft data is loaded, the organization ID is a disabled field
    showing the loaded ID, with a one-line reason under its label (#1492).

    The browser sends no disabled field, so the loaded ID is the value; a
    request that posts another ID anyway is refused, never quietly ignored.
    """
    digest = {"base_digest": "a" * 64}
    unsent = IntegrationForm("parishsoft", digest, loaded_organization=123)
    assert unsent.is_valid(), unsent.errors
    assert unsent.public_settings()["organization_id"] == "123"
    field = unsent["organization_id"]
    html = str(field)
    assert " disabled" in html and 'value="123"' in html
    assert str(ORGANIZATION_LOCKED) in unsent.as_div()
    assert str(ORGANIZATION_LOCKED_TIP) in unsent.as_div()
    assert "readonly" not in html
    same = IntegrationForm(
        "parishsoft", digest | {"organization_id": "123"}, loaded_organization=123
    )
    assert same.is_valid(), same.errors
    for forged in ("456", "x"):
        other = IntegrationForm(
            "parishsoft", digest | {"organization_id": forged}, loaded_organization=123
        )
        assert not other.is_valid()
        assert other.has_error("organization_id", "organization_locked")
        assert "Keep 123 here." in other.errors["organization_id"][0]
    before = IntegrationForm("parishsoft", digest | {"organization_id": "456"})
    assert before.is_valid(), before.errors
    assert " disabled" not in str(before["organization_id"])
    assert str(ORGANIZATION_LOCKED) not in before.as_div()
