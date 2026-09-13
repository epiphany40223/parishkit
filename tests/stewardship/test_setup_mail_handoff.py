"""A setup Workspace key belongs only to its exact ephemeral mail recipient."""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.key_files import MAX_FILE_BYTES
from parishkit.stewardship.accounts.setup_mail_handoff import (
    EphemeralMailRecipient,
    MailCredentialRecipient,
    MailCredentialScope,
    SealedMailCredential,
    WorkspaceCandidate,
    relay_mail_credential,
)
from parishkit.stewardship.jobs.ownership import TaskClaim


def transfer():
    """Synthetic bytes exercise real encryption without any provider credentials."""
    owner = PrivateHandoff("google_workspace", Key("fixture-v1", "active", b"g" * 32))
    scope = MailCredentialScope(uuid4(), uuid4(), 1, TaskClaim(uuid4(), 1, uuid4()))
    recipient = EphemeralMailRecipient(scope)
    candidate = WorkspaceCandidate(b"synthetic-workspace-private")
    original = owner.public().seal(scope.credential_id, candidate.value)
    reply = relay_mail_credential(
        owner,
        recipient=recipient.public,
        ciphertext=original,
        fingerprint=candidate.fingerprint,
    )
    return owner, recipient, candidate, original, reply


def test_real_relay_preserves_only_original_bytes_and_never_prints_them():
    """The public recipient, envelope and recovered value have private-safe reprs."""
    _, recipient, candidate, _, reply = transfer()
    assert recipient.open(reply).value == candidate.value
    for value in (candidate, recipient, recipient.public, reply):
        assert "synthetic-workspace-private" not in repr(value)
    assert "synthetic-workspace-private" not in reply.ciphertext
    with pytest.raises(CryptographicError):
        EphemeralMailRecipient(recipient.public.scope).open(reply)


@pytest.mark.parametrize(
    "field", ["delivery_id", "credential_id", "credential_version", "claim"]
)
def test_changed_scope_cannot_open_prior_envelope(field):
    """Even the same public key cannot authorize a different authenticated scope."""
    owner, recipient, candidate, original, _ = transfer()
    before = recipient.public.scope
    replacement = (
        2
        if field == "credential_version"
        else TaskClaim(before.claim.run_id, 2, before.claim.worker_id)
        if field == "claim"
        else uuid4()
    )
    changed = replace(before, **{field: replacement})
    destination = replace(recipient.public, scope=changed)
    original = owner.public().seal(changed.credential_id, candidate.value)
    reply = relay_mail_credential(
        owner,
        recipient=destination,
        ciphertext=original,
        fingerprint=candidate.fingerprint,
    )
    with pytest.raises(CryptographicError):
        recipient.open(reply)


@pytest.mark.parametrize("fingerprint", ["", "private", "é" * 64, "0" * 64])
def test_invalid_or_wrong_receipts_fail_before_private_value_is_returned(fingerprint):
    """A copied candidate or fabricated byte receipt never establishes readiness."""
    owner, recipient, _, original, reply = transfer()
    with pytest.raises(CryptographicError):
        relay_mail_credential(
            owner,
            recipient=recipient.public,
            ciphertext=original,
            fingerprint=fingerprint,
        )
    with pytest.raises(CryptographicError):
        recipient.open(replace(reply, fingerprint=fingerprint))


@pytest.mark.parametrize("target", ["parishsoft", "slack", "google_oauth"])
def test_unrelated_target_cannot_relay_workspace_input(target):
    """Possession of a different installer's key cannot cross the target boundary."""
    _, recipient, candidate, original, _ = transfer()
    owner = PrivateHandoff(target, Key("fixture-v1", "active", b"g" * 32))
    with pytest.raises(CryptographicError):
        relay_mail_credential(
            owner,
            recipient=recipient.public,
            ciphertext=original,
            fingerprint=candidate.fingerprint,
        )


@pytest.mark.parametrize("value", [None, "private", b"", b"x" * (MAX_FILE_BYTES + 1)])
def test_private_candidate_bounds_are_closed(value):
    """No oversized or implicitly encoded input enters the mail helper."""
    with pytest.raises(CryptographicError):
        WorkspaceCandidate(value)


def test_invalid_scope_recipient_and_envelope_types_fail_closed():
    """Primitive construction never coerces booleans or malformed key material."""
    _, recipient, _, _, _ = transfer()
    with pytest.raises(CryptographicError):
        replace(recipient.public.scope, credential_version=True)
    with pytest.raises(CryptographicError):
        EphemeralMailRecipient(None)
    with pytest.raises(CryptographicError):
        MailCredentialRecipient(recipient.public.scope, b"short")
    with pytest.raises(CryptographicError):
        recipient.open(None)
    with pytest.raises(CryptographicError):
        recipient.open(SealedMailCredential("not-an-envelope", "0" * 64))
