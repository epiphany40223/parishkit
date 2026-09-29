"""Private multi-recipient SMTP submission with explicit, redacted outcomes.

This adapter grants no delivery authority. The outbox owner must commit intent
before invocation and enforce a finite process deadline. In particular, SMTP's
Message-ID is correlation, not a contractual idempotent-send facility.
"""

import re
import smtplib
import ssl
from dataclasses import dataclass
from email import policy
from enum import StrEnum
from uuid import UUID

import requests
from google.auth.exceptions import RefreshError, TransportError

from parishkit.email.base import Email, build_message
from parishkit.email.google_workspace import xoauth2_string

from .accounts.credential_errors import CredentialValidationUnavailable
from .accounts.policy_schema import normalized_email
from .jobs.outbox_validation import mailbox, recipients
from .mail_layout import email_document
from .provider_check_worker import CheckSession
from .readiness_delivery import _credentials
from .sender_name import apply_sender_name, clean_sender_name
from .web.content import prepare_content, without_email_banner


class FamilyDeliveryStatus(StrEnum):
    """Only definitive non-acceptance permits ordinary automatic retry."""

    ACCEPTED = "accepted"
    TRANSIENT = "transient"
    UNAVAILABLE = "unavailable"
    PERMANENT = "permanent"
    UNKNOWN = "delivery_unknown"
    SYSTEMIC = "systemic"


class ProviderHealth(StrEnum):
    """Connection health is independent of whether DATA acceptance is known."""

    HEALTHY = "healthy"
    UNOBSERVED = "unobserved"
    UNAVAILABLE = "unavailable"
    SYSTEMIC = "systemic"


# Gmail's own sending limits for the whole sending mailbox, recognized only by
# the RFC 3463 enhanced status code that starts a reply line (the prose is
# never retained). "5.4.5" is the daily user sending limit (despite its 5xx
# code it clears as the rolling day moves on). "421 4.7.x" is Gmail closing the
# connection for a sending-rate limit, at any stage, and "454 4.7.x" in reply
# to AUTH is its login-rate limit. Other 4.7.x replies (for example to one
# RCPT) stay ordinary per-message or per-address temporary refusals.
_DAILY_LIMIT = re.compile(rb"(?m)^[ \t]*5\.4\.5(?=\s|$)")
_RATE_LIMIT = re.compile(rb"(?m)^[ \t]*4\.7\.[0-9]{1,3}(?=\s|$)")
SENDING_LIMITS = frozenset({"daily", "rate"})


def sending_limit(reply, *, stage=""):
    """Return "daily", "rate" or None for one raw ``(code, message)`` reply.

    ``stage`` is the SMTP command answered ("auth" admits the login-rate
    limit). Only the enhanced status code at the start of a reply line is
    inspected; nothing from the reply is kept.
    """
    if type(reply) is not tuple or len(reply) != 2 or type(reply[0]) is not int:
        return None
    code, text = reply
    if isinstance(text, str):
        text = text.encode("utf-8", "replace")
    if not isinstance(text, bytes):
        return None
    text = text[:1024]
    if 500 <= code <= 599 and _DAILY_LIMIT.search(text):
        return "daily"
    if (code == 421 or (code == 454 and stage == "auth")) and _RATE_LIMIT.search(text):
        return "rate"
    return None


def _limit_error(error, stage=""):
    """The sending limit carried by an SMTP exception, if any."""
    if isinstance(error, smtplib.SMTPResponseException):
        return sending_limit((error.smtp_code, error.smtp_error), stage=stage)
    return None


_RESULT_HEALTH = {
    FamilyDeliveryStatus.ACCEPTED: (ProviderHealth.HEALTHY,),
    FamilyDeliveryStatus.TRANSIENT: (
        ProviderHealth.HEALTHY,
        ProviderHealth.UNOBSERVED,
        ProviderHealth.UNAVAILABLE,
    ),
    FamilyDeliveryStatus.PERMANENT: (ProviderHealth.HEALTHY, ProviderHealth.UNOBSERVED),
    FamilyDeliveryStatus.UNKNOWN: (ProviderHealth.UNAVAILABLE, ProviderHealth.SYSTEMIC),
    FamilyDeliveryStatus.UNAVAILABLE: (ProviderHealth.UNAVAILABLE,),
    FamilyDeliveryStatus.SYSTEMIC: (ProviderHealth.SYSTEMIC,),
}


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
    health: ProviderHealth | None = None
    # A Gmail sending limit ("daily" or "rate") that refused this attempt. It
    # crosses the helper's output pipe but is never stored: durably the attempt
    # is an ordinary definitive non-acceptance by a healthy provider.
    limit: str | None = None

    def __post_init__(self):
        """Reject malformed or contradictory outcomes at the process boundary."""
        if self.limit is not None and (
            self.limit not in SENDING_LIMITS
            or self.status is not FamilyDeliveryStatus.TRANSIENT
            or self.health not in (None, ProviderHealth.HEALTHY)
            or self.permanent
            or self.transient
        ):
            raise ValueError("Invalid Family sending-limit outcome.")
        if (
            not isinstance(self.status, FamilyDeliveryStatus)
            or type(self.recipient_count) is not int
            or not 1 <= self.recipient_count <= 100
        ):
            raise ValueError("Invalid Family delivery outcome.")
        if self.health is None:
            object.__setattr__(self, "health", _RESULT_HEALTH[self.status][0])
        if (
            not isinstance(self.health, ProviderHealth)
            or self.health not in _RESULT_HEALTH[self.status]
        ):
            raise ValueError("Invalid Family provider health.")
        if self.health is ProviderHealth.UNOBSERVED and (
            self.permanent or self.transient
        ):
            raise ValueError("Unobserved provider cannot supply recipient evidence.")
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
            "health": self.health.value,
        }

    def wire_payload(self):
        """The helper's output line: the stored result plus any sending limit."""
        return self.payload() | ({"limit": self.limit} if self.limit else {})

    @classmethod
    def from_payload(cls, value, *, recipient_count, wire=False):
        """Bind a closed helper result to the parent's exact envelope size.

        ``wire`` admits the optional helper-only ``limit`` key; stored
        evidence never carries it.
        """
        keys = {"status", "recipient_count", "permanent", "transient", "health"}
        limit = None
        if wire and type(value) is dict and "limit" in value:
            value = dict(value)
            limit = value.pop("limit")
            if type(limit) is not str:
                raise ValueError("Invalid Family delivery result payload.")
        if (
            type(value) is not dict
            or set(value) != keys
            or type(value["status"]) is not str
            or type(value["health"]) is not str
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
            ProviderHealth(value["health"]),
            limit,
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
    # The deployment's public origin, set by the dispatcher from its own
    # configuration; the only host a campaign banner (#248) may load from.
    banner_origin: str = ""

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
        # The campaign banner (#248) is the one server-built image allowed.
        html = without_email_banner(self.html, self.banner_origin)
        content = prepare_content(html, text=self.text)
        if content.html != html or content.text != self.text:
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
            "banner_origin": self.banner_origin,
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
                "banner_origin",
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
                html=email_document(self.html),
                text=self.text,
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-{self.semantic_key.hex}@parishkit.invalid>"
        )
        return result


def delivery_settings(value):
    """Workspace identity is closed; routed recipients come only from the mail.

    ``sender_name`` is the optional From display name (see sender_name.py);
    every other value is a normalized address.
    """
    addresses = {"delegated_email", "sender", "reply_to"}
    if type(value) is not dict or set(value) - {"sender_name"} != addresses:
        raise ValueError("Invalid Family delivery settings.")
    if any(normalized_email(value[key]) != value[key] for key in addresses):
        raise ValueError("Invalid Family delivery settings.")
    if "sender_name" in value and (
        type(value["sender_name"]) is not str
        or clean_sender_name(value["sender_name"]) != value["sender_name"]
    ):
        raise ValueError("Invalid Family delivery settings.")
    return dict(value)


def _reply(value):
    """Discard server prose; a malformed reply cannot fabricate a status code."""
    if type(value) is not tuple or len(value) != 2 or type(value[0]) is not int:
        raise ValueError("Invalid SMTP reply.")
    return value[0]


def _submit(smtp, mail, sender_name=""):
    """Preserve per-address refusals across DATA failure without resending a Family.

    Explicit MAIL/RCPT staging distinguishes pre-DATA connection loss (definitely
    unsent) from ambiguous DATA loss. Content is serialized before MAIL so MIME
    errors cannot be confused with an external side effect.
    """
    count = len(mail.recipients)
    permanent, transient = [], []

    def result(status, health=None):
        return FamilyDeliveryResult(
            status, count, tuple(permanent), tuple(transient), health
        )

    def limited(kind):
        # The mailbox is over a Gmail limit: nothing was sent and no address
        # was refused, so no recipient evidence (or suppression) is recorded.
        return _limited_result(kind, count)

    uncertain = False
    stage = "content"
    try:
        addresses = (mail.sender, mail.reply_to, *mail.recipients)
        international = any(not address.isascii() for address in addresses)
        if international and not smtp.has_extn("smtputf8"):
            # A Family's unsupported address must not halt other Families or
            # fabricate a provider refusal for an address never sent to RCPT.
            return result(
                FamilyDeliveryStatus.SYSTEMIC
                if not mail.sender.isascii() or not mail.reply_to.isascii()
                else FamilyDeliveryStatus.PERMANENT
            )
        message = apply_sender_name(mail.message(), sender_name, mail.sender).as_bytes(
            policy=(policy.SMTPUTF8 if international else policy.SMTP).clone(
                cte_type="7bit"
            )
        )
        options = ["SMTPUTF8", "BODY=8BITMIME"] if international else []
        stage = "mail"
        reply = smtp.mail(mail.sender, options=options)
        if kind := sending_limit(reply, stage="mail"):
            return limited(kind)
        code = _reply(reply)
        if code != 250:
            return result(_handshake_failure(code))
        stage = "rcpt"
        for index, address in enumerate(mail.recipients):
            reply = smtp.rcpt(address)
            if kind := sending_limit(reply, stage="rcpt"):
                return limited(kind)
            code = _reply(reply)
            if code in (250, 251):
                continue
            if 400 <= code <= 499:
                transient.append(index)
            elif 500 <= code <= 599:
                permanent.append(index)
            else:
                return result(FamilyDeliveryStatus.TRANSIENT)
        if len(permanent) + len(transient) == count:
            return result(
                FamilyDeliveryStatus.TRANSIENT
                if transient
                else FamilyDeliveryStatus.PERMANENT
            )
        uncertain = True
        try:
            reply = smtp.data(message)
            if kind := sending_limit(reply, stage="data"):
                return limited(kind)
            code = _reply(reply)
        except smtplib.SMTPDataError as error:
            code = error.smtp_code
            # A limit refusal of DATA is still definitive non-acceptance.
            if kind := _limit_error(error, "data"):
                return limited(kind)
            # smtplib also raises here if the preliminary DATA reply was not
            # 354. An anomalous 250 exception is not final message acceptance.
            if type(code) is not int or not 400 <= code <= 599:
                return result(FamilyDeliveryStatus.UNKNOWN, ProviderHealth.SYSTEMIC)
        if type(code) is int:
            if code == 250:
                return result(FamilyDeliveryStatus.ACCEPTED)
            if 400 <= code <= 499:
                return result(FamilyDeliveryStatus.TRANSIENT)
            if 500 <= code <= 599:
                return result(FamilyDeliveryStatus.PERMANENT)
        return result(FamilyDeliveryStatus.UNKNOWN, ProviderHealth.SYSTEMIC)
    except Exception as error:
        if uncertain:
            fault = _connection_failure(error)
            return result(FamilyDeliveryStatus.UNKNOWN, ProviderHealth(fault.value))
        if stage in {"mail", "rcpt"}:
            return result(_connection_failure(error))
        return result(FamilyDeliveryStatus.TRANSIENT, ProviderHealth.UNOBSERVED)


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
    return _deliver_validated(
        value,
        settings,
        mail,
        smtp_factory=smtp_factory,
        session_factory=session_factory,
    )


def _deliver_validated(value, settings, mail, *, smtp_factory, session_factory):
    """Share SMTP effects only after a compiled mail adapter validates its contract.

    Public entry points retain their distinct typed mail restrictions. This
    internal transport cannot decide Family/digest admission or authorize retry.
    """
    try:
        with session_factory() as session:
            credentials = _credentials(value, settings, session)
    except (ssl.SSLError, requests.exceptions.SSLError) as error:
        return FamilyDeliveryResult(_tls_failure(error), len(mail.recipients))
    except (
        OSError,
        requests.RequestException,
        TransportError,
        CredentialValidationUnavailable,
    ):
        return FamilyDeliveryResult(
            FamilyDeliveryStatus.UNAVAILABLE, len(mail.recipients)
        )
    except RefreshError as error:
        return FamilyDeliveryResult(
            FamilyDeliveryStatus.UNAVAILABLE
            if error.retryable
            else FamilyDeliveryStatus.SYSTEMIC,
            len(mail.recipients),
        )
    except Exception:
        return FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, len(mail.recipients))
    result = None
    try:
        with smtp_factory(
            "smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()
        ) as smtp:
            reply = smtp.ehlo()
            # "421 4.7.0 Try again later, closing connection. (EHLO)" is Gmail's
            # connection-rate limit, not an outage.
            if kind := sending_limit(reply, stage="ehlo"):
                result = _limited_result(kind, len(mail.recipients))
                return result
            code = _reply(reply)
            if code != 250:
                result = FamilyDeliveryResult(
                    _handshake_failure(code), len(mail.recipients)
                )
                return result
            reply = smtp.docmd(
                "AUTH",
                "XOAUTH2 "
                + xoauth2_string(settings["delegated_email"], credentials.token),
            )
            # "Too many login attempts" (454 4.7.0) is a rate limit, not an outage.
            if kind := sending_limit(reply, stage="auth"):
                result = _limited_result(kind, len(mail.recipients))
                return result
            code = _reply(reply)
            if code != 235:
                result = FamilyDeliveryResult(
                    _handshake_failure(code), len(mail.recipients)
                )
                return result
            result = _submit(smtp, mail, settings.get("sender_name", ""))
    except Exception as error:
        # Once _submit has returned, even a QUIT failure cannot erase its
        # definitive DATA/refusal evidence. Earlier failures are shared faults,
        # unless the server's greeting (SMTPConnectError) or a raised reply
        # was a sending limit.
        if result is None:
            kind = _limit_error(error)
            result = (
                _limited_result(kind, len(mail.recipients))
                if kind
                else FamilyDeliveryResult(
                    _connection_failure(error), len(mail.recipients)
                )
            )
    return result


def _limited_result(kind, count):
    """Nothing was sent and no address was refused: a clean, healthy deferral."""
    return FamilyDeliveryResult(
        FamilyDeliveryStatus.TRANSIENT, count, health=ProviderHealth.HEALTHY, limit=kind
    )


def _handshake_failure(code):
    """Temporary server replies before DATA are safe to retry, not global halts."""
    return (
        FamilyDeliveryStatus.UNAVAILABLE
        if 400 <= code <= 499
        else FamilyDeliveryStatus.SYSTEMIC
    )


def _connection_failure(error):
    """Separate temporary shared outages from deterministic TLS/protocol faults."""
    if isinstance(error, ssl.SSLError):
        return _tls_failure(error)
    if isinstance(error, smtplib.SMTPResponseException):
        return (
            _handshake_failure(error.smtp_code)
            if type(error.smtp_code) is int
            else FamilyDeliveryStatus.SYSTEMIC
        )
    if isinstance(error, (smtplib.SMTPServerDisconnected, OSError)):
        return FamilyDeliveryStatus.UNAVAILABLE
    return FamilyDeliveryStatus.SYSTEMIC


def _tls_failure(error):
    """Inspect bounded typed exception causes, never provider response text.

    Requests wraps urllib3 errors, whose reason/args retain the original SSL
    exception. An EOF/closed/syscall interruption is temporary; certificate or
    unclassified TLS protocol faults remain systemic. Cycles are harmless.
    """
    pending, seen, temporary = [error], set(), False
    for _ in range(32):
        if not pending:
            break
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return FamilyDeliveryStatus.SYSTEMIC
        if isinstance(
            current, (ssl.SSLEOFError, ssl.SSLZeroReturnError, ssl.SSLSyscallError)
        ):
            temporary = True
        pending.extend(current.args[:8])
        pending.extend(
            (current.__cause__, current.__context__, getattr(current, "reason", None))
        )
    return (
        FamilyDeliveryStatus.UNAVAILABLE if temporary else FamilyDeliveryStatus.SYSTEMIC
    )
