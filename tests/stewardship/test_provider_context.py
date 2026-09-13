"""Provider validation receives exact reviewed metadata, not arbitrary API options."""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.provider_context import validated_context

WORKSPACE = {
    "delegated_email": "mail@example.org",
    "sender": "mail@example.org",
    "reply_to": "staff@example.org",
    "recipient": "test@example.org",
}


@pytest.mark.parametrize(
    ("target", "values"),
    [
        ("parishsoft", {"organization_id": 123}),
        ("slack", {"channel_id": "C123456789"}),
        ("google_workspace", WORKSPACE),
    ],
)
def test_context_is_copied_and_canonical(target, values):
    """A mutable caller cannot change the validated mapping by aliasing it."""
    result = validated_context(target, values)
    assert result == values and result is not values


@pytest.mark.parametrize(
    ("target", "values"),
    [
        (None, {}),
        ("unknown", {}),
        ("parishsoft", []),
        ("parishsoft", {"organization_id": True}),
        ("parishsoft", {"organization_id": "123"}),
        ("parishsoft", {"organization_id": 0}),
        ("parishsoft", {"organization_id": 2**31}),
        ("parishsoft", {"organization_id": 1, "api_key": "forbidden"}),
        ("slack", {"channel_id": "https://evil.example"}),
        ("slack", {"channel_id": None}),
        ("google_workspace", WORKSPACE | {"recipient": "TEST@example.org"}),
        ("google_workspace", WORKSPACE | {"recipient": "invalid"}),
        ("google_workspace", WORKSPACE | {"recipient": None}),
        ("google_workspace", WORKSPACE | {"token_uri": "https://evil.example"}),
    ],
)
def test_invalid_context_has_no_private_diagnostics(target, values):
    """Even a misplaced secret is rejected without echoing its value or field name."""
    with pytest.raises(ConfigError, match="^Invalid provider validation context.$"):
        validated_context(target, values)
