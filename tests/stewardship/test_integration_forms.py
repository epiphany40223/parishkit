"""Integration forms expose only closed public fields and write-only credentials."""

import pytest

from parishkit.stewardship.accounts.integration_forms import (
    CredentialForm,
    InlineCredentialForm,
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
    """UI normalization does not change the frozen canonical configuration schema.

    ParishSoft's organization ID is carried over from its stored settings
    (#1554), never posted.
    """
    stored = {"organization_id": "123"} if target == "parishsoft" else None
    posted = {} if target == "parishsoft" else values
    form = IntegrationForm(target, {"base_digest": "a" * 64} | posted, stored=stored)
    assert form.is_valid(), form.errors
    assert form.public_settings() == expected


@pytest.mark.parametrize(
    ("target", "values"),
    [
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


def test_parishsoft_has_no_organization_field_and_keeps_the_stored_one():
    """The organization is chosen once, in setup (#1554): the settings form
    has no field for it, so a posted one is never read, and the stored ID is
    what every save keeps."""
    digest = {"base_digest": "a" * 64}
    form = IntegrationForm(
        "parishsoft",
        digest | {"organization_id": "456"},
        stored={"organization_id": "123"},
    )
    assert "organization_id" not in form.fields
    assert form.is_valid(), form.errors
    assert form.public_settings()["organization_id"] == "123"
    assert "organization" not in form.as_div().lower()


def test_parishsoft_key_help_names_the_organization():
    """The new key's help names the organization it must reach (#1554)."""
    form = InlineCredentialForm("parishsoft", organization="St. Example Parish (1234)")
    field = form.fields["candidate"]
    tip = str(getattr(field, "tip", field.help_text))
    assert "It must be a key that reaches St. Example Parish (1234); any other" in tip
    unnamed = InlineCredentialForm("parishsoft")
    tip = str(getattr(unnamed.fields["candidate"], "tip", ""))
    assert "the organization chosen in setup" in tip
