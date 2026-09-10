"""Sealed target handoff cannot be decrypted by the public web-facing object."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.secret_models import SECRET_TARGETS


@pytest.mark.parametrize("target", SECRET_TARGETS)
def test_sealed_handoff_binds_target_request_and_key(target):
    private = PrivateHandoff(target, Key("handoff-1", "active", b"h" * 32))
    request_id = uuid4()
    public = private.public()
    ciphertext = public.seal(request_id, b"synthetic-secret")
    assert "synthetic-secret" not in ciphertext
    assert private.open(request_id, ciphertext) == b"synthetic-secret"
    assert not hasattr(public, "open") and not hasattr(public, "decrypt")
    assert "hhhh" not in repr(private)
    with pytest.raises(CryptographicError):
        private.open(uuid4(), ciphertext)
    another = "slack" if target != "slack" else "google_oauth"
    with pytest.raises(CryptographicError):
        PrivateHandoff(another, private.key).open(request_id, ciphertext)
    with pytest.raises(CryptographicError):
        PrivateHandoff(target, Key("handoff-1", "active", b"x" * 32)).open(
            request_id, ciphertext
        )


@pytest.mark.parametrize("value", [b"", "secret", b"x" * (128 * 1024 + 1)])
def test_handoff_bounds_plaintext_before_sealing(value):
    public = PrivateHandoff("slack", Key("h1", "active", b"h" * 32)).public()
    with pytest.raises(CryptographicError):
        public.seal(uuid4(), value)


@pytest.mark.parametrize(
    "purpose,other", [("candidate", "rollback"), ("rollback", "candidate")]
)
def test_handoff_purpose_is_authenticated(purpose, other):
    """Rollback evidence cannot be substituted for a candidate or vice versa."""
    private = PrivateHandoff("slack", Key("h1", "active", b"h" * 32))
    identifier = uuid4()
    ciphertext = private.public().seal(identifier, b"synthetic-secret", purpose=purpose)
    assert private.open(identifier, ciphertext, purpose=purpose) == b"synthetic-secret"
    with pytest.raises(CryptographicError):
        private.open(identifier, ciphertext, purpose=other)
    with pytest.raises(CryptographicError):
        private.public().seal(identifier, b"synthetic-secret", purpose="unapproved")
    with pytest.raises(CryptographicError):
        private.open(identifier, ciphertext, purpose="unapproved")
