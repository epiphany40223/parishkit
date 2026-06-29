"""Google Workspace SMTP/XOAUTH2 email provider."""

from __future__ import annotations

import base64
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError, resolve_path
from parishkit.email.base import Email, EmailMessage, EmailProvider, build_message
from parishkit.google.auth import load_service_account_credentials

GMAIL_SMTP_SCOPE = "https://mail.google.com/"


def xoauth2_string(user: str, access_token: str) -> str:
    """Build the base64 SASL XOAUTH2 string for SMTP AUTH.

    Encodes the user and bearer token in the ``user=...^Aauth=Bearer ...^A^A``
    layout Gmail's XOAUTH2 mechanism expects (``\\x01`` is the control-A
    separator), then base64-encodes it as required by the AUTH command.
    """
    payload = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def _google_auth_request() -> Any:
    """Create the transport Request object used to refresh OAuth tokens.

    Imported lazily so the optional Google dependencies are only required when a
    token actually needs refreshing; a missing install raises
    :class:`ConfigError`.
    """
    try:
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise ConfigError(
            "Google Workspace email requires parishkit[google] for token refresh"
        ) from exc
    return Request()


@dataclass(frozen=True)
class GoogleWorkspaceSMTPProvider(EmailProvider):
    """Send mail via Gmail SMTP using OAuth2 (XOAUTH2) authentication.

    Authenticates with a service account that has domain-wide delegation to act
    as ``user`` (the delegated mailbox), avoiding stored passwords.
    ``smtp_factory`` is injectable so tests can substitute a fake SMTP class.
    """

    smtp_host: str
    smtp_port: int
    user: str
    credentials: Any
    smtp_factory: Any = smtplib.SMTP_SSL

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        base_dir: Path | None = None,
    ) -> GoogleWorkspaceSMTPProvider:
        """Build the provider from YAML configuration.

        Requires ``service_account_file`` and a delegated user (``delegated_user``
        or ``user``); SMTP host/port default to Gmail's SSL endpoint. Loads
        service-account credentials scoped for Gmail and impersonating that
        user. Raises :class:`ConfigError` on missing or mistyped settings.
        """
        key_file = config.get("service_account_file")
        user = config.get("delegated_user") or config.get("user")
        if not isinstance(key_file, str) or not isinstance(user, str):
            raise ConfigError(
                "google-workspace email requires service_account_file "
                "and delegated_user"
            )
        smtp_host = config.get("smtp_host", "smtp.gmail.com")
        smtp_port = config.get("smtp_port", 465)
        if not isinstance(smtp_host, str):
            raise ConfigError("google-workspace smtp_host must be a string")
        if not isinstance(smtp_port, int):
            raise ConfigError("google-workspace smtp_port must be an integer")
        key_path = resolve_path(
            key_file,
            "email.service_account_file",
            base_dir=base_dir,
        )
        credentials = load_service_account_credentials(
            key_path,
            scopes=[GMAIL_SMTP_SCOPE],
            subject=user,
        )
        return cls(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            user=user,
            credentials=credentials,
        )

    def send(self, message: Email, *, dry_run: bool = False) -> EmailMessage:
        """Send ``message`` via Gmail SMTP, or return it unsent in dry-run mode.

        Refreshes the OAuth token if it is not currently valid, authenticates
        with XOAUTH2, and delivers to the combined to/cc/bcc envelope. Returns
        the constructed :class:`EmailMessage`. Raises :class:`ConfigError` if
        the token refresh fails or the SMTP server rejects authentication.
        """
        email_message = build_message(message)
        if dry_run:
            return email_message
        credentials = self.credentials
        # Refresh only when needed: a freshly loaded service-account credential
        # has no access token yet, and a cached one may have expired.
        if not getattr(credentials, "valid", False):
            try:
                credentials.refresh(_google_auth_request())
            except Exception as exc:
                raise ConfigError(
                    f"Google Workspace token refresh failed: {exc}"
                ) from exc
        auth = xoauth2_string(self.user, credentials.token)
        # bcc recipients go on the SMTP envelope only, never in a header, so
        # they stay hidden from other recipients.
        recipients = list(message.to) + list(message.cc) + list(message.bcc)
        with self.smtp_factory(self.smtp_host, self.smtp_port) as smtp:
            # Gmail requires the client greeting before AUTH. smtplib normally
            # sends EHLO lazily for high-level login helpers, but XOAUTH2 uses a
            # manual AUTH command here, so the greeting must be explicit.
            smtp.ehlo()
            # Issue the AUTH command manually because smtplib has no built-in
            # XOAUTH2 helper; 235 is the SMTP "authentication succeeded" code.
            code, response = smtp.docmd("AUTH", "XOAUTH2 " + auth)
            if code != 235:
                raise ConfigError(f"SMTP XOAUTH2 failed: {code} {response!r}")
            smtp.send_message(
                email_message, from_addr=message.sender, to_addrs=recipients
            )
        return email_message
