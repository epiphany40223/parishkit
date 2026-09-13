"""Closed fictional readiness mail, always routed to one explicit test address.

This is message construction, not authorization, queueing or provider delivery.
The owning worker must persist intent and submitting state before provider IO.
No real Family lookup, live code, attachment or arbitrary header is supported.
"""

from dataclasses import dataclass
from html import escape
from uuid import UUID

from parishkit.email.base import Email, build_message

from .accounts.policy_schema import normalized_email
from .web.content import prepare_content, validate_template


@dataclass(frozen=True, repr=False)
class ReadinessMail:
    """One bounded safe sample plus immutable, normalized test delivery scope."""

    delivery_id: UUID
    sender: str
    reply_to: str
    recipient: str
    subject: str
    html: str
    text: str

    def __post_init__(self):
        """Restored queue payloads receive the same checks as initial rendering."""
        if not isinstance(self.delivery_id, UUID):
            raise ValueError("An explicit readiness delivery UUID is required.")
        if any(
            normalized_email(value) != value
            for value in (self.sender, self.reply_to, self.recipient)
        ):
            raise ValueError("Readiness delivery addresses must be normalized.")
        if type(self.subject) is not str or not self.subject.strip():
            raise ValueError("Readiness mail requires a subject.")
        validate_template(self.subject, subject=True)
        prepared = prepare_content(self.html, text=self.text)
        if prepared.html != self.html or prepared.text != self.text:
            raise ValueError("Readiness mail must contain canonical safe content.")

    def payload(self):
        """Return only closed serializable public input; there are no secret fields."""
        return {
            "delivery_id": str(self.delivery_id),
            "sender": self.sender,
            "reply_to": self.reply_to,
            "recipient": self.recipient,
            "subject": self.subject,
            "html": self.html,
            "text": self.text,
        }

    @classmethod
    def from_payload(cls, value):
        """Reject unknown fields, coercions and alternate UUID spellings at IPC."""
        fields = {
            "delivery_id",
            "sender",
            "reply_to",
            "recipient",
            "subject",
            "html",
            "text",
        }
        if (
            type(value) is not dict
            or set(value) != fields
            or any(type(item) is not str for item in value.values())
        ):
            raise ValueError("Invalid readiness mail payload.")
        identifier = UUID(value["delivery_id"])
        if str(identifier) != value["delivery_id"]:
            raise ValueError("Invalid readiness mail identity.")
        return cls(**(value | {"delivery_id": identifier}))

    def message(self):
        """Reuse shared MIME construction with mandatory TEST subject/body overrides.

        A stable Message-ID is correlation only. SMTP has no contractual
        idempotent-send facility; this header never justifies an automatic resend.
        """
        banner = (
            "TEST — readiness sample, not a live Family message. "
            + f"Sent only to {self.recipient}. "
            + "Family names and access values are fictional."
        )
        result = build_message(
            Email(
                subject="[TEST] " + self.subject,
                sender=self.sender,
                to=[self.recipient],
                html="<h2>TEST — readiness sample</h2><p>"
                + escape(banner)
                + "</p>"
                + self.html,
                text=banner + "\n\n" + self.text,
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-readiness-{self.delivery_id.hex}@parishkit.invalid>"
        )
        return result
