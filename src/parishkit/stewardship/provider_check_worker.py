"""Private, bounded connectivity helper; never send mail or Slack notifications.

Input is one parent-validated request over stdin. Output is one closed outcome,
never a token, provider body or exception. The parent owns the wall-clock limit
and forced drainage, including DNS and trickling-response stalls.
"""

import base64
import json
import logging
import smtplib
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace

import requests
from google.auth.exceptions import RefreshError

from parishkit.config import ConfigError
from parishkit.email.google_workspace import xoauth2_string
from parishkit.parishsoft import (
    DEFAULT_API_BASE_URL,
    ParishSoftAPIError,
    ParishSoftConfig,
)
from parishkit.parishsoft_source import CoherentParishSoftClient
from parishkit.parishsoft_transport import ExactSourceResponse
from parishkit.retry import RetryError, RetryPolicy

from .accounts.credential_errors import CredentialValidationUnavailable
from .accounts.cryptography import CryptographicError
from .accounts.integration_candidates import (
    GOOGLE_TOKEN_URI,
    slack_candidate,
    workspace_candidate,
)
from .accounts.key_files import MAX_FILE_BYTES
from .accounts.provider_context import validated_context
from .source.credentials import SourceCredential

MAX_INPUT = MAX_FILE_BYTES * 2 + 4096
MAX_RESPONSE = 65536
OUTCOMES = frozenset({"valid", "invalid", "unavailable"})
ENDPOINTS = frozenset(
    {
        ("POST", GOOGLE_TOKEN_URI),
        ("POST", "https://slack.com/api/auth.test"),
        ("POST", DEFAULT_API_BASE_URL + "/organizations/search"),
    }
)


class CheckSession(requests.Session):
    """Restrict private HTTP calls to fixed provider endpoints and bounded bodies."""

    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        """Forbid redirects/proxies; the subprocess parent supplies a hard timeout."""
        if (method, url) not in ENDPOINTS or set(kwargs) - {
            "headers",
            "data",
            "json",
            "timeout",
        }:
            raise ValueError("Unsupported provider check operation.")
        kwargs["timeout"] = 10
        with super().request(
            method, url, stream=True, allow_redirects=False, **kwargs
        ) as upstream:
            if upstream.status_code == 429 or upstream.status_code >= 500:
                raise CredentialValidationUnavailable()
            if 300 <= upstream.status_code < 400:
                raise ValueError("Provider check redirect is forbidden.")
            content = bytearray()
            for chunk in upstream.iter_content(chunk_size=8192):
                if len(content) + len(chunk) > MAX_RESPONSE:
                    raise ValueError("Provider check response exceeds its bound.")
                content.extend(chunk)
            result = ExactSourceResponse()
            result.status_code = upstream.status_code
            result._content = bytes(content)
            result._content_consumed = True
            result.headers = upstream.headers.copy()
            return result


def _parishsoft(value, settings, session):
    """Reuse ParishKit's uncached exact-tenant validation, with no disk cache."""
    credential = SourceCredential(value)
    client = CoherentParishSoftClient(
        ParishSoftConfig(
            credential.api_key, Path("."), cache_enabled=False, timeout=10
        ),
        organization_id=settings["organization_id"],
        session=session,
        retry_policy=RetryPolicy(attempts=1),
        maximum_requests=1,
        maximum_bytes=MAX_RESPONSE,
        maximum_seconds=30,
    )
    return client.validate_organization_uncached() == settings["organization_id"]


def _workspace(value, settings, session):
    """Authenticate the mailbox; delivery and sender acceptance are separate."""
    credentials = workspace_candidate(
        value, delegated_email=settings["delegated_email"]
    )
    rejected = False

    def token_request(url, method="GET", body=None, headers=None, **kwargs):
        """Adapt the Google transport protocol without accepting alternate origins."""
        nonlocal rejected
        if url != GOOGLE_TOKEN_URI or method != "POST":
            raise ValueError("Unsupported token exchange.")
        response = session.request(method, url, data=body, headers=headers)
        if response.status_code in {400, 401}:
            data = response.json()
            rejected = type(data) is dict and data.get("error") in {
                "invalid_grant",
                "unauthorized_client",
                "invalid_client",
            }
        return SimpleNamespace(
            status=response.status_code, data=response.content, headers=response.headers
        )

    try:
        credentials.refresh(token_request)
    except RefreshError:
        if rejected:
            return False
        raise CredentialValidationUnavailable() from None
    with smtplib.SMTP_SSL(
        "smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()
    ) as smtp:
        code, _ = smtp.ehlo()
        if code != 250:
            raise CredentialValidationUnavailable()
        code, _ = smtp.docmd(
            "AUTH",
            "XOAUTH2 " + xoauth2_string(settings["delegated_email"], credentials.token),
        )
        if code == 334:
            # Gmail's SASL error challenge requires an empty continuation before
            # its definitive 535 reply. Never decode or log the private challenge.
            code, _ = smtp.docmd("")
        if code in {235, 535}:
            return code == 235
        raise CredentialValidationUnavailable()


def _slack(value, settings, session):
    """Check token authentication only; no channel-read scope or message side effect."""
    token = slack_candidate(value)
    response = session.request(
        "POST",
        "https://slack.com/api/auth.test",
        headers={"Authorization": "Bearer " + token},
        data={},
    )
    if response.status_code in {401, 403}:
        return False
    if response.status_code != 200:
        raise CredentialValidationUnavailable()
    body = response.json()
    if type(body) is not dict or type(body.get("ok")) is not bool:
        raise CredentialValidationUnavailable()
    if body["ok"]:
        return True
    if body.get("error") in {
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "account_inactive",
        "token_expired",
    }:
        return False
    raise CredentialValidationUnavailable()


def decode_request(raw):
    """Reject ambiguous/oversized private IPC before constructing provider clients."""
    from .accounts.integration_candidates import _object

    if type(raw) is not bytes or len(raw) > MAX_INPUT:
        raise ValueError("Invalid provider check request.")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if type(request) is not dict or set(request) != {"target", "settings", "candidate"}:
        raise ValueError("Invalid provider check request.")
    settings = validated_context(request["target"], request["settings"])
    value = base64.b64decode(request["candidate"], validate=True)
    if not 0 < len(value) <= MAX_FILE_BYTES:
        raise ValueError("Invalid provider check candidate.")
    return request["target"], settings, value


def check_request(raw):
    """Return a fixed classification; transport outages are not rejected credentials."""
    try:
        target, settings, value = decode_request(raw)
    except (ValueError, TypeError, ConfigError, RecursionError):
        return "invalid"
    try:
        with CheckSession() as session:
            valid = {
                "parishsoft": _parishsoft,
                "google_workspace": _workspace,
                "slack": _slack,
            }[target](value, settings, session)
        return "valid" if valid is True else "invalid"
    except (ConfigError, CryptographicError):
        # Closed candidate parsers reject malformed private bytes locally.
        return "invalid"
    except ParishSoftAPIError as error:
        return "invalid" if error.status_code in {401, 403} else "unavailable"
    except (
        CredentialValidationUnavailable,
        requests.ConnectionError,
        requests.Timeout,
        RetryError,
        OSError,
        smtplib.SMTPServerDisconnected,
    ):
        return "unavailable"
    except Exception:
        # Unknown adapter/DTO/library failures are not evidence of bad credentials.
        # Suppress all private exception text without manufacturing a rejection.
        return "unavailable"


def main():
    """Keep every third-party diagnostic off stdout/stderr in the isolated helper."""
    logging.disable(logging.CRITICAL)
    result = check_request(sys.stdin.buffer.read(MAX_INPUT + 1))
    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
