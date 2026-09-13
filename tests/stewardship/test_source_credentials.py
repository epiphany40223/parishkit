"""Loaded API-key receipts bind exact private bytes without printable key values."""

import pytest

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.key_files import file_fingerprint, write_private
from parishkit.stewardship.source.credentials import SourceCredential


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_loaded_credential_uses_exact_installed_receipt_and_private_repr(
    tmp_path, ending
):
    """Private file bytes bind the receipt without leaking the printable key."""
    value = b"SYNTHETIC-PRIVATE-KEY" + ending
    path = tmp_path / "api-key"
    write_private(path, value)
    credential = SourceCredential.read(path)
    assert credential.api_key == "SYNTHETIC-PRIVATE-KEY"
    assert credential.fingerprint == file_fingerprint(value)
    assert "PRIVATE" not in repr(credential)


@pytest.mark.parametrize(
    "value",
    [
        b"",
        "KEY",
        True,
        b"KEY\n\n",
        b"KEY\r",
        b"KEY SECRET",
        b"KEY\x00",
        b"\xff",
        b"A" * 4099,
    ],
)
def test_invalid_credentials_never_echo_private_material(value):
    """Credential diagnostics reveal neither malformed input nor key fragments."""
    with pytest.raises(CryptographicError) as error:
        SourceCredential(value)
    assert "SECRET" not in str(error.value) and "KEY" not in str(error.value)
