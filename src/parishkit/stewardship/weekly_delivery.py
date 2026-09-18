"""One-Admin text report mail under the unchanged SMTP certainty boundary."""

import smtplib
from dataclasses import dataclass
from uuid import UUID

from parishkit.email.base import Email, build_message

from .family_delivery import _deliver_validated, delivery_settings
from .jobs.outbox_validation import mailbox, recipients
from .provider_check_worker import CheckSession
from .web.content import bounded_text
from .web.weekly_digest_content import validate_weekly_body


@dataclass(frozen=True, repr=False)
class WeeklyDeliveryMail:
    """A closed, no-attachment report, not arbitrary MIME or a recipient list."""

    semantic_key: UUID
    sender: str
    reply_to: str
    recipients: tuple[str, ...]
    subject: str
    html: str
    text: str

    def __post_init__(self):
        """Reject unsafe data before reading credentials or opening sockets."""
        if not isinstance(self.semantic_key, UUID):
            raise ValueError("Invalid weekly delivery identity.")
        mailbox(self.sender)
        mailbox(self.reply_to)
        object.__setattr__(self, "recipients", recipients(self.recipients))
        if len(self.recipients) != 1:
            raise ValueError("A weekly envelope requires exactly one recipient.")
        if (
            type(self.subject) is not str
            or not self.subject.strip()
            or len(self.subject) > 254
            or any(char in self.subject for char in "\r\n\x00")
        ):
            raise ValueError("Invalid weekly delivery subject.")
        bounded_text(self.subject)
        validate_weekly_body(self.html, self.text)

    def payload(self):
        """Only bounded compiled content enters the private pipe; no file paths."""
        return {
            "schema": "weekly-digest-mail-v1",
            "semantic_key": str(self.semantic_key),
            "sender": self.sender,
            "reply_to": self.reply_to,
            "recipients": list(self.recipients),
            "subject": self.subject,
            "html": self.html,
            "text": self.text,
        }

    @classmethod
    def from_payload(cls, value):
        """Reject unknown fields, cross-purpose payloads and noncanonical identities."""
        if (
            type(value) is not dict
            or set(value)
            != {
                "schema",
                "semantic_key",
                "sender",
                "reply_to",
                "recipients",
                "subject",
                "html",
                "text",
            }
            or value["schema"] != "weekly-digest-mail-v1"
            or type(value["recipients"]) is not list
            or any(
                type(item) is not str
                for name, item in value.items()
                if name != "recipients"
            )
        ):
            raise ValueError("Invalid weekly delivery payload.")
        key = UUID(value["semantic_key"])
        if str(key) != value["semantic_key"]:
            raise ValueError("Invalid weekly delivery identity.")
        values = {name: item for name, item in value.items() if name != "schema"}
        return cls(**(values | {"semantic_key": key}))

    def message(self):
        """Use the shared MIME builder with no images, attachments, or disk reads."""
        result = build_message(
            Email(
                subject=self.subject,
                sender=self.sender,
                to=self.recipients,
                html=self.html,
                text=self.text,
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-{self.semantic_key.hex}@parishkit.invalid>"
        )
        return result


def deliver_weekly(
    value,
    settings,
    mail,
    *,
    smtp_factory=smtplib.SMTP_SSL,
    session_factory=CheckSession,
):
    """Keep DATA acceptance/uncertainty semantics identical to existing mail."""
    settings = delivery_settings(settings)
    if not isinstance(mail, WeeklyDeliveryMail) or any(
        getattr(mail, name) != settings[name] for name in ("sender", "reply_to")
    ):
        raise ValueError("Weekly mail differs from its admitted context.")
    return _deliver_validated(
        value,
        settings,
        mail,
        smtp_factory=smtp_factory,
        session_factory=session_factory,
    )
