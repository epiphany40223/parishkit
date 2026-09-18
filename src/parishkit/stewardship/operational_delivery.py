"""Operational-only transports compile safe facts again inside the private helper.

These types establish content shape, not authority. The durable delivery owner
must separately admit the current recipient/channel and commit submission first.
"""

import smtplib
from dataclasses import dataclass
from uuid import UUID

from parishkit.config import ConfigError
from parishkit.email.base import Email, build_message

from .accounts.integration_candidates import slack_candidate
from .accounts.provider_context import validated_context
from .family_delivery import _deliver_validated, delivery_settings
from .jobs.operational_content import OperationalAlert, render_alert
from .jobs.operational_payload import alert_payload, canonical_id, decode_alert
from .jobs.outbox_validation import mailbox, recipients
from .provider_check_worker import CheckSession
from .readiness_delivery import DeliveryOutcome
from .readiness_notification import _post_notification


@dataclass(frozen=True, repr=False)
class OperationalMail:
    """One current Admin envelope with no user-authored body or attachment slots."""

    semantic_key: UUID
    sender: str
    reply_to: str
    recipients: tuple[str, ...]
    alert: OperationalAlert

    def __post_init__(self):
        """Validate before credential exchange or any possible provider connection."""
        if not isinstance(self.semantic_key, UUID) or not isinstance(
            self.alert, OperationalAlert
        ):
            raise ValueError("Invalid operational mail identity or facts.")
        mailbox(self.sender)
        mailbox(self.reply_to)
        object.__setattr__(self, "recipients", recipients(self.recipients))
        if len(self.recipients) != 1:
            raise ValueError("An operational envelope requires exactly one Admin.")

    def payload(self):
        """No rendered campaign text or secret-bearing URL enters this schema."""
        return {
            "schema": "operational-mail-v1",
            "semantic_key": str(self.semantic_key),
            "sender": self.sender,
            "reply_to": self.reply_to,
            "recipients": list(self.recipients),
            "alert": alert_payload(self.alert),
        }

    @classmethod
    def from_payload(cls, value):
        """Keep the operational exemption unavailable to ordinary mail payloads."""
        if (
            type(value) is not dict
            or set(value)
            != {"schema", "semantic_key", "sender", "reply_to", "recipients", "alert"}
            or value["schema"] != "operational-mail-v1"
            or type(value["recipients"]) is not list
        ):
            raise ValueError("Invalid operational mail payload.")
        return cls(
            canonical_id(value["semantic_key"]),
            value["sender"],
            value["reply_to"],
            tuple(value["recipients"]),
            decode_alert(value["alert"]),
        )

    def message(self):
        """Recompile fixed content after IPC; use shared MIME and stable message ID."""
        content = render_alert(self.alert)
        result = build_message(
            Email(
                subject=content.subject,
                sender=self.sender,
                to=self.recipients,
                html=content.html,
                text=content.text,
            )
        )
        result["Reply-To"] = self.reply_to
        result["Message-ID"] = (
            f"<stewardship-{self.semantic_key.hex}@parishkit.invalid>"
        )
        return result


@dataclass(frozen=True, repr=False)
class OperationalSlack:
    """Fixed non-personal text and a validated channel, with no mention/template API."""

    delivery_id: UUID
    channel_id: str
    alert: OperationalAlert

    def __post_init__(self):
        """Validate fixed schema before the private helper receives any credentials."""
        if not isinstance(self.delivery_id, UUID) or not isinstance(
            self.alert, OperationalAlert
        ):
            raise ValueError("Invalid operational Slack identity or facts.")
        validated_context("slack", {"channel_id": self.channel_id})

    def payload(self):
        """Only the incident fact vocabulary crosses the operational Slack pipe."""
        return {
            "schema": "operational-slack-v1",
            "delivery_id": str(self.delivery_id),
            "channel_id": self.channel_id,
            "alert": alert_payload(self.alert),
        }

    @classmethod
    def from_payload(cls, value):
        """Reject readiness samples, arbitrary message text and unknown fields."""
        if (
            type(value) is not dict
            or set(value) != {"schema", "delivery_id", "channel_id", "alert"}
            or value["schema"] != "operational-slack-v1"
        ):
            raise ValueError("Invalid operational Slack payload.")
        return cls(
            canonical_id(value["delivery_id"]),
            value["channel_id"],
            decode_alert(value["alert"]),
        )

    def message(self):
        """Explicit mode remains visible; disable formatting and link expansion."""
        content = render_alert(self.alert)
        return {
            "channel": self.channel_id,
            "text": content.subject + "\n\n" + content.text,
            "mrkdwn": False,
            "parse": "none",
            "unfurl_links": False,
            "unfurl_media": False,
        }


def deliver_operational_mail(
    value,
    settings,
    mail,
    *,
    smtp_factory=smtplib.SMTP_SSL,
    session_factory=CheckSession,
):
    """Reuse proven SMTP outcome handling without widening ordinary-mail types."""
    settings = delivery_settings(settings)
    if not isinstance(mail, OperationalMail) or any(
        getattr(mail, name) != settings[name] for name in ("sender", "reply_to")
    ):
        raise ValueError("Operational mail differs from its admitted context.")
    return _deliver_validated(
        value,
        settings,
        mail,
        smtp_factory=smtp_factory,
        session_factory=session_factory,
    )


def deliver_operational_slack(value, notification):
    """One fixed-endpoint attempt, with no retries or recursive failure alerts."""
    try:
        if not isinstance(notification, OperationalSlack):
            raise ValueError("Invalid operational notification.")
        token = slack_candidate(value)
    except (ConfigError, ValueError, TypeError):
        return DeliveryOutcome.NOT_SENT
    return _post_notification(token, notification)
