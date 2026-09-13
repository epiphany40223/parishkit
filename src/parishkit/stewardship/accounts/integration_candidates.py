"""Local parsing for private integration candidates before bounded provider checks.

These helpers do not attest to connectivity, send messages, install files or
mark setup ready. The target-isolated test owner supplies those operations.
"""

import json

from parishkit.config import ConfigError
from parishkit.email.google_workspace import GMAIL_SMTP_SCOPE
from parishkit.google.auth import load_service_account_info

from .key_files import MAX_FILE_BYTES
from .policy_schema import normalized_email

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_FIELDS = frozenset(
    {
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "auth_uri",
        "token_uri",
        "auth_provider_x509_cert_url",
        "client_x509_cert_url",
        "universe_domain",
    }
)


def _object(pairs):
    """A duplicate endpoint or private-key field cannot select conflicting values."""
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate credential field.")
        result[name] = value
    return result


def workspace_info(value):
    """Parse bounded UTF-8 service-account JSON with a fixed OAuth token endpoint.

    A service-account upload is not permission to send its signed JWT to an
    arbitrary token URI or alternate universe. Other document URLs are inert
    metadata; this workflow never fetches certificate/discovery URLs from it.
    """
    try:
        if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
            raise ValueError
        info = json.loads(value.decode("utf-8"), object_pairs_hook=_object)
        if (
            type(info) is not dict
            or set(info) - GOOGLE_FIELDS
            or any(type(item) is not str or "\x00" in item for item in info.values())
            or info.get("type") != "service_account"
            or info.get("token_uri") != GOOGLE_TOKEN_URI
            or info.get("universe_domain", "googleapis.com") != "googleapis.com"
            or not all(
                info.get(name) for name in ("private_key", "client_email", "client_id")
            )
        ):
            raise ValueError
        normalized_email(info["client_email"])
        return info
    except (ValueError, TypeError, UnicodeError, RecursionError, ConfigError):
        raise ConfigError(
            "Google Workspace credential information is invalid."
        ) from None


def workspace_candidate(value, *, delegated_email):
    """Construct scoped in-memory credentials without a plaintext temporary file."""
    subject = normalized_email(delegated_email)
    return load_service_account_info(
        workspace_info(value), scopes=[GMAIL_SMTP_SCOPE], subject=subject
    )


def slack_candidate(value):
    """Accept one bounded ASCII token, never interpreting it as a URL or file path."""
    if type(value) is not bytes or not 1 <= len(value) <= 4098:
        raise ConfigError("Slack credential is invalid.")
    trimmed = (
        value.removesuffix(b"\r\n")
        if value.endswith(b"\r\n")
        else value.removesuffix(b"\n")
    )
    if not 1 <= len(trimmed) <= 4096 or any(
        byte < 33 or byte > 126 for byte in trimmed
    ):
        raise ConfigError("Slack credential is invalid.")
    return trimmed.decode("ascii")
