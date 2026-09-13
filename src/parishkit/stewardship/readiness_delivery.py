"""Private single-recipient SMTP submission with explicit acceptance uncertainty.

This internal adapter is intended for the bounded mail-dispatch helper only.
It does not authorize delivery or retry. Its caller must durably record intent,
keep ownership alive, impose a total deadline and reconcile missing outcomes.
"""

import smtplib
import ssl
from enum import StrEnum
from types import SimpleNamespace

from parishkit.email.google_workspace import xoauth2_string

from .accounts.integration_candidates import GOOGLE_TOKEN_URI, workspace_candidate
from .accounts.provider_context import validated_context
from .provider_check_worker import CheckSession
from .readiness_mail import ReadinessMail


class DeliveryOutcome(StrEnum):
    """Provider acceptance is distinct from recipient inbox arrival or uncertainty."""

    ACCEPTED = "accepted"
    NOT_SENT = "not_sent"
    UNKNOWN = "delivery_unknown"


def _credentials(value, settings, session):
    """Only the canonical token endpoint receives the in-memory delegated assertion."""
    credentials = workspace_candidate(
        value, delegated_email=settings["delegated_email"]
    )

    def token_request(url, method="GET", body=None, headers=None, **kwargs):
        """Reuse bounded HTTP without inheriting ambient proxy settings."""
        if url != GOOGLE_TOKEN_URI or method != "POST":
            raise ValueError("Unsupported token exchange.")
        response = session.request(method, url, data=body, headers=headers)
        return SimpleNamespace(
            status=response.status_code, data=response.content, headers=response.headers
        )

    credentials.refresh(token_request)
    return credentials


def deliver_sample(
    value,
    settings,
    mail,
    *,
    smtp_factory=smtplib.SMTP_SSL,
    session_factory=CheckSession,
):
    """Never retry, leak provider exceptions or mistake a failed QUIT for non-delivery.

    smtplib sends only after MAIL/RCPT negotiation. Explicit address/data rejection
    proves non-acceptance; timeout, disconnect and unexpected responses after
    submission starts are unknown. A successful DATA response remains acceptance
    even if connection shutdown subsequently fails.
    """
    settings = validated_context("google_workspace", settings)
    if not isinstance(mail, ReadinessMail) or any(
        getattr(mail, key) != settings[key]
        for key in ("sender", "reply_to", "recipient")
    ):
        raise ValueError("The sample differs from its admitted mail context.")
    outcome = DeliveryOutcome.NOT_SENT
    try:
        message = mail.message()
        with session_factory() as session:
            credentials = _credentials(value, settings, session)
        with smtp_factory(
            "smtp.gmail.com", 465, timeout=10, context=ssl.create_default_context()
        ) as smtp:
            code, _ = smtp.ehlo()
            if code != 250:
                return DeliveryOutcome.NOT_SENT
            code, _ = smtp.docmd(
                "AUTH",
                "XOAUTH2 "
                + xoauth2_string(settings["delegated_email"], credentials.token),
            )
            if code != 235:
                return DeliveryOutcome.NOT_SENT
            outcome = DeliveryOutcome.UNKNOWN
            outcome = _submit(smtp, message, mail)
            # Once send_message supplied its complete response, QUIT cannot
            # change that decision, including an explicit single-address refusal.
    except Exception:
        # Private credential/provider strings never reach the parent or logging.
        # Preserve a known DATA result even when QUIT fails during cleanup.
        pass
    return outcome


def _submit(smtp, message, mail):
    """Classify DATA before context-manager cleanup can obscure its real outcome."""
    try:
        refused = smtp.send_message(
            message, from_addr=mail.sender, to_addrs=[mail.recipient]
        )
        if refused == {}:
            return DeliveryOutcome.ACCEPTED
        if isinstance(refused, dict) and set(refused) == {mail.recipient}:
            return DeliveryOutcome.NOT_SENT
    except (
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
        smtplib.SMTPNotSupportedError,
    ):
        return DeliveryOutcome.NOT_SENT
    except smtplib.SMTPDataError as error:
        return (
            DeliveryOutcome.NOT_SENT
            if type(error.smtp_code) is int and 400 <= error.smtp_code <= 599
            else DeliveryOutcome.UNKNOWN
        )
    except Exception:
        pass
    return DeliveryOutcome.UNKNOWN
