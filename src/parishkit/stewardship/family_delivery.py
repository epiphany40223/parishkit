"""Private multi-recipient SMTP submission with explicit, redacted outcomes.

This adapter grants no delivery authority. The outbox owner must commit intent
before invocation and enforce a finite process deadline. In particular, SMTP's
Message-ID is correlation, not a contractual idempotent-send facility.
"""

import smtplib
import ssl
from dataclasses import dataclass
from email import policy
from enum import StrEnum
from uuid import UUID

from parishkit.email.base import Email, build_message
from parishkit.email.google_workspace import xoauth2_string

from .accounts.policy_schema import normalized_email
from .jobs.outbox_validation import mailbox, recipients
from .provider_check_worker import CheckSession
from .readiness_delivery import _credentials
from .web.content import prepare_content


class FamilyDeliveryStatus(StrEnum):
    """Only definitive non-acceptance permits ordinary automatic retry."""

    ACCEPTED = "accepted"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "delivery_unknown"
    SYSTEMIC = "systemic"


@dataclass(frozen=True)
class FamilyDeliveryResult:
    """No response strings or addresses cross the private helper's output pipe.

    Refusals reference positions in the exact admitted envelope, not arbitrary
    addresses. RCPT acceptance alone is never evidence of message acceptance.
    Refusals remain useful even when DATA acceptance is uncertain.
    """

    status: FamilyDeliveryStatus
    recipient_count: int
    permanent: tuple[int, ...] = ()
    transient: tuple[int, ...] = ()

    def __post_init__(self):
        """Reject malformed or contradictory outcomes at the process boundary."""
        if (
            not isinstance(self.status, FamilyDeliveryStatus)
            or type(self.recipient_count) is not int
            or not 1 <= self.recipient_count <= 100
        ):
            raise ValueError("Invalid Family delivery outcome.")
        for indices in (self.permanent, self.transient):
            if (
                type(indices) is not tuple
                or any(type(index) is not int for index in indices)
                or indices != tuple(sorted(set(indices)))
                or any(not 0 <= index < self.recipient_count for index in indices)
            ):
                raise ValueError("Invalid Family recipient outcome.")
        if set(self.permanent) & set(self.transient) or (
            self.status in {FamilyDeliveryStatus.ACCEPTED, FamilyDeliveryStatus.UNKNOWN}
            and len(self.permanent) + len(self.transient) == self.recipient_count
        ):
            raise ValueError("Contradictory Family delivery outcome.")

    def payload(self):
        """The bounded wire result has no secret-bearing error or address field."""
        return {
            "status": self.status.value,
            "recipient_count": self.recipient_count,
            "permanent": list(self.permanent),
            "transient": list(self.transient),
        }

    @classmethod
    def from_payload(cls, value, *, recipient_count):
        """Bind a closed helper result to the parent's exact envelope size."""
        if (
            type(value) is not dict
            or set(value) != {"status", "recipient_count", "permanent", "transient"}
            or type(value["status"]) is not str
            or type(value["permanent"]) is not list
            or type(value["transient"]) is not list
            or value["recipient_count"] != recipient_count
        ):
            raise ValueError("Invalid Family delivery result payload.")
        return cls(
            FamilyDeliveryStatus(value["status"]),
            value["recipient_count"],
            tuple(value["permanent"]),
            tuple(value["transient"]),
        )


@dataclass(frozen=True, repr=False)
class FamilyDeliveryMail:
    """Resolved private mail lives only in memory and the bounded helper pipe."""

    semantic_key: UUID
    sender: str
    reply_to: str
    recipients: tuple[str, ...]
    subject: str
    html: str
    text: str

    def __post_init__(self):
        """Do not admit attachments, injected headers or noncanonical content."""
        if not isinstance(self.semantic_key, UUID):
            raise ValueError("Invalid Family delivery identity.")
        mailbox(self.sender)
        mailbox(self.reply_to)
        object.__setattr__(self, "recipients", recipients(self.recipients))
        if (
            type(self.subject) is not str
            or not self.subject.strip()
            or len(self.subject) > 254
            or any(char in self.subject for char in "\r\n\x00")
        ):
            raise ValueError("Invalid Family delivery subject.")
        content = prepare_content(self.html, text=self.text)
        if content.html != self.html or content.text != self.text:
            raise ValueError("Invalid Family delivery content.")

    def payload(self):
        """Serialize only for a private pipe; never persist or log this value."""
        return {
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
        """Reject unknown fields and noncanonical identities at private IPC."""
        if (
            type(value) is not dict
            or set(value)
            != {
                "semantic_key",
                "sender",
                "reply_to",
                "recipients",
                "subject",
                "html",
                "text",
            }
            or type(value["recipients"]) is not list
            or any(
                type(item) is not str
                for key, item in value.items()
                if key != "recipients"
            )
        ):
            raise ValueError("Invalid Family delivery payload.")
        key = UUID(value["semantic_key"])
        if str(key) != value["semantic_key"]:
            raise ValueError("Invalid Family delivery identity.")
        return cls(**(value | {"semantic_key": key}))

    def message(self):
        """Keep the one-Family envelope in To and reuse shared MIME construction."""
        result = build_message(
            Email(
                sender=self.sender,
                to=self.recipients,
                subject=self.subject,
                html=self.html,
                text=self.text,
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-{self.semantic_key.hex}@parishkit.invalid>"
        )
        return result


def delivery_settings(value):
    """Workspace identity is closed; routed recipients come only from the mail."""
    if type(value) is not dict or set(value) != {
        "delegated_email",
        "sender",
        "reply_to",
    }:
        raise ValueError("Invalid Family delivery settings.")
    if any(normalized_email(item) != item for item in value.values()):
        raise ValueError("Invalid Family delivery settings.")
    return dict(value)


def _reply(value):
    """Discard server prose; a malformed reply cannot fabricate a status code."""
    if type(value) is not tuple or len(value) != 2 or type(value[0]) is not int:
        raise ValueError("Invalid SMTP reply.")
    return value[0]


def _submit(smtp, mail):
    """Preserve per-address refusals across DATA failure without resending a Family.

    Explicit MAIL/RCPT staging distinguishes pre-DATA connection loss (definitely
    unsent) from ambiguous DATA loss. Content is serialized before MAIL so MIME
    errors cannot be confused with an external side effect.
    """
    count = len(mail.recipients)
    permanent, transient = [], []

    def result(status):
        return FamilyDeliveryResult(status, count, tuple(permanent), tuple(transient))

    uncertain = False
    try:
        addresses = (mail.sender, *mail.recipients)
        international = any(not address.isascii() for address in addresses)
        if international and not smtp.has_extn("smtputf8"):
            return result(FamilyDeliveryStatus.SYSTEMIC)
        message = mail.message().as_bytes(
            policy=policy.SMTPUTF8 if international else policy.SMTP
        )
        options = ["SMTPUTF8", "BODY=8BITMIME"] if international else []
        code = _reply(smtp.mail(mail.sender, options=options))
        if code != 250:
            return result(FamilyDeliveryStatus.SYSTEMIC)
        for index, address in enumerate(mail.recipients):
            code = _reply(smtp.rcpt(address))
            if code in (250, 251):
                continue
            if 400 <= code <= 499:
                transient.append(index)
            elif 500 <= code <= 599:
                permanent.append(index)
            else:
                return result(FamilyDeliveryStatus.SYSTEMIC)
        if len(permanent) + len(transient) == count:
            return result(
                FamilyDeliveryStatus.TRANSIENT
                if transient
                else FamilyDeliveryStatus.PERMANENT
            )
        uncertain = True
        try:
            code = _reply(smtp.data(message))
        except smtplib.SMTPDataError as error:
            code = error.smtp_code
            # smtplib also raises here if the preliminary DATA reply was not
            # 354. An anomalous 250 exception is not final message acceptance.
            if type(code) is not int or not 400 <= code <= 599:
                return result(FamilyDeliveryStatus.UNKNOWN)
        if type(code) is int:
            if code == 250:
                return result(FamilyDeliveryStatus.ACCEPTED)
            if 400 <= code <= 499:
                return result(FamilyDeliveryStatus.TRANSIENT)
            if 500 <= code <= 599:
                return result(FamilyDeliveryStatus.PERMANENT)
        return result(FamilyDeliveryStatus.UNKNOWN)
    except Exception:
        return result(
            FamilyDeliveryStatus.UNKNOWN
            if uncertain
            else FamilyDeliveryStatus.TRANSIENT
        )


def deliver_family(
    value,
    settings,
    mail,
    *,
    smtp_factory=smtplib.SMTP_SSL,
    session_factory=CheckSession,
):
    """Perform exactly one fixed-endpoint submission; never log private errors.

    Once DATA has a definitive response, a failing QUIT cannot overturn it.
    Authentication/configuration failures are systemic, not address refusals.
    """
    settings = delivery_settings(settings)
    if not isinstance(mail, FamilyDeliveryMail) or any(
        getattr(mail, name) != settings[name] for name in ("sender", "reply_to")
    ):
        raise ValueError("Family mail differs from its admitted context.")
    result = FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, len(mail.recipients))
    try:
        with session_factory() as session:
            credentials = _credentials(value, settings, session)
        result = FamilyDeliveryResult(
            FamilyDeliveryStatus.TRANSIENT, len(mail.recipients)
        )
        with smtp_factory(
            "smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()
        ) as smtp:
            result = FamilyDeliveryResult(
                FamilyDeliveryStatus.SYSTEMIC, len(mail.recipients)
            )
            if _reply(smtp.ehlo()) != 250:
                return result
            if (
                _reply(
                    smtp.docmd(
                        "AUTH",
                        "XOAUTH2 "
                        + xoauth2_string(
                            settings["delegated_email"], credentials.token
                        ),
                    )
                )
                != 235
            ):
                return result
            result = _submit(smtp, mail)
    except Exception:
        pass
    return result
