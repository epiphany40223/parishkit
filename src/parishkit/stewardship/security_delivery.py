"""Security alert envelopes for the private mail helper, with no user-authored body."""

import smtplib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from parishkit.email.base import Email, build_message

from .campaigns.domain import SystemMode
from .family_delivery import _deliver_validated, delivery_settings
from .jobs.operational_payload import canonical_id
from .jobs.outbox_validation import mailbox, recipients
from .jobs.security_content import KINDS, SecurityAlert, render_security_alert
from .provider_check_worker import CheckSession


def alert_payload(alert):
    """Only the event's own closed facts cross the private pipe."""
    return {
        "event_id": str(alert.event_id),
        "kind": alert.kind,
        "target": alert.target,
        "actor": alert.actor,
        "mode": alert.mode.value,
        "occurred_at": alert.occurred_at.isoformat(),
        "before_roles": list(alert.before_roles),
        "after_roles": list(alert.after_roles),
    }


def decode_alert(value):
    """Rebuild typed facts; the alert's own validation rejects anything else."""
    if type(value) is not dict or set(value) != {
        "event_id",
        "kind",
        "target",
        "actor",
        "mode",
        "occurred_at",
        "before_roles",
        "after_roles",
    }:
        raise ValueError("Invalid security alert payload.")
    if (
        value["kind"] not in KINDS
        or type(value["occurred_at"]) is not str
        or type(value["before_roles"]) is not list
        or type(value["after_roles"]) is not list
    ):
        raise ValueError("Invalid security alert payload.")
    return SecurityAlert(
        canonical_id(value["event_id"]),
        value["kind"],
        value["target"],
        value["actor"],
        SystemMode(value["mode"]),
        datetime.fromisoformat(value["occurred_at"]),
        tuple(value["before_roles"]),
        tuple(value["after_roles"]),
    )


@dataclass(frozen=True, repr=False)
class SecurityMail:
    """One recorded-recipient envelope with no user-authored body or attachments."""

    semantic_key: UUID
    sender: str
    reply_to: str
    recipients: tuple[str, ...]
    alert: SecurityAlert

    def __post_init__(self):
        """Validate before credential exchange or any possible provider connection."""
        if not isinstance(self.semantic_key, UUID) or not isinstance(
            self.alert, SecurityAlert
        ):
            raise ValueError("Invalid security mail identity or facts.")
        mailbox(self.sender)
        mailbox(self.reply_to)
        object.__setattr__(self, "recipients", recipients(self.recipients))
        if len(self.recipients) != 1:
            raise ValueError("A security envelope requires exactly one recipient.")

    def payload(self):
        """No rendered text or secret-bearing URL enters this schema."""
        return {
            "schema": "security-mail-v1",
            "semantic_key": str(self.semantic_key),
            "sender": self.sender,
            "reply_to": self.reply_to,
            "recipients": list(self.recipients),
            "alert": alert_payload(self.alert),
        }

    @classmethod
    def from_payload(cls, value):
        """Keep the security exemption unavailable to ordinary mail payloads."""
        if (
            type(value) is not dict
            or set(value)
            != {"schema", "semantic_key", "sender", "reply_to", "recipients", "alert"}
            or value["schema"] != "security-mail-v1"
            or type(value["recipients"]) is not list
        ):
            raise ValueError("Invalid security mail payload.")
        return cls(
            canonical_id(value["semantic_key"]),
            value["sender"],
            value["reply_to"],
            tuple(value["recipients"]),
            decode_alert(value["alert"]),
        )

    def message(self):
        """Recompile fixed content after IPC; use shared MIME and stable message ID."""
        content = render_security_alert(self.alert)
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


def deliver_security_mail(
    value,
    settings,
    mail,
    *,
    smtp_factory=smtplib.SMTP_SSL,
    session_factory=CheckSession,
):
    """Reuse proven SMTP outcome handling without widening ordinary-mail types."""
    settings = delivery_settings(settings)
    if not isinstance(mail, SecurityMail) or any(
        getattr(mail, name) != settings[name] for name in ("sender", "reply_to")
    ):
        raise ValueError("Security mail differs from its admitted context.")
    return _deliver_validated(
        value,
        settings,
        mail,
        smtp_factory=smtp_factory,
        session_factory=session_factory,
    )
