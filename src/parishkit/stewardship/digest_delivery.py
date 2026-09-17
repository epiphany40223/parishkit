"""One-Admin compiled chart mail using the existing SMTP outcome machinery."""

import base64
import smtplib
from dataclasses import dataclass, field
from uuid import UUID

from parishkit.email.base import Email, InlineImage, build_message

from .family_delivery import _deliver_validated, delivery_settings
from .jobs.outbox_validation import mailbox, recipients
from .provider_check_worker import CheckSession
from .web.digest_content import CHART_ID, MAX_CHART_BYTES, validate_digest_body


@dataclass(frozen=True, repr=False)
class DigestDeliveryMail:
    """A private compiled mail, not an arbitrary attachment or multi-Admin envelope."""

    semantic_key: UUID
    sender: str
    reply_to: str
    recipients: tuple[str, ...]
    subject: str
    html: str
    text: str
    chart: bytes = field(repr=False)

    def __post_init__(self):
        """Validate the closed MIME contract before credentials or sockets are used."""
        if not isinstance(self.semantic_key, UUID):
            raise ValueError("Invalid digest delivery identity.")
        mailbox(self.sender)
        mailbox(self.reply_to)
        object.__setattr__(self, "recipients", recipients(self.recipients))
        if len(self.recipients) != 1:
            raise ValueError("A digest envelope requires exactly one recipient.")
        if (
            type(self.subject) is not str
            or not self.subject.strip()
            or len(self.subject) > 254
            or any(char in self.subject for char in "\r\n\x00")
        ):
            raise ValueError("Invalid digest delivery subject.")
        validate_digest_body(self.html, self.text, self.chart)

    def payload(self):
        """Encode a closed in-memory payload only for the bounded private pipe."""
        return {
            "schema": "daily-digest-mail-v1",
            "semantic_key": str(self.semantic_key),
            "sender": self.sender,
            "reply_to": self.reply_to,
            "recipients": list(self.recipients),
            "subject": self.subject,
            "html": self.html,
            "text": self.text,
            "chart": base64.b64encode(self.chart).decode("ascii"),
        }

    @classmethod
    def from_payload(cls, value):
        """No filesystem path, raw MIME, SMTP endpoint or unknown field is accepted."""
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
                "chart",
            }
            or value["schema"] != "daily-digest-mail-v1"
            or type(value["recipients"]) is not list
            or any(
                type(item) is not str
                for name, item in value.items()
                if name != "recipients"
            )
            or len(value["chart"]) > 4 * ((MAX_CHART_BYTES + 2) // 3)
        ):
            raise ValueError("Invalid digest delivery payload.")
        key = UUID(value["semantic_key"])
        if str(key) != value["semantic_key"]:
            raise ValueError("Invalid digest delivery identity.")
        chart = base64.b64decode(value["chart"], validate=True)
        if base64.b64encode(chart).decode("ascii") != value["chart"]:
            raise ValueError("Invalid digest image encoding.")
        values = {name: item for name, item in value.items() if name != "schema"}
        return cls(**(values | {"semantic_key": key, "chart": chart}))

    def message(self):
        """Use shared multipart construction with one fixed CID and no disk reads."""
        result = build_message(
            Email(
                subject=self.subject,
                sender=self.sender,
                to=self.recipients,
                html=self.html,
                text=self.text,
                inline_images=(InlineImage(self.chart, CHART_ID),),
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-{self.semantic_key.hex}@parishkit.invalid>"
        )
        return result


def deliver_digest(
    value,
    settings,
    mail,
    *,
    smtp_factory=smtplib.SMTP_SSL,
    session_factory=CheckSession,
):
    """Use identical DATA certainty semantics without weakening Family mail input."""
    settings = delivery_settings(settings)
    if not isinstance(mail, DigestDeliveryMail) or any(
        getattr(mail, name) != settings[name] for name in ("sender", "reply_to")
    ):
        raise ValueError("Digest mail differs from its admitted context.")
    return _deliver_validated(
        value,
        settings,
        mail,
        smtp_factory=smtp_factory,
        session_factory=session_factory,
    )
