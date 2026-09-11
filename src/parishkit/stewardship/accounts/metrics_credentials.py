"""Metrics acknowledgements name a random version, never hash the bearer token.

The private file protocol still verifies exact bytes inside its isolated journal.
Only this independent public receipt is allowed into request/audit/acknowledgement
metadata. Knowledge of the receipt gives no ability to authenticate a scrape.
"""

import json
import re
import secrets
from dataclasses import dataclass, field

from .cryptography import CryptographicError
from .key_files import _unique_object, file_fingerprint


@dataclass(frozen=True)
class MetricsCredential:
    """Independent random public version label and private 256-bit bearer token."""

    receipt: str
    token: bytes = field(repr=False)

    def __post_init__(self):
        if (
            type(self.receipt) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.receipt) is None
            or type(self.token) is not bytes
            or re.fullmatch(rb"[A-Za-z0-9_-]{43}", self.token) is None
        ):
            raise CryptographicError("Metrics credential document is invalid.")

    @classmethod
    def generate(cls):
        """The receipt is not derived from the token or any other private value."""
        return cls(secrets.token_hex(32), secrets.token_urlsafe(32).encode("ascii"))

    def serialize(self):
        """Private serialization is written only to the owner-only credential file."""
        return json.dumps(
            {
                "version": 1,
                "kind": "metrics-bearer",
                "receipt": self.receipt,
                "token": self.token.decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    @classmethod
    def parse(cls, value):
        """Accept only one bounded unambiguous metrics document, without fallback."""
        try:
            if type(value) is not bytes or not 0 < len(value) <= 1024:
                raise ValueError
            document = json.loads(value, object_pairs_hook=_unique_object)
            if (
                type(document) is not dict
                or set(document) != {"version", "kind", "receipt", "token"}
                or type(document["version"]) is not int
                or document["version"] != 1
                or document["kind"] != "metrics-bearer"
                or type(document["token"]) is not str
            ):
                raise ValueError
            return cls(document["receipt"], document["token"].encode("ascii"))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise CryptographicError(
                "Metrics credential document is invalid."
            ) from None


def credential_receipt(value, target):
    """Only metrics uses an opaque version; ordinary credential receipts bind bytes."""
    if target == "metrics":
        return MetricsCredential.parse(value).receipt
    return file_fingerprint(value)
