"""Google Workspace SMTP/XOAUTH2 email provider."""

from __future__ import annotations

import base64
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError
from parishkit.email.base import Email, EmailMessage, EmailProvider, build_message
from parishkit.google.auth import load_service_account_credentials

GMAIL_SMTP_SCOPE = "https://mail.google.com/"


def xoauth2_string(user: str, access_token: str) -> str:
    payload = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def _google_auth_request() -> Any:
    try:
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise ConfigError(
            "Google Workspace email requires parishkit[google] for token refresh"
        ) from exc
    return Request()


@dataclass(frozen=True)
class GoogleWorkspaceSMTPProvider(EmailProvider):
    smtp_host: str
    smtp_port: int
    user: str
    credentials: Any
    smtp_factory: Any = smtplib.SMTP_SSL

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> GoogleWorkspaceSMTPProvider:
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
        credentials = load_service_account_credentials(
            Path(key_file),
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
        email_message = build_message(message)
        if dry_run:
            return email_message
        credentials = self.credentials
        if not getattr(credentials, "valid", False):
            try:
                credentials.refresh(_google_auth_request())
            except Exception as exc:
                raise ConfigError(
                    f"Google Workspace token refresh failed: {exc}"
                ) from exc
        auth = xoauth2_string(self.user, credentials.token)
        recipients = list(message.to) + list(message.cc) + list(message.bcc)
        with self.smtp_factory(self.smtp_host, self.smtp_port) as smtp:
            code, response = smtp.docmd("AUTH", "XOAUTH2 " + auth)
            if code != 235:
                raise ConfigError(f"SMTP XOAUTH2 failed: {code} {response!r}")
            smtp.send_message(
                email_message, from_addr=message.sender, to_addrs=recipients
            )
        return email_message
