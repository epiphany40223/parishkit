"""Private local parsing is not provider authentication or configuration readiness."""

import json
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.integration_candidates import (
    GOOGLE_TOKEN_URI,
    slack_candidate,
    workspace_candidate,
    workspace_info,
)


def account(**overrides):
    """Synthetic service-account shape; key parsing is independently exercised."""
    return json.dumps(
        {
            "type": "service_account",
            "private_key": "synthetic-private-value",
            "client_email": "service@example.iam.gserviceaccount.com",
            "client_id": "123456789",
            "token_uri": GOOGLE_TOKEN_URI,
            **overrides,
        }
    ).encode()


def test_workspace_candidate_reuses_shared_memory_loader(monkeypatch):
    """Only the fixed mail scope and explicit normalized delegated mailbox are used."""
    loader = Mock(return_value=object())
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.integration_candidates.load_service_account_info",
        loader,
    )
    result = workspace_candidate(account(), delegated_email="MAIL@example.org")
    loader.assert_called_once_with(
        workspace_info(account()),
        scopes=["https://mail.google.com/"],
        subject="mail@example.org",
    )
    assert result is loader.return_value


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"not-json",
        b"[]",
        b"\xff",
        b"x" * 131073,
        b'{"type":"service_account","type":"authorized_user"}',
        account(type="authorized_user"),
        account(token_uri="http://127.0.0.1/token"),
        account(token_uri="https://oauth2.googleapis.com.evil.example/token"),
        account(universe_domain="evil.example"),
        account(private_key=None),
        account(client_email="invalid"),
        account(arbitrary_secret="unknown"),
    ],
)
def test_workspace_candidate_rejects_ambiguous_or_redirected_credentials(value):
    """No private parser message or submitted secret can escape a rejection."""
    with pytest.raises(ConfigError) as error:
        workspace_info(value)
    assert str(error.value) == "Google Workspace credential information is invalid."
    assert error.value.__suppress_context__


def test_workspace_candidate_constructs_real_credentials_without_network():
    """A synthetic RSA key exercises google-auth's actual memory-only constructor."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    credentials = workspace_candidate(
        account(private_key=pem), delegated_email="mail@example.org"
    )
    assert not credentials.valid and credentials.token is None
    assert (
        credentials.service_account_email == "service@example.iam.gserviceaccount.com"
    )


@pytest.mark.parametrize("value", [b"test-token", b"test-token\n", b"test-token\r\n"])
def test_slack_candidate_accepts_only_one_terminal_line_ending(value):
    """The exact file bytes retain their receipt; only transport text is trimmed."""
    assert slack_candidate(value) == "test-token"


@pytest.mark.parametrize(
    "value",
    [b"", b"\n", b"token\n\n", b"token with space", b"\xff", b"x" * 4099, "not-bytes"],
)
def test_slack_candidate_rejects_invalid_private_bytes(value):
    """Invalid values never appear in the exception text."""
    with pytest.raises(ConfigError, match="^Slack credential is invalid.$"):
        slack_candidate(value)
