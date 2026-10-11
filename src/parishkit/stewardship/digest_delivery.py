"""One-Admin compiled chart mail using the existing SMTP outcome machinery."""

import base64
import smtplib
from dataclasses import dataclass, field
from uuid import UUID

from parishkit.email.base import Email, InlineImage, build_message

from .family_delivery import _deliver_validated, delivery_settings
from .jobs.outbox_validation import mailbox, recipients
from .mail_layout import REPORT_WIDTH, email_document
from .provider_check_worker import CheckSession
from .web.digest_content import (
    CHART_ID,
    COMPARISON_CHART_IDS,
    MAX_CHART_BYTES,
    validate_digest_body,
)


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
    # The Compared with past campaigns section's (content id, PNG) pairs,
    # when the digest has the section (#1018 slice 7).
    comparison: tuple = field(default=(), repr=False)

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
        validate_digest_body(self.html, self.text, self.chart, self.comparison)

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
            "comparison": [
                [name, base64.b64encode(data).decode("ascii")]
                for name, data in self.comparison
            ],
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
                "comparison",
            }
            or value["schema"] != "daily-digest-mail-v1"
            or type(value["recipients"]) is not list
            or any(
                type(item) is not str
                for name, item in value.items()
                if name not in {"recipients", "comparison"}
            )
            or len(value["chart"]) > 4 * ((MAX_CHART_BYTES + 2) // 3)
            or type(value["comparison"]) is not list
            or len(value["comparison"]) > len(COMPARISON_CHART_IDS)
            or any(
                type(pair) is not list
                or len(pair) != 2
                or pair[0] not in COMPARISON_CHART_IDS
                or type(pair[1]) is not str
                or len(pair[1]) > 4 * ((MAX_CHART_BYTES + 2) // 3)
                for pair in value["comparison"]
            )
        ):
            raise ValueError("Invalid digest delivery payload.")
        key = UUID(value["semantic_key"])
        if str(key) != value["semantic_key"]:
            raise ValueError("Invalid digest delivery identity.")
        chart = _decoded(value["chart"])
        comparison = tuple((name, _decoded(text)) for name, text in value["comparison"])
        values = {name: item for name, item in value.items() if name != "schema"}
        return cls(
            **(values | {"semantic_key": key, "chart": chart, "comparison": comparison})
        )

    def message(self):
        """Use shared multipart construction with one fixed CID and no disk reads."""
        result = build_message(
            Email(
                subject=self.subject,
                sender=self.sender,
                to=self.recipients,
                html=email_document(self.html, family=False, width=REPORT_WIDTH),
                text=self.text,
                inline_images=(
                    InlineImage(self.chart, CHART_ID),
                    *(InlineImage(data, name) for name, data in self.comparison),
                ),
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-{self.semantic_key.hex}@parishkit.invalid>"
        )
        return result


def _decoded(text):
    """Canonical base64 of one image, decoded; anything else is refused."""
    data = base64.b64decode(text, validate=True)
    if base64.b64encode(data).decode("ascii") != text:
        raise ValueError("Invalid digest image encoding.")
    return data


def deliver_digest(
    value,
    settings,
    mail,
    *,
    profile,
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
        profile=profile,
        smtp_factory=smtp_factory,
        session_factory=session_factory,
    )
