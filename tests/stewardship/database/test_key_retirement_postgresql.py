"""Retirement never trades recoverability or non-reuse for inventory cleanup."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from django.utils import timezone

from parishkit.stewardship.accounts.cryptography import (
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    Key,
    SigningKeyring,
    TokenPrivateKeyring,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.cipher_rotation import reencrypt_batch
from parishkit.stewardship.campaigns.credential_keys import (
    add_rotation_key,
    initialize_key_inventories,
    inventory_digest,
)
from parishkit.stewardship.campaigns.credential_models import (
    CredentialKeyState,
    RehearsalCodeReservation,
)
from parishkit.stewardship.campaigns.key_retirement import RetirementProof, retire_keys
from parishkit.stewardship.campaigns.mac_rotation import (
    backfill_mac_batch,
    collision_only,
)

from .credential_builders import family_campaign
from .test_cipher_rotation_postgresql import admitted

pytestmark = pytest.mark.django_db(transaction=True)


def proof_owner(**changes):
    """Synthetic owner seam only; no production backup catalog is fabricated."""

    @contextmanager
    def dependencies(previous, replacement):
        """Hold evidence through commit and prove audit is durable before exit."""
        proof = RetirementProof(
            inventory_digest(previous), inventory_digest(replacement), True, True
        )
        yield replace(proof, **changes)
        assert AuditEvent.objects.filter(event_type="credential_keys_retired").exists()

    return dependencies


def rotated_general(tmp_path):
    """Install an independent new writer, preserving the old decryption key."""
    _, _, _, keys = family_campaign(tmp_path)
    rotated = GeneralKeyring(
        [Key("g1", "decrypt-only", b"g" * 32), Key("g2", "active", b"h" * 32)]
    )
    replacement = GeneralKeyring([rotated.active])
    add_rotation_key(keys.general, rotated)
    return rotated, replacement


def test_retirement_rechecks_ciphertext_then_fences_stale_holder(tmp_path):
    """Retirement requires migrated values and rejects a superseded key holder."""
    old, new = rotated_general(tmp_path)
    with pytest.raises(CryptographicError, match="ciphertext"):
        retire_keys(old, new, admit=admitted, dependencies=proof_owner())
    assert reencrypt_batch(old, admit=admitted) == 1
    assert retire_keys(old, new, admit=admitted, dependencies=proof_owner()) == {"g1"}
    assert CredentialKeyState.objects.get(kind=new.kind).inventory_digest == (
        inventory_digest(new)
    )
    with pytest.raises(CryptographicError, match="not current"):
        reencrypt_batch(old, admit=admitted)


@pytest.mark.parametrize(
    "changes",
    [
        {"backups_compatible": False},
        {"backups_compatible": 1},
        {"consumers_ready": False},
        {"previous_digest": "a" * 64},
        {"replacement_digest": "b" * 64},
    ],
)
def test_missing_or_stale_owner_evidence_preserves_inventory(tmp_path, changes):
    """Incomplete backup or consumer evidence leaves every previous key available."""
    old, new = rotated_general(tmp_path)
    reencrypt_batch(old, admit=admitted)
    with pytest.raises(CryptographicError, match="dependencies"):
        retire_keys(old, new, admit=admitted, dependencies=proof_owner(**changes))
    assert CredentialKeyState.objects.get(kind=old.kind).inventory_digest == (
        inventory_digest(old)
    )
    assert not AuditEvent.objects.filter(event_type="credential_keys_retired").exists()


def test_mac_reservations_block_retirement_after_login_migration(tmp_path):
    """Anonymous non-reuse history survives removal of old-key login acceptance."""
    _, campaign, _, keys = family_campaign(tmp_path)
    rotated = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    add_rotation_key(keys.mac, rotated)
    backfill_mac_batch(campaign_id=campaign.pk, general=keys.general, mac=rotated)
    new = CodeMacKeyring([rotated.active])
    with pytest.raises(CryptographicError, match="demotion"):
        retire_keys(rotated, new, admit=admitted, dependencies=proof_owner())
    demoted = CodeMacKeyring([Key("m1", "collision-only", b"m" * 32), rotated.active])
    collision_only(rotated, demoted)
    RehearsalCodeReservation.objects.create(
        campaign=campaign, key_id="m1", digest="a" * 64
    )
    with pytest.raises(CryptographicError, match="reservations"):
        retire_keys(demoted, new, admit=admitted, dependencies=proof_owner())


def test_backfilled_mac_without_reservations_can_leave_online_ring(tmp_path):
    """Verified unused MAC keys can retire without deleting immutable history."""
    _, campaign, _, keys = family_campaign(tmp_path)
    rotated = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    add_rotation_key(keys.mac, rotated)
    backfill_mac_batch(campaign_id=campaign.pk, general=keys.general, mac=rotated)
    demoted = CodeMacKeyring([Key("m1", "collision-only", b"m" * 32), rotated.active])
    collision_only(rotated, demoted)
    assert retire_keys(
        demoted,
        CodeMacKeyring([rotated.active]),
        admit=admitted,
        dependencies=proof_owner(),
    ) == {"m1"}


@pytest.mark.parametrize("window", [None, "future", "naive", "expired"])
def test_signing_retirement_requires_last_possible_credential_expiry(tmp_path, window):
    """Verification keys outlive every credential signed before the writer changed."""
    family_campaign(tmp_path)
    old = SigningKeyring(
        [Key("s1", "verify-only", b"s" * 32), Key("s2", "active", b"r" * 32)]
    )
    initialize_key_inventories(old)
    new = SigningKeyring([old.active])
    end = {
        None: None,
        "future": timezone.now() + timedelta(seconds=1),
        "naive": timezone.now().replace(tzinfo=None),
        "expired": timezone.now() - timedelta(seconds=1),
    }[window]
    if window == "expired":
        assert retire_keys(
            old,
            new,
            admit=admitted,
            dependencies=proof_owner(verification_ends_at=end),
        ) == {"s1"}
    else:
        with pytest.raises(CryptographicError, match="window"):
            retire_keys(
                old,
                new,
                admit=admitted,
                dependencies=proof_owner(verification_ends_at=end),
            )


def test_token_retirement_requires_private_owner_and_preserves_active_pair(tmp_path):
    """A general service's public ring cannot authorize private-token migration."""
    _, _, _, keys = family_campaign(tmp_path)
    old = TokenPrivateKeyring(
        [Key("t1", "decrypt-only", b"t" * 32), Key("t2", "active", b"u" * 32)]
    )
    new = TokenPrivateKeyring([old.active])
    add_rotation_key(keys.public, old.public())
    with pytest.raises(TypeError):
        retire_keys(
            old.public(), new.public(), admit=admitted, dependencies=proof_owner()
        )
    assert retire_keys(old, new, admit=admitted, dependencies=proof_owner()) == {"t1"}


def test_signing_retirement_ignores_ahead_application_clock(tmp_path, monkeypatch):
    """Host clock skew cannot retire a key before the database verification end."""
    from parishkit.stewardship.accounts.sessions import database_now

    family_campaign(tmp_path)
    old = SigningKeyring(
        [Key("s1", "verify-only", b"s" * 32), Key("s2", "active", b"r" * 32)]
    )
    initialize_key_inventories(old)
    end = database_now() + timedelta(minutes=10)
    monkeypatch.setattr(timezone, "now", lambda: end + timedelta(hours=1))
    with pytest.raises(CryptographicError, match="window"):
        retire_keys(
            old,
            SigningKeyring([old.active]),
            admit=admitted,
            dependencies=proof_owner(verification_ends_at=end),
        )


def test_denied_or_combined_retirement_cannot_change_inventory(tmp_path):
    """Admission and exact removal semantics precede any inventory change."""
    old, new = rotated_general(tmp_path)
    with pytest.raises(PermissionError):
        retire_keys(old, new, admit=lambda: False, dependencies=proof_owner())
    with pytest.raises(CryptographicError):
        retire_keys(new, old, admit=admitted, dependencies=proof_owner())
    with pytest.raises(TypeError):
        retire_keys(old, new, admit=admitted, dependencies=None)


def test_exclusive_key_owner_never_waits_indefinitely_behind_reader(tmp_path):
    """A competing rotation receives the same bounded retry as a competing reader."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections, transaction

    from parishkit.stewardship.campaigns.credential_keys import key_set_lock

    _, _, _, keys = family_campaign(tmp_path)
    replacement = GeneralKeyring(
        [Key("g1", "decrypt-only", b"g" * 32), Key("g2", "active", b"h" * 32)]
    )

    def rotate():
        try:
            with pytest.raises(CryptographicError, match="busy"):
                add_rotation_key(keys.general, replacement)
        finally:
            connections.close_all()

    # The executor outlives the held transaction so even a regression timeout
    # releases its blocking lock before joining the worker thread.
    with (
        ThreadPoolExecutor(max_workers=1) as workers,
        transaction.atomic(),
        key_set_lock(keys.general),
    ):
        workers.submit(rotate).result(timeout=5)
