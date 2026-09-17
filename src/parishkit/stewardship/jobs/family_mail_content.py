"""Credential-redacted Family rendering and purpose-bound sealed references.

These pure operations do not authorize queueing or delivery. The owning service
must pin source, content, lifecycle and credential identities transactionally.
General workers can seal a manual code and an opaque retained-token reference;
they never decrypt or copy a reusable link token's plaintext here.
"""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from parishkit.stewardship.accounts.cryptography import (
    ALPHABET,
    CryptographicError,
    TokenPrivateKeyring,
    TokenPublicKeyring,
)
from parishkit.stewardship.web.content import (
    FAMILY_CODE_MARKER as CODE_PLACEHOLDER,
)
from parishkit.stewardship.web.content import (
    FAMILY_CREDENTIAL_PLACEHOLDERS as PRIVATE_NAMES,
)
from parishkit.stewardship.web.content import (
    FAMILY_LINK_MARKER as LINK_PLACEHOLDER,
)
from parishkit.stewardship.web.content import (
    PLACEHOLDERS,
    bounded_text,
    prepare_content,
    render_template,
    validate_family_email,
)

from .family_mail_routing import route_family_mail
from .outbox_validation import (
    DeliveryIdentity,
    SealedSubstitutions,
    substitution_context,
)


@dataclass(frozen=True, repr=False)
class FamilyMailTemplate:
    """Already-sanitized immutable content; raw private prose stays out of repr."""

    subject: str
    html: str
    text: str

    def __post_init__(self):
        """Retained content must be canonical, bounded and use inert placeholders."""
        prepared = prepare_content(self.html, text=self.text)
        if prepared.html != self.html:
            raise ValueError("Family email requires canonical safe content.")
        validate_family_email(self.subject, self.html, self.text)


def _no_reserved_markers(value):
    """Source/template literals cannot masquerade as generated credential slots."""
    bounded_text(value)
    if CODE_PLACEHOLDER in value or LINK_PLACEHOLDER in value:
        raise ValueError("Family email contains a reserved placeholder.")


def _family_identity(identity):
    """Only invitations/reminders carry credentials; receipts have no access values."""
    if not isinstance(identity, DeliveryIdentity) or identity.purpose not in {
        "initial",
        "reminder",
    }:
        raise TypeError("An exact Family delivery identity is required.")
    expected = "production" if identity.mode == "production" else "rehearsal"
    if identity.credential_namespace != expected:
        raise ValueError("Family mail requires its own credential namespace.")


def _render_part(value, values, *, html=False):
    """Distinguish intentional private slots from markers assembled by public text.

    A per-render temporary pair survives URL sanitization but is replaced before
    persistence. It cannot change the deterministic retained content or digest.
    """
    nonce = uuid4().hex
    code = "PARISHKIT_SLOT_" + nonce
    link = "https://parishkit.invalid/slot/" + nonce
    if any(code in item or link in item for item in (value, *values.values())):
        raise ValueError("Family email contains a reserved placeholder.")
    rendered = render_template(
        value, values | {"family_code": code, "family_url": link}, html=html
    )
    _no_reserved_markers(rendered)
    return rendered.replace(code, CODE_PLACEHOLDER).replace(link, LINK_PLACEHOLDER)


def render_family_mail(
    *,
    identity,
    configuration_id,
    template_id,
    template,
    values,
    sender,
    intended_recipients,
    testing_recipient=None,
    reply_to=None,
):
    """Pin non-secret content and routing; never receive a plaintext link or code.

    Reserved markers occupy credential positions in both retained parts. A
    later admitted dispatcher resolves them only after namespace/generation
    checks. Testing has exactly one routed address and a mandatory safe banner.
    """
    _family_identity(identity)
    if not isinstance(template, FamilyMailTemplate) or type(values) is not dict:
        raise TypeError("Typed Family content and explicit public values are required.")
    if set(values) - (PLACEHOLDERS - PRIVATE_NAMES):
        raise ValueError("Family content accepts only public substitutions.")
    for value in values.values():
        _no_reserved_markers(value)
    subject = _render_part(template.subject, values)
    html = _render_part(template.html, values, html=True)
    text = _render_part(template.text, values)
    return route_family_mail(
        identity=identity,
        configuration_id=configuration_id,
        template_id=template_id,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=intended_recipients,
        testing_recipient=testing_recipient,
        family_name=values.get("family_member_names")
        or values.get("family_name")
        or "Family",
        subject=subject,
        html=html,
        text=text,
    )


def _credential_payload(identity, code, token_id):
    """Use a closed, explicit namespace binding, including the original epoch."""
    _family_identity(identity)
    testing = identity.mode == "testing"
    valid_code = type(code) is str and len(code) == 8
    if valid_code:
        valid_code = (
            code.startswith("I") and all(value in ALPHABET for value in code[1:])
            if testing
            else all(value in ALPHABET for value in code)
        )
    if not valid_code or not isinstance(token_id, UUID):
        raise ValueError("Family credential reference is invalid.")
    return {
        "version": 1,
        "family_id": str(identity.family_id),
        "purpose": identity.purpose,
        "namespace": identity.credential_namespace,
        "rehearsal_epoch_id": str(identity.rehearsal_epoch_id) if testing else None,
        "token_id": str(token_id),
        "code": code,
    }


def seal_family_credentials(
    *,
    identity,
    render,
    public,
    code,
    token_id,
    token_generation_id=None,
    credential_epoch_id=None,
):
    """Seal only the code/reference; the general worker has no token plaintext."""
    if not isinstance(public, TokenPublicKeyring):
        raise TypeError("Family preparation requires only a token public keyring.")
    payload = _credential_payload(identity, code, token_id)
    production = identity.mode == "production"
    if any(
        isinstance(value, UUID) != production or (not production and value is not None)
        for value in (token_generation_id, credential_epoch_id)
    ):
        raise ValueError("Family credential generation does not match its namespace.")
    payload.update(
        token_generation_id=str(token_generation_id) if production else None,
        credential_epoch_id=str(credential_epoch_id) if production else None,
    )
    envelope = public.encrypt(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        context=substitution_context(identity, render),
    )
    return SealedSubstitutions(envelope, token_generation_id, credential_epoch_id)


@dataclass(frozen=True, repr=False)
class FamilyCredentialReference:
    """An in-memory dispatch input, not proof that the retained token is current."""

    code: str
    token_id: UUID


def open_family_credentials(*, identity, render, sealed, private):
    """Only a private-key owner can recover a code; token resolution is separate.

    Dispatch must independently recheck current namespace/generation and load
    the referenced token for the exact Family. This primitive grants no provider
    authority and never decrypts the retained reusable token itself.
    """
    if not isinstance(private, TokenPrivateKeyring) or not isinstance(
        sealed, SealedSubstitutions
    ):
        raise TypeError("Dispatch requires typed private-key and sealed inputs.")
    try:
        payload = json.loads(
            private.decrypt(
                sealed.envelope, context=substitution_context(identity, render)
            )
        )
        production = identity.mode == "production"
        if (
            type(payload) is not dict
            or type(payload.get("version")) is not int
            or any(
                isinstance(value, UUID) != production
                or (not production and value is not None)
                for value in (sealed.token_generation_id, sealed.credential_epoch_id)
            )
        ):
            raise ValueError
        expected = _credential_payload(
            identity, payload["code"], UUID(payload["token_id"])
        )
        expected.update(
            token_generation_id=str(sealed.token_generation_id)
            if sealed.token_generation_id is not None
            else None,
            credential_epoch_id=str(sealed.credential_epoch_id)
            if sealed.credential_epoch_id is not None
            else None,
        )
        if payload != expected:
            raise ValueError
        return FamilyCredentialReference(payload["code"], UUID(payload["token_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CryptographicError("Family mail credentials are unavailable.") from None
