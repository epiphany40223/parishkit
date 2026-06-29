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
    """A file to attach to an email.

    ``mime_type`` is the full ``type/subtype`` string (defaulting to opaque
    binary). ``filename`` overrides the name shown to the recipient; when
    ``None`` the on-disk file name is used.
    """

    path: Path
    mime_type: str = "application/octet-stream"
    filename: str | None = None


@dataclass(frozen=True)
class Email:
    """A provider-neutral outgoing email message.

    Carries the addressing, subject, body (text and/or HTML), and any
    attachments. Either ``text`` or ``html`` (or both) must be provided; see
    :func:`build_message` for how the parts are assembled.
    """

    subject: str
    sender: str
    to: Sequence[str]
    text: str | None = None
    html: str | None = None
    cc: Sequence[str] = field(default_factory=list)
    bcc: Sequence[str] = field(default_factory=list)
    attachments: Sequence[Attachment] = field(default_factory=list)


class EmailProvider(ABC):
    """Abstract base for backend-specific email senders.

    Concrete providers (Google Workspace, MS365, ...) implement :meth:`send`;
    callers obtain one via :func:`provider_from_config`.
    """

    @abstractmethod
    def send(self, message: Email, *, dry_run: bool = False) -> EmailMessage:
        """Send ``message``, or in dry-run mode build and return it unsent.

        Returning the constructed :class:`EmailMessage` lets callers inspect
        exactly what would be sent without contacting any mail server.
        """


def build_message(message: Email) -> EmailMessage:
    """Assemble a stdlib :class:`EmailMessage` from a provider-neutral Email.

    Builds a multipart/alternative message when HTML is present: the plain-text
    part is set first so non-HTML readers have a fallback, then the HTML part is
    added as an alternative. Attachments are read from disk and attached with
    their declared MIME type. Bcc is intentionally not written as a header here;
    providers pass bcc recipients at the SMTP envelope level. Raises
    :class:`ConfigError` if neither text nor HTML content is supplied.
    """
    if not message.text and not message.html:
        raise ConfigError("email requires text or HTML content")
    email_message = EmailMessage()
    email_message["Subject"] = message.subject
    email_message["From"] = message.sender
    email_message["To"] = ", ".join(message.to)
    if message.cc:
        email_message["Cc"] = ", ".join(message.cc)
    # set_content establishes the message body. When only HTML was supplied we
    # still set a minimal text body so the result is a proper multipart/
    # alternative and text-only clients are not left with an empty message.
    if message.text:
        email_message.set_content(message.text)
    else:
        email_message.set_content("This message requires an HTML-capable reader.")
    if message.html:
        email_message.add_alternative(message.html, subtype="html")
    for attachment in message.attachments:
        # MIME types are "maintype/subtype"; split once so add_attachment gets
        # the two parts it expects.
        maintype, subtype = attachment.mime_type.split("/", 1)
        email_message.add_attachment(
            attachment.path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename or attachment.path.name,
        )
    return email_message


def provider_from_config(
    config: Mapping[str, Any],
    *,
    base_dir: Path | None = None,
) -> EmailProvider:
    """Instantiate the email provider named by the config's ``provider`` key.

    Recognizes ``google-workspace`` (or the underscore spelling) and ``ms365``;
    provider modules are imported lazily so their optional dependencies are only
    required when that provider is actually selected. Raises :class:`ConfigError`
    for an unknown or missing provider.
    """
    provider = config.get("provider")
    if provider in {"google-workspace", "google_workspace"}:
        from parishkit.email.google_workspace import GoogleWorkspaceSMTPProvider

        return GoogleWorkspaceSMTPProvider.from_config(config, base_dir=base_dir)
    if provider == "ms365":
        from parishkit.email.ms365 import MS365Provider

        return MS365Provider.from_config(config)
    raise ConfigError("email.provider must be google-workspace or ms365")
