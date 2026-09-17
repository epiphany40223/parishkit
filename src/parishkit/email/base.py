"""Email provider interfaces and message construction."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError, reject_unknown_keys

MAX_INLINE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class InlineImage:
    """An in-memory raster image referenced by ``cid:content_id`` in HTML.

    This MIME helper never opens a path. The caller owns image decoding and
    content safety; the builder bounds bytes and validates routing headers.
    SVG/active formats are deliberately not supported by this convenience API.
    """

    data: bytes = field(repr=False)
    content_id: str
    mime_type: str = "image/png"

    def __post_init__(self):
        """Reject ambiguous IDs, unsupported image types and oversized payloads."""
        if (
            type(self.data) is not bytes
            or not 0 < len(self.data) <= MAX_INLINE_BYTES
            or type(self.content_id) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}", self.content_id)
            or type(self.mime_type) is not str
            or self.mime_type
            not in {"image/png", "image/jpeg", "image/gif", "image/webp"}
        ):
            raise ConfigError(
                "Inline image requires bounded raster bytes and a safe ID"
            )


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
    inline_images: Sequence[InlineImage] = field(default_factory=list)


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
    added as an alternative. Inline images form a related part of that HTML
    alternative, leaving the text fallback and ordinary attachments separate.
    Attachments are read from disk and attached with their declared MIME type.
    Bcc is intentionally not written as a header here;
    providers pass bcc recipients at the SMTP envelope level. Raises
    :class:`ConfigError` if neither text nor HTML content is supplied.
    """
    if not message.text and not message.html:
        raise ConfigError("email requires text or HTML content")
    inline_images = tuple(message.inline_images)
    if inline_images and (
        not message.html
        or len(inline_images) > 16
        or any(not isinstance(image, InlineImage) for image in inline_images)
        or len({image.content_id for image in inline_images}) != len(inline_images)
        or sum(len(image.data) for image in inline_images) > MAX_INLINE_BYTES
    ):
        raise ConfigError(
            "Inline images require HTML, unique IDs and bounded total bytes"
        )
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
        html_part = email_message.get_payload()[-1]
        for image in inline_images:
            html_part.add_related(
                image.data,
                maintype="image",
                subtype=image.mime_type.split("/", 1)[1],
                cid=f"<{image.content_id}>",
                disposition="inline",
            )
    for attachment in message.attachments:
        # MIME types are "maintype/subtype"; split once so add_attachment gets
        # the two parts it expects.
        try:
            maintype, subtype = attachment.mime_type.split("/", 1)
        except ValueError as exc:
            raise ConfigError(
                f"attachment MIME type must be type/subtype: {attachment.mime_type}"
            ) from exc
        if not maintype or not subtype:
            raise ConfigError(
                f"attachment MIME type must be type/subtype: {attachment.mime_type}"
            )
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

    Recognizes ``google-workspace`` (or the underscore spelling). Provider
    modules are imported lazily so optional dependencies are only required when
    that provider is actually selected. Raises :class:`ConfigError` for an
    unknown, missing, or not-yet-implemented provider.
    """
    provider = config.get("provider")
    if provider in {"google-workspace", "google_workspace"}:
        reject_unknown_keys(
            config,
            {
                "provider",
                "service_account_file",
                "delegated_user",
                "user",
                "smtp_host",
                "smtp_port",
            },
            "email",
        )
        from parishkit.email.google_workspace import GoogleWorkspaceSMTPProvider

        return GoogleWorkspaceSMTPProvider.from_config(config, base_dir=base_dir)
    if provider == "ms365":
        reject_unknown_keys(
            config,
            {"provider", "tenant_id", "client_id", "client_secret_file", "sender"},
            "email",
        )
        raise ConfigError(
            "email.provider ms365 is not implemented yet; use google-workspace"
        )
    raise ConfigError("email.provider must be google-workspace")
