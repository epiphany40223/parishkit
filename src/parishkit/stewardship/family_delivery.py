"""Private multi-recipient SMTP submission with explicit, redacted outcomes.

This adapter grants no delivery authority. The outbox owner must commit intent
before invocation and enforce a finite process deadline. In particular, SMTP's
Message-ID is correlation, not a contractual idempotent-send facility.
"""

import re
import smtplib
import ssl
import time
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
# connection for a sending-rate limit, and "454 4.7.x" in reply to AUTH is its
# login-rate limit. A "421 4.7.x" answering DATA is still a rate limit (421
# closes the session; a content or size refusal would be 5.7.x or 552), but it
# is reported as "message": that message waits like any limit refusal without
# pausing all other mail, so one message Gmail keeps refusing cannot hold up
# the queue at every retry. Other 4.7.x replies (for example to one RCPT) stay
# ordinary per-message or per-address temporary refusals.
#
# Any other 421 (the session is closing) and any 4.3.x or 4.4.x (a mail
# system or network condition, such as "451 4.3.0 Temporary System Problem")
# in reply to RCPT or DATA is the provider's temporary trouble, not the
# message's: it is a "message" limit too, so a partial Google incident cannot
# spend the attempt budget of every queued invitation. (At MAIL such replies
# are already a shared outage; see _handshake_failure.)
_DAILY_LIMIT = re.compile(rb"(?m)^[ \t]*5\.4\.5(?=\s|$)")
_RATE_LIMIT = re.compile(rb"(?m)^[ \t]*4\.7\.[0-9]{1,3}(?=\s|$)")
_PROVIDER_TROUBLE = re.compile(rb"(?m)^[ \t]*4\.[34]\.[0-9]{1,3}(?=\s|$)")
SENDING_LIMITS = frozenset({"daily", "rate", "message"})
# A batched helper (#284) replaces its SMTP connection after this long or this
# many accepted messages, and refreshes its OAuth token (valid for an hour)
# when a connection is opened within TOKEN_MARGIN of the token's expiry.
CONNECTION_SECONDS = 300
CONNECTION_MESSAGES = 50
TOKEN_MARGIN = timedelta(minutes=5)


def sending_limit(reply, *, stage=""):
    """Return "daily", "rate", "message" or None for one ``(code, message)`` reply.

    ``stage`` is the SMTP command answered ("auth" admits the login-rate
    limit; "data" makes a rate limit "message"; "rcpt" and "data" admit
    provider trouble as "message"). Only the enhanced
    status code at the start of a reply line is inspected; nothing from the
    reply is kept.
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
    rate = code == 421 or (code == 454 and stage == "auth")
    if rate and _RATE_LIMIT.search(text):
        return "message" if stage == "data" else "rate"
    if stage in {"rcpt", "data"} and (
        code == 421 or (400 <= code <= 499 and _PROVIDER_TROUBLE.search(text))
    ):
        return "message"
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
    A one-message session never reuses a connection, so it never retries.
    """
    session = SmtpSession(
        value, settings, smtp_factory=smtp_factory, session_factory=session_factory
    )
    try:
        return session.deliver(mail)
    finally:
        session.close()


class SmtpSession:
    """One authenticated Gmail connection reused across messages (#284).

    The batched Family helper keeps one of these for its whole life, so the
    OAuth token and the TLS/SMTP connection are paid for once, not per
    message. Each ``deliver`` is still exactly one submission with the same
    outcome rules as a one-shot helper:

    - The token is fetched when a connection is opened and refreshed there
      when it is missing or within TOKEN_MARGIN of expiry. A connection is
      replaced after CONNECTION_SECONDS (well inside a token's hour), so an
      open connection never outlives the token that authenticated it.
    - A kept connection may have been closed by Gmail while idle. Because an
      UNAVAILABLE result is only ever reported before DATA (definitely
      unsent), exactly one fresh connection is then tried for the same
      message. An uncertain DATA outcome is never retried.
    - Any result other than acceptance drops the connection, so the next
      message starts on a clean connection and SMTP transaction (a refused
      RCPT, a 421 or a broken DATA leave the old one in an unknown state).
    """

    def __init__(
        self,
        value,
        settings,
        *,
        smtp_factory=smtplib.SMTP_SSL,
        session_factory=CheckSession,
        clock=time.monotonic,
    ):
        """Hold the key in memory only; nothing is contacted until ``deliver``."""
        self.value = value
        self.settings = delivery_settings(settings)
        self.smtp_factory = smtp_factory
        self.session_factory = session_factory
        self.clock = clock
        self.credentials = None
        self.connection = None
        self.smtp = None
        self.opened = 0.0
        self.sent = 0

    def deliver(self, mail):
        """Submit one validated mail, reusing the open connection when it is fresh."""
        if self.smtp is not None and (
            self.sent >= CONNECTION_MESSAGES
            or self.clock() - self.opened >= CONNECTION_SECONDS
        ):
            self.drop()
        reused = self.smtp is not None
        result = self._attempt(mail)
        if reused and result.status is FamilyDeliveryStatus.UNAVAILABLE:
            # Nothing was sent on the stale connection; try a fresh one once.
            self.drop()
            result = self._attempt(mail)
        if result.status is FamilyDeliveryStatus.ACCEPTED:
            self.sent += 1
        else:
            self.drop()
        return result

    def _attempt(self, mail):
        """Open a connection when none is kept, then run one SMTP transaction."""
        if self.smtp is None:
            failure = self._connect(len(mail.recipients))
            if failure is not None:
                return failure
        return _submit(self.smtp, mail, self.settings.get("sender_name", ""))

    def _token(self, count):
        """Keep a live token, or return the definitely-unsent result of failing.

        Token failures map exactly as a one-shot helper's always have: an
        interrupted or temporary exchange is UNAVAILABLE, a refused or
        malformed credential is SYSTEMIC. No SMTP command has been sent yet.
        """
        if self.credentials is not None and not _expiring(self.credentials):
            return None
        self.credentials = None
        try:
            with self.session_factory() as session:
                self.credentials = _credentials(self.value, self.settings, session)
            return None
        except (ssl.SSLError, requests.exceptions.SSLError) as error:
            return FamilyDeliveryResult(_tls_failure(error), count)
        except (
            OSError,
            requests.RequestException,
            TransportError,
            CredentialValidationUnavailable,
        ):
            return FamilyDeliveryResult(FamilyDeliveryStatus.UNAVAILABLE, count)
        except RefreshError as error:
            return FamilyDeliveryResult(
                FamilyDeliveryStatus.UNAVAILABLE
                if error.retryable
                else FamilyDeliveryStatus.SYSTEMIC,
                count,
            )
        except Exception:
            return FamilyDeliveryResult(FamilyDeliveryStatus.SYSTEMIC, count)

    def _connect(self, count):
        """Open and authenticate one connection, or return why it failed.

        A limit reply ("421 4.7.0 ... (EHLO)", "454 4.7.0" to AUTH, or a
        rate-limited greeting raised by the factory) is a sending limit, not
        an outage. The failed connection is closed; a failing QUIT is ignored.
        """
        failure = self._token(count)
        if failure is not None:
            return failure
        stack = ExitStack()
        try:
            smtp = stack.enter_context(
                self.smtp_factory(
                    "smtp.gmail.com",
                    465,
                    timeout=10,
                    context=ssl.create_default_context(),
                )
            )
            failure = _handshake("ehlo", smtp.ehlo(), 250, count)
            if failure is None:
                reply = smtp.docmd(
                    "AUTH",
                    "XOAUTH2 "
                    + xoauth2_string(
                        self.settings["delegated_email"], self.credentials.token
                    ),
                )
                failure = _handshake("auth", reply, 235, count)
            if failure is None:
                self.connection, self.smtp = stack, smtp
                self.opened, self.sent = self.clock(), 0
                return None
        except Exception as error:
            kind = _limit_error(error)
            failure = (
                _limited_result(kind, count)
                if kind
                else FamilyDeliveryResult(_connection_failure(error), count)
            )
        _close(stack)
        return failure

    def drop(self):
        """Close the kept connection (QUIT when possible); keep the token."""
        connection, self.connection, self.smtp = self.connection, None, None
        if connection is not None:
            _close(connection)

    def close(self):
        """End the session: close the connection and forget the key and token."""
        self.drop()
        self.credentials = None
        self.value = None


def _handshake(stage, reply, expected, count):
    """None when an EHLO/AUTH reply is the expected code, else its failure."""
    if kind := sending_limit(reply, stage=stage):
        return _limited_result(kind, count)
    code = _reply(reply)
    if code != expected:
        return FamilyDeliveryResult(_handshake_failure(code), count)
    return None


def _close(stack):
    """Close one connection; once a result is known a QUIT failure cannot change it."""
    with suppress(Exception):
        stack.close()


def _expiring(credentials):
    """Whether a token is missing an expiry or within TOKEN_MARGIN of it."""
    expiry = getattr(credentials, "expiry", None)
    if not isinstance(expiry, datetime):
        return True
    now = datetime.now(UTC)
    if expiry.tzinfo is None:
        # google-auth keeps naive UTC expiries.
        now = now.replace(tzinfo=None)
    return expiry - now <= TOKEN_MARGIN


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
