"""Private provider values with the same byte receipt as credential installation."""

from dataclasses import dataclass, field

from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.key_files import file_fingerprint, read_private


@dataclass(frozen=True)
class SourceCredential:
    """Only the receipt is printable; the API key remains private working memory."""

    value: bytes = field(repr=False)

    def __post_init__(self):
        """Reject malformed or multiline credential material without echoing it."""
        if type(self.value) is not bytes or not 1 <= len(self.value) <= 4098:
            raise CryptographicError("ParishSoft credential is invalid.")
        key = self._key()
        if not 1 <= len(key) <= 4096 or any(char < 33 or char > 126 for char in key):
            raise CryptographicError("ParishSoft credential is invalid.")

    @property
    def fingerprint(self):
        """Bind exact installed bytes, including an optional terminal line ending."""
        return file_fingerprint(self.value)

    @property
    def api_key(self):
        """Expose key text only to the closed private HTTP transport."""
        return self._key().decode("ascii")

    def _key(self):
        """Accept at most one conventional final line ending, not a multiline key."""
        if self.value.endswith(b"\r\n"):
            return self.value[:-2]
        return self.value[:-1] if self.value.endswith(b"\n") else self.value

    @classmethod
    def read(cls, path):
        """Read only an exact owner-only mounted credential through the shared guard."""
        return cls(read_private(path, maximum=4098))
