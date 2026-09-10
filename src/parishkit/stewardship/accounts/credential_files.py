"""Crash-reconcilable target-owned file replacement with sealed rollback material.

The isolated installer's database/queue admission wraps this file-side protocol.
It supplies authoritative UTC time, validation/test logic and authenticated
consumer acknowledgements; this module does not turn caller strings into roles.
No plaintext candidate or prior credential is staged outside its working file.
"""

import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import get_ident
from uuid import UUID

from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

from .credential_handoff import PrivateHandoff
from .cryptography import CryptographicError
from .key_files import _path, file_fingerprint, read_private, write_private

JOURNAL_LIMIT = 512 * 1024
FIELDS = frozenset(
    {
        "v",
        "target",
        "request",
        "state",
        "expires_at",
        "prior_fingerprint",
        "candidate_fingerprint",
        "prior_ciphertext",
        "candidate_ciphertext",
        "consumers",
    }
)


@dataclass(frozen=True)
class FileReceipt:
    """Only non-secret progress escapes the private filesystem protocol."""

    request_id: UUID
    state: str
    fingerprint: str | None


def _utc(value):
    """The database-owning caller supplies an explicit authoritative UTC instant."""
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise CryptographicError("Credential installation requires UTC time.")
    return value


def _fingerprint(value, *, optional=False):
    """Do not accept a credential value in a nominal metadata fingerprint slot."""
    if value is None and optional:
        return
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CryptographicError("Invalid credential fingerprint.")


class CredentialFiles:
    """One exact target file; all mutations require the target's exclusive lock."""

    def __init__(self, path, private):
        if not isinstance(private, PrivateHandoff):
            raise TypeError("A target-specific private handoff key is required.")
        self.path, self.private = _path(path), private
        if self.path.name in {".replacement.json", ".replacement.lock"}:
            raise CryptographicError("Credential filename is reserved.")
        self.journal_path = self.path.parent / ".replacement.json"
        self.locked = False
        self.owner = None

    @contextmanager
    def lock(self):
        """Filesystem serialization survives database reconnects and process crashes.

        Nonblocking flock prevents an unbounded queue of workers holding other
        resources. Only this target installer mounts the containing directory RW.
        """
        import fcntl

        if self.locked:
            raise CryptographicError("Credential file lock cannot be nested.")
        _path(self.path)
        try:
            parent = self.path.parent.stat()
        except OSError:
            raise CryptographicError("Credential directory is unavailable.") from None
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise CryptographicError("Credential directory must be owner-only.")
        descriptor = None
        acquired = False
        try:
            descriptor = os.open(
                self.path.parent / ".replacement.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise CryptographicError("Credential lock metadata is invalid.")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locked = True
            self.owner = (os.getpid(), get_ident())
            acquired = True
            yield self
        except OSError:
            raise CryptographicError(
                "Credential file operation is unavailable."
            ) from None
        finally:
            if acquired:
                self.locked = False
                self.owner = None
            if descriptor is not None:
                os.close(descriptor)

    def _guard(self):
        """A target path or public request ID is not permission to bypass its lock."""
        if not self.locked or self.owner != (os.getpid(), get_ident()):
            raise CryptographicError("Credential operation requires its target lock.")

    def pending_request(self):
        """Recover the exact journal owner before admitting a later queued request."""
        self._guard()
        if not self.journal_path.exists():
            return None
        try:
            data = json.loads(read_private(self.journal_path, maximum=JOURNAL_LIMIT))
            identifier = UUID(data["request"])
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise CryptographicError(
                "Credential replacement journal is invalid."
            ) from None
        self._read(identifier)
        return identifier

    def _consumers(self):
        """Acknowledgements come only from services permitted to use this target."""
        return {
            role.value
            for role, names in ALLOWED_SECRETS.items()
            if self.private.target in names
        }

    def _selected(self):
        """Read the exact working file; absence is distinct from unsafe metadata."""
        _path(self.path)
        return read_private(self.path) if self.path.exists() else None

    def _save(self, journal):
        """Atomic private journal rename precedes each working-file side effect."""
        self._guard()
        value = json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
        write_private(self.journal_path, value, maximum=JOURNAL_LIMIT)

    def _read(self, request_id):
        """Reject corrupt, swapped or ambiguous journals with sanitized diagnostics."""
        self._guard()
        if not isinstance(request_id, UUID):
            raise TypeError("A credential request UUID is required.")
        value = read_private(self.journal_path, maximum=JOURNAL_LIMIT)
        try:
            journal = json.loads(value)
            if (
                type(journal) is not dict
                or set(journal) != FIELDS
                or type(journal["v"]) is not int
                or journal["v"] != 1
                or journal["target"] != self.private.target
                or journal["request"] != str(request_id)
                or journal["state"]
                not in {"prepared", "installed", "applied", "rolled_back"}
                or type(journal["consumers"]) is not list
                or not journal["consumers"]
                or any(type(item) is not str for item in journal["consumers"])
                or set(journal["consumers"]) - self._consumers()
                or len(set(journal["consumers"])) != len(journal["consumers"])
                or value
                != json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
            ):
                raise ValueError
            _utc(datetime.fromisoformat(journal["expires_at"]))
            _fingerprint(journal["candidate_fingerprint"])
            _fingerprint(journal["prior_fingerprint"], optional=True)
            if (
                journal["prior_ciphertext"] is not None
                and type(journal["prior_ciphertext"]) is not str
            ):
                raise ValueError
            if journal["state"] == "prepared":
                if type(journal["candidate_ciphertext"]) is not str:
                    raise ValueError
            elif journal["candidate_ciphertext"] is not None:
                raise ValueError
            if journal["state"] in {"applied", "rolled_back"}:
                if journal["prior_ciphertext"] is not None:
                    raise ValueError
            elif (journal["prior_fingerprint"] is None) != (
                journal["prior_ciphertext"] is None
            ):
                raise ValueError
            return journal
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise CryptographicError(
                "Credential replacement journal is invalid."
            ) from None

    def _receipt(self, journal):
        """Installed is not applied: the latter requires consumer acknowledgement."""
        fingerprint = journal["candidate_fingerprint"]
        if journal["state"] in {"prepared", "rolled_back"}:
            fingerprint = journal["prior_fingerprint"]
        return FileReceipt(UUID(journal["request"]), journal["state"], fingerprint)

    def _terminal_receipt(self, journal):
        """A terminal retry must still match the file before publishing its receipt."""
        receipt = self._receipt(journal)
        selected = self._selected()
        actual = file_fingerprint(selected) if selected is not None else None
        if actual != receipt.fingerprint:
            raise CryptographicError(
                "Working credential has an unexpected fingerprint."
            )
        return receipt

    def prepare(
        self,
        *,
        request_id,
        candidate,
        expected_fingerprint,
        consumers,
        expires_at,
        now,
        validate,
    ):
        """Validate/test in memory, then durably seal the prior value before install."""
        self._guard()
        if not isinstance(request_id, UUID) or not callable(validate):
            raise TypeError(
                "Typed request identity and credential validation are required."
            )
        _fingerprint(expected_fingerprint, optional=True)
        _utc(now)
        _utc(expires_at)
        if expires_at <= now or expires_at - now > timedelta(hours=24):
            raise CryptographicError("Credential staging interval is invalid.")
        if (
            type(consumers) is not tuple
            or not consumers
            or any(type(item) is not str for item in consumers)
            or set(consumers) - self._consumers()
            or len(set(consumers)) != len(consumers)
        ):
            raise CryptographicError("A bounded consumer inventory is required.")
        if self.journal_path.exists():
            # Never overwrite a pending operation with a different request.
            existing = self._read(request_id)
            plaintext = self.private.open(request_id, candidate)
            if (
                file_fingerprint(plaintext) != existing["candidate_fingerprint"]
                or expected_fingerprint != existing["prior_fingerprint"]
                or sorted(consumers) != existing["consumers"]
                or expires_at.isoformat() != existing["expires_at"]
            ):
                raise CryptographicError(
                    "Credential request identity is already bound."
                )
            return self._receipt(existing)
        plaintext = self.private.open(request_id, candidate)
        try:
            plaintext.decode("utf-8")
        except UnicodeError:
            raise CryptographicError("Credential document must be UTF-8.") from None
        try:
            if validate(plaintext) is not True:
                raise CryptographicError("Credential validation was not acknowledged.")
        except Exception:
            raise CryptographicError(
                "Credential validation or testing failed."
            ) from None
        prior = self._selected()
        actual = file_fingerprint(prior) if prior is not None else None
        if actual != expected_fingerprint:
            raise CryptographicError("Installed credential fingerprint has changed.")
        journal = {
            "v": 1,
            "target": self.private.target,
            "request": str(request_id),
            "state": "prepared",
            "expires_at": expires_at.isoformat(),
            "prior_fingerprint": actual,
            "candidate_fingerprint": file_fingerprint(plaintext),
            "prior_ciphertext": self.private.public().seal(
                request_id, prior, purpose="rollback"
            )
            if prior is not None
            else None,
            "candidate_ciphertext": candidate,
            "consumers": sorted(consumers),
        }
        self._save(journal)
        return self._receipt(journal)

    def release(self, request_id, *, record_terminal):
        """Remove a scrubbed journal only after its terminal receipt commits.

        The database-owning callback is idempotent and must return True only
        after durable checkpoint/audit acknowledgement. Failure retains the
        journal and keeps the target unavailable to another request.
        """
        journal = self._read(request_id)
        if journal["state"] not in {"applied", "rolled_back"}:
            raise CryptographicError("Credential replacement is not terminal.")
        receipt = self._terminal_receipt(journal)
        if not callable(record_terminal) or record_terminal(receipt) is not True:
            raise CryptographicError("Credential terminal receipt is not durable.")
        self.journal_path.unlink()
        descriptor = os.open(
            self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return receipt

    def install(self, request_id, *, now):
        """Reconcile a rename that committed even if its caller saw an exception."""
        journal = self._read(request_id)
        _utc(now)
        if journal["state"] in {"applied", "rolled_back"}:
            return self._terminal_receipt(journal)
        if now >= datetime.fromisoformat(journal["expires_at"]):
            return self.rollback(request_id)
        selected = self._selected()
        fingerprint = file_fingerprint(selected) if selected is not None else None
        if fingerprint != journal["candidate_fingerprint"]:
            if (
                fingerprint != journal["prior_fingerprint"]
                or journal["state"] != "prepared"
            ):
                raise CryptographicError(
                    "Working credential has an unexpected fingerprint."
                )
            plaintext = self.private.open(request_id, journal["candidate_ciphertext"])
            if file_fingerprint(plaintext) != journal["candidate_fingerprint"]:
                raise CryptographicError(
                    "Sealed credential fingerprint is inconsistent."
                )
            write_private(self.path, plaintext)
        journal.update(state="installed", candidate_ciphertext=None)
        self._save(journal)
        return self._receipt(journal)

    def acknowledge(self, request_id, *, fingerprints, now):
        """Consume trusted current-consumer evidence, not a browser-provided mapping."""
        journal = self._read(request_id)
        _utc(now)
        if journal["state"] in {"applied", "rolled_back"}:
            return self._terminal_receipt(journal)
        if now >= datetime.fromisoformat(journal["expires_at"]):
            return self.rollback(request_id)
        if journal["state"] != "installed":
            raise CryptographicError("Credential is not installed yet.")
        if type(fingerprints) is not dict or set(fingerprints) - set(
            journal["consumers"]
        ):
            raise CryptographicError("Invalid consumer acknowledgement inventory.")
        for value in fingerprints.values():
            _fingerprint(value)
        if any(
            fingerprints.get(name) != journal["candidate_fingerprint"]
            for name in journal["consumers"]
        ):
            return self._receipt(journal)
        selected = self._selected()
        if (
            selected is None
            or file_fingerprint(selected) != journal["candidate_fingerprint"]
        ):
            raise CryptographicError(
                "Working credential has an unexpected fingerprint."
            )
        journal.update(state="applied", prior_ciphertext=None)
        self._save(journal)
        return self._receipt(journal)

    def rollback(self, request_id):
        """Restore only this request's known predecessor, then scrub sealed staging."""
        journal = self._read(request_id)
        if journal["state"] == "rolled_back":
            return self._terminal_receipt(journal)
        if journal["state"] == "applied":
            raise CryptographicError(
                "Applied credentials require a new replacement request."
            )
        selected = self._selected()
        fingerprint = file_fingerprint(selected) if selected is not None else None
        if fingerprint != journal["prior_fingerprint"]:
            if fingerprint != journal["candidate_fingerprint"]:
                raise CryptographicError(
                    "Working credential has an unexpected fingerprint."
                )
            if journal["prior_ciphertext"] is None:
                self.path.unlink()
                descriptor = os.open(
                    self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                prior = self.private.open(
                    request_id, journal["prior_ciphertext"], purpose="rollback"
                )
                if file_fingerprint(prior) != journal["prior_fingerprint"]:
                    raise CryptographicError("Sealed prior credential is inconsistent.")
                write_private(self.path, prior)
        journal.update(
            state="rolled_back", candidate_ciphertext=None, prior_ciphertext=None
        )
        self._save(journal)
        return self._receipt(journal)
