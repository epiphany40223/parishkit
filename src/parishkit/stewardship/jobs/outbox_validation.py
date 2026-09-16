"""Bounded, immutable inputs for the internal delivery journal.

The owning renderer supplies already sanitized, credential-redacted content.
This module validates its shape; it cannot establish that arbitrary prose is
free of personal data or that an intended recipient is currently eligible.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    envelope_header,
)


def identifier(value, *, optional=False):
    """Reject arbitrary private text without interpolating it into an error."""
    if not isinstance(value, UUID) and not (optional and value is None):
        raise TypeError("Delivery identifiers must be UUIDs.")


def mailbox(value):
    """Accept one bounded bare address, never an injected header or display name."""
    if (
        type(value) is not str
        or len(value) > 254
        or any(character in value for character in "\r\n\x00")
    ):
        raise ValueError("Invalid delivery address.")
    try:
        validate_email(value)
    except ValidationError:
        raise ValueError("Invalid delivery address.") from None
    if value != value.strip():
        raise ValueError("Invalid delivery address.")
    return value


def recipients(values):
    """Freeze an ordered nonempty recipient list without silently dropping any."""
    if type(values) not in (tuple, list) or not 1 <= len(values) <= 100:
        raise ValueError("Invalid delivery recipients.")
    result = tuple(mailbox(value) for value in values)
    if len({value.casefold() for value in result}) != len(result):
        raise ValueError("Duplicate delivery recipients.")
    return result


@dataclass(frozen=True)
class DeliveryIdentity:
    """Stable scope and routing cannot be reclassified during retries."""

    scope_id: UUID
    semantic_key: UUID
    mode: str
    routing: str
    purpose: str
    campaign_id: UUID | None = None
    family_id: UUID | None = None
    credential_namespace: str = "none"
    rehearsal_epoch_id: UUID | None = None

    def __post_init__(self):
        """Separate operational mail and Testing credentials from Production."""
        for value in (self.scope_id, self.semantic_key):
            identifier(value)
        for value in (self.campaign_id, self.family_id, self.rehearsal_epoch_id):
            identifier(value, optional=True)
        if self.mode not in ("testing", "production") or self.purpose not in (
            "initial",
            "reminder",
            "receipt",
            "daily_digest",
            "weekly_digest",
            "operational",
        ):
            raise ValueError("Invalid delivery identity.")
        family_purpose = self.purpose in ("initial", "reminder", "receipt")
        if (self.family_id is not None) != family_purpose:
            raise ValueError("Invalid delivery Family binding.")
        if self.purpose == "operational":
            valid = self.routing == "operational"
        else:
            valid = (
                self.campaign_id == self.scope_id
                and self.routing
                == {"testing": "testing_override", "production": "production"}[
                    self.mode
                ]
            )
        if not valid:
            raise ValueError("Invalid delivery routing.")
        if self.credential_namespace == "none":
            valid = self.rehearsal_epoch_id is None
        elif self.credential_namespace == "production":
            valid = (
                family_purpose
                and self.mode == "production"
                and self.rehearsal_epoch_id is None
            )
        elif self.credential_namespace == "rehearsal":
            valid = (
                family_purpose
                and self.mode == "testing"
                and self.rehearsal_epoch_id is not None
            )
        else:
            valid = False
        if not valid:
            raise ValueError("Invalid delivery credential namespace.")


@dataclass(frozen=True, repr=False)
class SealedSubstitutions:
    """Opaque presealed values; the journal never loads a private decryption key."""

    envelope: str
    token_generation_id: UUID | None = None
    credential_epoch_id: UUID | None = None

    def __post_init__(self):
        """Validate the envelope header without exposing its body in failures."""
        identifier(self.token_generation_id, optional=True)
        identifier(self.credential_epoch_id, optional=True)
        if type(self.envelope) is not str or len(self.envelope) > 32768:
            raise ValueError("Invalid sealed delivery substitution.")
        try:
            envelope_header(self.envelope, "sealedbox-v1")
        except CryptographicError:
            raise ValueError("Invalid sealed delivery substitution.") from None

    def fields(self):
        """Return current sealed storage only; never copy it into attempt history."""
        key_id, _ = envelope_header(self.envelope, "sealedbox-v1")
        return {
            "sealed_substitutions": self.envelope,
            "sealed_key_id": key_id,
            "token_generation_id": self.token_generation_id,
            "credential_epoch_id": self.credential_epoch_id,
        }


def substitution_context(identity, render):
    """Bind encrypted placeholders to semantic identity and this exact rendering.

    The later dispatch owner must use the same context when decrypting. Neither
    a different message nor a re-rendered message can transplant substitutions.
    No secret value is placed in the context or content fingerprint.
    """
    if not isinstance(identity, DeliveryIdentity) or not isinstance(
        render, RenderInput
    ):
        raise TypeError("Typed delivery identity and render are required.")
    return (
        b"outbox-substitutions-v1:"
        + identity.scope_id.bytes
        + identity.semantic_key.bytes
        + identity.mode.encode("ascii")
        + render.configuration_id.bytes
        + (
            b"\x01" + render.template_id.bytes
            if render.template_id is not None
            else b"\x00"
        )
        + bytes.fromhex(render.fields()["payload_digest"])
    )


@dataclass(frozen=True)
class RenderInput:
    """Non-secret content pinned to its configuration and optional template."""

    configuration_id: UUID
    template_id: UUID | None
    sender: str
    intended_recipients: tuple[str, ...]
    routed_recipients: tuple[str, ...]
    subject: str
    html: str
    text: str
    reply_to: str | None = None

    def __post_init__(self):
        """Validate once and detach caller-owned recipient lists from this value."""
        identifier(self.configuration_id)
        identifier(self.template_id, optional=True)
        mailbox(self.sender)
        object.__setattr__(
            self, "reply_to", self.sender if self.reply_to is None else self.reply_to
        )
        mailbox(self.reply_to)
        for field in ("intended_recipients", "routed_recipients"):
            object.__setattr__(self, field, recipients(getattr(self, field)))
        if (
            type(self.subject) is not str
            or not self.subject.strip()
            or len(self.subject) > 254
            or any(value in self.subject for value in "\r\n\x00")
        ):
            raise ValueError("Invalid delivery subject.")
        for value in (self.html, self.text):
            if (
                type(value) is not str
                or not value.strip()
                or "\x00" in value
                or len(value.encode("utf-8")) > 1_048_576
            ):
                raise ValueError("Invalid delivery body.")

    def fields(self):
        """Produce a detached record payload and reproducible content fingerprint."""
        content = {
            "sender": self.sender,
            "reply_to": self.reply_to,
            "intended_recipients": list(self.intended_recipients),
            "routed_recipients": list(self.routed_recipients),
            "subject": self.subject,
            "html": self.html,
            "text": self.text,
        }
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return dict(
            content,
            payload_digest=digest,
            configuration_id=self.configuration_id,
            template_id=self.template_id,
        )


@dataclass(frozen=True)
class DeliveryEvidence:
    """Private reconciliation note with only fingerprinted provider identifiers."""

    provider_key_digest: str = ""
    provider_message_digest: str = ""
    evidence_digest: str = ""
    evidence_note: str = ""
    reason: str = ""

    def __post_init__(self):
        """Keep raw provider responses out of structured identifier fields."""
        for value in (
            self.provider_key_digest,
            self.provider_message_digest,
            self.evidence_digest,
        ):
            if type(value) is not str or (
                value != "" and re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise ValueError("Invalid delivery evidence fingerprint.")
        if type(self.reason) is not str or (
            self.reason != ""
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.reason) is None
        ):
            raise ValueError("Invalid delivery reason.")
        if (
            type(self.evidence_note) is not str
            or len(self.evidence_note) > 2000
            or "\x00" in self.evidence_note
        ):
            raise ValueError("Invalid delivery reconciliation note.")
