"""Email provider interfaces and message construction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError


@dataclass(frozen=True)
class Attachment:
    path: Path
    mime_type: str = "application/octet-stream"
    filename: str | None = None


@dataclass(frozen=True)
class Email:
    subject: str
    sender: str
    to: Sequence[str]
    text: str | None = None
    html: str | None = None
    cc: Sequence[str] = field(default_factory=list)
    bcc: Sequence[str] = field(default_factory=list)
    attachments: Sequence[Attachment] = field(default_factory=list)


class EmailProvider(ABC):
    @abstractmethod
    def send(self, message: Email, *, dry_run: bool = False) -> EmailMessage:
        """Send a message or return the constructed message in dry-run mode."""


def build_message(message: Email) -> EmailMessage:
    if not message.text and not message.html:
        raise ConfigError("email requires text or HTML content")
    email_message = EmailMessage()
    email_message["Subject"] = message.subject
    email_message["From"] = message.sender
    email_message["To"] = ", ".join(message.to)
    if message.cc:
        email_message["Cc"] = ", ".join(message.cc)
    if message.text:
        email_message.set_content(message.text)
    else:
        email_message.set_content("This message requires an HTML-capable reader.")
    if message.html:
        email_message.add_alternative(message.html, subtype="html")
    for attachment in message.attachments:
        maintype, subtype = attachment.mime_type.split("/", 1)
        email_message.add_attachment(
            attachment.path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename or attachment.path.name,
        )
    return email_message


def provider_from_config(config: Mapping[str, Any]) -> EmailProvider:
    provider = config.get("provider")
    if provider == "google-workspace":
        from parishkit.email.google_workspace import GoogleWorkspaceSMTPProvider

        return GoogleWorkspaceSMTPProvider.from_config(config)
    if provider == "ms365":
        from parishkit.email.ms365 import MS365Provider

        return MS365Provider.from_config(config)
    raise ConfigError("email.provider must be google-workspace or ms365")
