"""A setup worker can receive one candidate without obtaining its persistent key."""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.source.setup_handoff import (
    EphemeralSetupRecipient,
    SealedSetupCredential,
    SetupCredentialRecipient,
    SetupCredentialScope,
    relay_setup_credential,
)

VALUE = b"synthetic-private-setup-key\n"


@pytest.fixture
def exchange():
    """Fresh identities and independent synthetic target/worker key material."""
    scope = SetupCredentialScope(uuid4(), uuid4(), TaskClaim(uuid4(), 1, uuid4()), 4)
    worker = EphemeralSetupRecipient(scope)
    target = PrivateHandoff("parishsoft", Key("synthetic-target", "active", b"t" * 32))
    candidate = target.public().seal(scope.request_id, VALUE)
    return scope, worker, target, candidate


def relay(exchange, **changes):
    """The target owner opens only the original request's candidate envelope."""
    _, worker, target, candidate = exchange
    return relay_setup_credential(
        target,
        **(
            dict(
                recipient=worker.public,
                ciphertext=candidate,
                fingerprint=file_fingerprint(VALUE),
            )
            | changes
        ),
    )


def test_ephemeral_relay_roundtrip_is_not_a_persistent_private_key_transfer(exchange):
    """Only the original process key opens the randomized source-only envelope."""
    scope, worker, _, candidate = exchange
    sealed = relay(exchange)
    assert worker.open(sealed).value == VALUE
    assert sealed.ciphertext != candidate
    assert relay(exchange).ciphertext != sealed.ciphertext
    assert "synthetic-private" not in repr(sealed) + repr(worker) + repr(worker.public)
    assert file_fingerprint(VALUE) not in repr(sealed)
    with pytest.raises(CryptographicError):
        EphemeralSetupRecipient(scope).open(sealed)


@pytest.mark.parametrize(
    "field", ["attempt", "request", "task", "worker", "task_fence", "source_fence"]
)
def test_exchange_context_cannot_be_swapped_between_any_owning_identity(
    exchange, field
):
    """Even the same public key cannot decrypt a packet sealed for another scope."""
    scope, worker, target, candidate = exchange
    changes = {
        "attempt": {"attempt_id": uuid4()},
        "request": {"request_id": uuid4()},
        "task": {"claim": replace(scope.claim, run_id=uuid4())},
        "worker": {"claim": replace(scope.claim, worker_id=uuid4())},
        "task_fence": {"claim": replace(scope.claim, fence=2)},
        "source_fence": {"source_fence": 5},
    }
    changed = replace(scope, **changes[field])
    if field == "request":
        # Correct input isolates the relay context from persistent handoff's
        # separate request-swap rejection below.
        candidate = target.public().seal(changed.request_id, VALUE)
    sealed = relay(
        exchange,
        recipient=SetupCredentialRecipient(changed, worker.public.public_key),
        ciphertext=candidate,
    )
    with pytest.raises(CryptographicError):
        worker.open(sealed)


@pytest.mark.parametrize(
    "fingerprint", [None, [], "private-é", "0" * 64, "A" * 64, "short"]
)
def test_bad_fingerprint_never_leaks_values_or_produces_a_transfer(
    exchange, fingerprint
):
    """Fingerprint parsing and equality use fixed errors, not caller input."""
    with pytest.raises(CryptographicError) as error:
        relay(exchange, fingerprint=fingerprint)
    assert "private" not in str(error.value)


def test_ciphertext_swap_wrong_target_and_small_order_public_key_are_closed(exchange):
    """The relay cannot become a decryptor for another request, target or key."""
    scope, worker, target, _ = exchange
    with pytest.raises(CryptographicError):
        relay(exchange, ciphertext=target.public().seal(uuid4(), VALUE))
    with pytest.raises(CryptographicError):
        relay_setup_credential(
            PrivateHandoff("slack", target.key),
            recipient=worker.public,
            ciphertext="invalid",
            fingerprint=file_fingerprint(VALUE),
        )
    with pytest.raises(CryptographicError):
        relay(exchange, recipient=SetupCredentialRecipient(scope, b"\0" * 32))


@pytest.mark.parametrize("fence", [True, 0, -1, 2**63, "4"])
def test_source_fence_is_an_exact_positive_database_integer(exchange, fence):
    """Boolean and unbounded values cannot alias real source ownership."""
    with pytest.raises(CryptographicError):
        replace(exchange[0], source_fence=fence)


def test_worker_rejects_modified_receipt_and_wrong_envelope(exchange):
    """Only a matching envelope and independently bound receipt become a key."""
    _, worker, _, _ = exchange
    sealed = relay(exchange)
    with pytest.raises(CryptographicError):
        worker.open(replace(sealed, fingerprint="0" * 64))
    with pytest.raises(CryptographicError):
        worker.open(SealedSetupCredential("invalid", sealed.fingerprint))
