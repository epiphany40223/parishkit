"""Independent key purposes, versioned envelopes and campaign-scoped credentials."""

import json
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.cryptography import (
    ALPHABET,
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    Key,
    SigningKeyring,
    TokenPrivateKeyring,
    canonical_code,
    independent_keyrings,
    new_code,
    new_token,
    token_digest,
)


def test_general_envelopes_are_randomized_context_bound_and_versioned():
    ring = GeneralKeyring([Key("g1", "active", b"a" * 32)])
    one = ring.encrypt(b"ABCDEFGH", context=b"family:1")
    assert one != ring.encrypt(b"ABCDEFGH", context=b"family:1")
    assert "ABCDEFGH" not in one
    assert json.loads(one)["alg"] == "aes256gcm-v1"
    assert ring.decrypt(one, context=b"family:1") == b"ABCDEFGH"
    with pytest.raises(CryptographicError):
        ring.decrypt(one, context=b"family:2")
    header = json.loads(one)
    header["body"] = header["body"][:-1] + (
        "A" if not header["body"].endswith("A") else "B"
    )
    with pytest.raises(CryptographicError):
        ring.decrypt(
            json.dumps(header, separators=(",", ":"), sort_keys=True),
            context=b"family:1",
        )


def test_general_rotation_can_read_old_key_but_writes_only_new():
    original = GeneralKeyring([Key("g1", "active", b"a" * 32)])
    ciphertext = original.encrypt(b"code", context=b"context")
    rotated = GeneralKeyring(
        [Key("g1", "decrypt-only", b"a" * 32), Key("g2", "active", b"b" * 32)]
    )
    assert rotated.decrypt(ciphertext, context=b"context") == b"code"
    migrated = rotated.encrypt(
        rotated.decrypt(ciphertext, context=b"context"), context=b"context"
    )
    assert json.loads(migrated)["kid"] == "g2"
    with pytest.raises(CryptographicError):
        original.decrypt(migrated, context=b"context")


def test_public_token_ring_cannot_decrypt_with_general_key():
    private = TokenPrivateKeyring([Key("t1", "active", b"c" * 32)])
    public = private.public()
    token = new_token().encode()
    sealed = public.encrypt(token, context=b"campaign:1:family:1")
    assert not hasattr(public, "decrypt")
    assert private.keys["t1"].material != public.keys["t1"].material
    assert private.decrypt(sealed, context=b"campaign:1:family:1") == token
    with pytest.raises(CryptographicError):
        private.decrypt(sealed, context=b"campaign:2:family:1")
    with pytest.raises(CryptographicError):
        GeneralKeyring([Key("t1", "active", b"c" * 32)]).decrypt(
            sealed, context=b"campaign:1:family:1"
        )


def test_token_rotation_preserves_plaintext_only_in_private_service():
    old = TokenPrivateKeyring([Key("t1", "active", b"a" * 32)])
    ring = TokenPrivateKeyring(
        [Key("t1", "decrypt-only", b"a" * 32), Key("t2", "active", b"b" * 32)]
    )
    sealed = old.public().encrypt(b"secret", context=b"object")
    migrated = ring.public().encrypt(
        ring.decrypt(sealed, context=b"object"), context=b"object"
    )
    assert json.loads(migrated)["kid"] == "t2"
    assert ring.decrypt(migrated, context=b"object") == b"secret"


def test_mac_lookup_excludes_collision_only_and_reservations_are_separate():
    ring = CodeMacKeyring(
        [
            Key("m1", "collision-only", b"a" * 32),
            Key("m2", "lookup-only", b"b" * 32),
            Key("m3", "active", b"c" * 32),
        ]
    )
    campaign = uuid4()
    assert set(ring.lookups(campaign, "IABCDEFG")) == {"m2", "m3"}
    with pytest.raises(CryptographicError):
        ring.digest("m1", campaign, "IABCDEFG")
    assert ring.digest("m1", campaign, "IABCDEFG", reservation=True)
    assert ring.digest("m2", campaign, "IABCDEFG", reservation=True) != ring.digest(
        "m2", campaign, "IABCDEFG"
    )
    assert ring.digest("m2", uuid4(), "IABCDEFG") != ring.digest(
        "m2", campaign, "IABCDEFG"
    )
    with pytest.raises(CryptographicError, match="Required collision key"):
        ring.digest("missing", campaign, "IABCDEFG", reservation=True)


@pytest.mark.parametrize(
    ("submitted", "canonical"),
    [
        ("abcd-efgh", "ABCDEFGH"),
        (" a b c d e f g h ", "ABCDEFGH"),
        ("ILOAaaaa", "ILOAAAAA"),
        ("abcdefgh1", None),
        ("abc\tdefgh", None),
        ("abcdefgh\n", None),
        ("АBCDEFGH", None),
        ("ßabcdefg", None),
        (None, None),
        ("a" * 129, None),
    ],
)
def test_code_normalization_is_ascii_only(submitted, canonical):
    assert canonical_code(submitted) == canonical


def test_mode_disjoint_generation_and_domain_separated_link_digest():
    campaign = uuid4()
    codes = {new_code() for _ in range(1000)}
    assert len(codes) == 1000
    assert all(len(code) == 8 and set(code) <= set(ALPHABET) for code in codes)
    assert all(new_code(testing=True).startswith("I") for _ in range(50))
    token = new_token()
    assert len(token) == 43
    assert new_token(testing=True).startswith("test.")
    assert token_digest(token, campaign) != token_digest("test." + token, campaign)
    assert token_digest(token, campaign) != token_digest(token, uuid4())


def test_signing_settings_and_safe_inventory():
    ring = SigningKeyring(
        [Key("s1", "verify-only", b"a" * 32), Key("s2", "active", b"b" * 32)]
    )
    settings = ring.django_settings()
    assert len(settings["SECRET_KEY_FALLBACKS"]) == 1
    assert settings["SECRET_KEY"] != settings["SECRET_KEY_FALLBACKS"][0]
    assert set(ring.inventory()[0]) == {"kind", "id", "usage", "fingerprint"}
    assert "bbbb" not in repr(ring.keys)
    with pytest.raises(CryptographicError, match="independent"):
        independent_keyrings(ring, GeneralKeyring([Key("g", "active", b"a" * 32)]))


@pytest.mark.parametrize(
    "keys",
    [
        [],
        [Key("a", "lookup-only", b"a" * 32)],
        [Key("a", "active", b"a" * 32), Key("b", "active", b"b" * 32)],
        [Key("a", "active", b"a" * 32), Key("b", "decrypt-only", b"a" * 32)],
    ],
)
def test_invalid_keyrings_fail_closed(keys):
    with pytest.raises(CryptographicError):
        GeneralKeyring(keys)


@pytest.mark.parametrize("value", ["", "{}", "[]", "null", "{", "a" * 4_194_305])
def test_malformed_envelopes_are_bounded_and_private(value):
    ring = GeneralKeyring([Key("g1", "active", b"a" * 32)])
    with pytest.raises(CryptographicError, match="Invalid cryptographic envelope"):
        ring.decrypt(value, context=b"context")
