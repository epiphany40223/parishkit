"""Actual database re-encryption preserves stable credentials and batch atomicity."""

import pytest
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    GeneralKeyring,
    Key,
    TokenPrivateKeyring,
    envelope_header,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.cipher_rotation import (
    reencrypt_batch,
    verify_ciphertexts,
)
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.link_tokens import (
    prepare_generation_batch,
    token_context,
)

from .credential_builders import family_campaign
from .test_link_tokens_postgresql import admit, begin

pytestmark = pytest.mark.django_db(transaction=True)


def admitted():
    """Storage-only test seam; no process, backup, or provider authority is implied."""
    return True


def test_general_cipher_batches_are_bounded_and_preserve_display_codes(tmp_path):
    _, _, _, keys = family_campaign(tmp_path, count=3)
    codes = {
        row.pk: keys.general.decrypt(row.code_ciphertext, context=code_context(row.pk))
        for row in FamilyCampaign.objects.all()
    }
    replacement = GeneralKeyring(
        [Key("g1", "decrypt-only", b"g" * 32), Key("g2", "active", b"h" * 32)]
    )
    add_rotation_key(keys.general, replacement)
    assert verify_ciphertexts(replacement, admit=admitted) == {"g1": 3, "g2": 0}
    assert reencrypt_batch(replacement, admit=admitted, batch_size=2) == 2
    assert verify_ciphertexts(replacement, admit=admitted) == {"g1": 1, "g2": 2}
    assert reencrypt_batch(replacement, admit=admitted, batch_size=2) == 1
    assert reencrypt_batch(replacement, admit=admitted, batch_size=2) == 0
    assert verify_ciphertexts(replacement, admit=admitted) == {"g1": 0, "g2": 3}
    for row in FamilyCampaign.objects.all():
        assert (
            replacement.decrypt(row.code_ciphertext, context=code_context(row.pk))
            == codes[row.pk]
        )
    assert (
        AuditEvent.objects.filter(event_type="credential_cipher_batch_migrated").count()
        == 2
    )


def test_token_cipher_migration_preserves_digest_and_original_plaintext(tmp_path):
    _, campaign, actor, keys = family_campaign(tmp_path, count=2)
    generation = begin(campaign, actor, keys)
    prepare_generation_batch(
        generation_id=generation.pk, public=keys.public, admit=admit
    )
    original = {
        row.pk: (
            row.digest,
            keys.private.decrypt(row.ciphertext, context=token_context(row.pk)),
        )
        for row in FamilyAccessToken.objects.all()
    }
    replacement = TokenPrivateKeyring(
        [Key("t1", "decrypt-only", b"t" * 32), Key("t2", "active", b"u" * 32)]
    )
    add_rotation_key(keys.public, replacement.public())
    with pytest.raises(TypeError):
        reencrypt_batch(replacement.public(), admit=admitted)
    assert reencrypt_batch(replacement, admit=admitted) == 2
    assert verify_ciphertexts(replacement, admit=admitted) == {"t1": 0, "t2": 2}
    for row in FamilyAccessToken.objects.all():
        assert (
            row.digest,
            replacement.decrypt(row.ciphertext, context=token_context(row.pk)),
        ) == original[row.pk]
        assert row.generation_id == generation.pk


def test_corrupt_cipher_rolls_back_the_entire_rotation_batch(tmp_path):
    _, _, _, keys = family_campaign(tmp_path, count=2)
    rows = list(FamilyCampaign.objects.order_by("pk"))
    replacement = GeneralKeyring(
        [Key("g1", "decrypt-only", b"g" * 32), Key("g2", "active", b"h" * 32)]
    )
    add_rotation_key(keys.general, replacement)
    FamilyCampaign.objects.filter(pk=rows[-1].pk).update(
        code_ciphertext='{"alg":"aes256gcm-v1","body":"AAAA","kid":"g1","v":1}',
        version=F("version") + 1,
    )
    with pytest.raises(CryptographicError):
        reencrypt_batch(replacement, admit=admitted)
    rows[0].refresh_from_db()
    assert envelope_header(rows[0].code_ciphertext, replacement.algorithm)[0] == "g1"
    assert not AuditEvent.objects.filter(
        event_type="credential_cipher_batch_migrated"
    ).exists()
    with pytest.raises(CryptographicError):
        verify_ciphertexts(replacement, admit=admitted)


@pytest.mark.parametrize("size", [0, 1001, True])
def test_migration_rejects_invalid_bounds_before_storage(tmp_path, size):
    _, _, _, keys = family_campaign(tmp_path)
    with pytest.raises(ValueError):
        reencrypt_batch(keys.general, admit=admitted, batch_size=size)


def test_stale_or_denied_worker_cannot_migrate(tmp_path):
    _, _, _, keys = family_campaign(tmp_path)
    with pytest.raises(PermissionError):
        reencrypt_batch(keys.general, admit=lambda: False)
    replacement = GeneralKeyring(
        [Key("g1", "decrypt-only", b"g" * 32), Key("g2", "active", b"h" * 32)]
    )
    add_rotation_key(keys.general, replacement)
    with pytest.raises(CryptographicError, match="not current"):
        reencrypt_batch(keys.general, admit=admitted)


def test_independent_installation_cannot_reuse_another_purpose_key(tmp_path):
    """An isolated installer checks inventory, not unrelated private mounts."""
    from parishkit.stewardship.accounts.cryptography import SigningKeyring
    from parishkit.stewardship.campaigns.credential_keys import (
        initialize_key_inventories,
    )

    _, _, _, keys = family_campaign(tmp_path)
    replacement = GeneralKeyring(
        [
            Key("g1", "decrypt-only", b"g" * 32),
            Key("g2", "active", keys.mac.active.material),
        ]
    )
    with pytest.raises(CryptographicError, match="independent"):
        add_rotation_key(keys.general, replacement)
    with pytest.raises(CryptographicError, match="independent"):
        initialize_key_inventories(
            SigningKeyring([Key("signing", "active", keys.public.active.material)])
        )
    assert verify_ciphertexts(keys.general, admit=admitted) == {"g1": 1}
