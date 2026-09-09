"""Versioned YAML authority and the configuration materialization protocol.

These internal primitives do not authorize a web save or implement the online
installer. The owning workflow must supply its schema validator and a durable
materializer/serialization lock; there is no permissive default implementation.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

import yaml

from parishkit.config import ConfigError, load_yaml_config
from parishkit.files import atomic_write_text

SCHEMA_VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SECTIONS = frozenset(
    {
        "parish",
        "integrations",
        "login_rules",
        "campaigns",
        "content",
        "schedules",
        "share_options",
        "ministries",
        "funds",
    }
)


def _uuid(value: object) -> str:
    """Require an explicitly serialized stable UUID, never generate one on load."""
    if type(value) is not str:
        raise ConfigError("configuration IDs must be UUID strings")
    try:
        return str(UUID(value))
    except ValueError:
        raise ConfigError("configuration IDs must be UUID strings") from None


def _digest(value: object) -> str:
    """Validate a digest without including a malformed value in diagnostics."""
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ConfigError("invalid configuration digest")
    return value


def _json_value(value: object, budget: list[int], depth: int = 0) -> object:
    """Bound canonicalization and reject implicit dates, floats, aliases/cycles.

    Dates, timestamps, and money have explicit string encodings in the schema.
    Node/depth limits also terminate cyclic or exponentially expanded YAML input.
    """
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise ConfigError("configuration document exceeds structural limits")
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, list):
        return [_json_value(item, budget, depth + 1) for item in value]
    if isinstance(value, dict) and all(type(key) is str for key in value):
        return {
            key: _json_value(item, budget, depth + 1) for key, item in value.items()
        }
    raise ConfigError("configuration values must use explicit JSON-compatible types")


@dataclass(frozen=True)
class ConfigurationVersion:
    """Immutable canonical bytes; callers cannot mutate a retained document dict."""

    version_id: UUID
    predecessor_digest: str | None
    canonical: bytes = field(repr=False)

    @property
    def digest(self) -> str:
        """Hash the normalized document, independent of YAML presentation."""
        return hashlib.sha256(self.canonical).hexdigest()

    def document(self) -> dict:
        """Return a fresh copy for a schema validator or materializer."""
        return json.loads(self.canonical)

    def yaml_text(self) -> str:
        """Render canonical non-secret data as human-readable, deterministic YAML."""
        # Escape Unicode separators such as NEL: literal YAML line breaks can
        # normalize on load and change the canonical document's digest.
        return yaml.safe_dump(self.document(), sort_keys=True, allow_unicode=False)


def parse_version(
    document: dict, *, validate_sections: Callable[[dict], None]
) -> ConfigurationVersion:
    """Validate the v1 envelope and require the owning product schema validator.

    Each section is a collection of globally unique stable-ID records. Record
    order canonicalizes by ID; user-visible ordering belongs to explicit schema
    fields. Unknown schema versions are rejected, not silently downgraded.
    """
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "version_id",
        "predecessor_digest",
        "sections",
    }:
        raise ConfigError("invalid configuration version envelope")
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != SCHEMA_VERSION
    ):
        raise ConfigError("unsupported configuration schema version")
    normalized = _json_value(document, [100_000])
    normalized["version_id"] = _uuid(normalized["version_id"])
    predecessor = normalized["predecessor_digest"]
    if predecessor is not None:
        _digest(predecessor)
    sections = normalized["sections"]
    if not isinstance(sections, dict) or set(sections) - _SECTIONS:
        raise ConfigError("unknown configuration section")
    seen: set[str] = set()
    for records in sections.values():
        if not isinstance(records, list):
            raise ConfigError("configuration sections must be record lists")
        for record in records:
            if (
                not isinstance(record, dict)
                or set(record) != {"id", "values"}
                or not isinstance(record["values"], dict)
            ):
                raise ConfigError(
                    "configuration records require IDs and value mappings"
                )
            record["id"] = _uuid(record["id"])
            if record["id"] in seen:
                raise ConfigError("configuration record IDs must be globally unique")
            seen.add(record["id"])
        records.sort(key=lambda record: record["id"])
    # Validation has no opportunity to mutate the canonical candidate. Its
    # concrete schema rejects unknown/secret fields and checks cross-references.
    canonical = json.dumps(
        normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(canonical) > 2_000_000:
        raise ConfigError("configuration document exceeds byte limit")
    validate_sections(json.loads(canonical))
    return ConfigurationVersion(UUID(normalized["version_id"]), predecessor, canonical)


class Materializer(Protocol):
    """DAT-01/installer contract; methods must provide durable, idempotent writes.

    The lock serializes all activations/recovery, not just this process. Prepare
    commits an immutable matching normalized snapshot before returning. Activate
    commits its active pointer and request/audit checkpoint together. Neither
    method can independently change a canonical document or materialized row.
    """

    def lock(self) -> AbstractContextManager[None]:
        """Hold deployment-wide configuration serialization through the workflow."""
        ...

    def active_digest(self) -> str | None:
        """Return the active applied snapshot digest, or None before bootstrap."""
        ...

    def prepare(self, version: ConfigurationVersion) -> None:
        """Durably prepare a schema-validated normalized snapshot and checkpoint."""
        ...

    def is_prepared(self, digest: str) -> bool:
        """Confirm canonical and normalized materializations both match the digest."""
        ...

    def activate(self, digest: str) -> None:
        """Activate only a fully prepared snapshot, atomically with audit/requests."""
        ...


def _sync_directory(path: Path) -> None:
    """Flush directory entries on the POSIX service filesystem used by Compose."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class AuthorityStore:
    """Narrow file store for the installer; web consumers need read methods only.

    Deployment owns directory provisioning, mount access, and symlink/ownership
    validation. No operation here creates the configured authority root.
    """

    def __init__(self, root: Path, validate_sections: Callable[[dict], None]):
        """Retain the already provisioned authority path and required validator."""
        self.root = root
        self.validate_sections = validate_sections

    def read_version(self, version_id: UUID) -> ConfigurationVersion:
        """Load a specific immutable document and validate its complete schema."""
        path = self.root / f"{version_id}.yaml"
        if not isinstance(version_id, UUID):
            raise ConfigError("invalid configuration version reference")
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigError("invalid configuration version reference")
            if metadata.st_size > 8_000_000:
                raise ConfigError("configuration YAML exceeds input byte limit")
            data = load_yaml_config(path, required=True, reject_duplicate_keys=True)
            version = parse_version(data, validate_sections=self.validate_sections)
        except (ConfigError, OSError, UnicodeError):
            raise ConfigError(
                "configuration version is unreadable or invalid"
            ) from None
        if version.version_id != version_id:
            raise ConfigError("configuration version identity mismatch")
        return version

    def manifest_reference(self) -> tuple[UUID, str] | None:
        """Read and validate the small manifest without reloading its document."""
        path = self.root / "active.yaml"
        try:
            if not stat.S_ISDIR(self.root.stat().st_mode):
                raise ConfigError("authority root must be an existing directory")
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigError("invalid active configuration manifest")
            if metadata.st_size > 4_096:
                raise ConfigError("configuration manifest exceeds input byte limit")
            manifest = load_yaml_config(path, required=True, reject_duplicate_keys=True)
            if (
                set(manifest) != {"schema_version", "version_id", "digest"}
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != SCHEMA_VERSION
            ):
                raise ConfigError("invalid active manifest schema")
            return UUID(_uuid(manifest["version_id"])), _digest(manifest["digest"])
        except (ConfigError, OSError, UnicodeError):
            raise ConfigError(
                "active configuration manifest is unreadable or invalid"
            ) from None

    def active(self) -> ConfigurationVersion | None:
        """Read the atomic reference once and verify its immutable document hash."""
        reference = self.manifest_reference()
        if reference is None:
            return None
        try:
            version = self.read_version(reference[0])
            if version.digest != reference[1]:
                raise ConfigError("active manifest digest mismatch")
        except (ConfigError, OSError, UnicodeError):
            raise ConfigError(
                "active configuration manifest is unreadable or invalid"
            ) from None
        return version

    def write_version(self, version: ConfigurationVersion) -> None:
        """Publish a fully fsynced immutable file without replacing an existing ID."""
        validated = parse_version(
            version.document(), validate_sections=self.validate_sections
        )
        if validated != version:
            raise ConfigError(
                "configuration metadata does not match its canonical document"
            )
        target = self.root / f"{version.version_id}.yaml"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.root
        ) as temporary:
            temporary.write(version.yaml_text())
            temporary.flush()
            os.fsync(temporary.fileno())
            try:
                os.link(temporary.name, target)
            except FileExistsError:
                if self.read_version(version.version_id).digest != version.digest:
                    raise ConfigError(
                        "cannot replace an immutable configuration version"
                    ) from None
            _sync_directory(self.root)

    def select(self, version: ConfigurationVersion) -> None:
        """Atomically select an already persisted matching version, then fsync."""
        if self.read_version(version.version_id).digest != version.digest:
            raise ConfigError("cannot select a mismatched configuration version")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "version_id": str(version.version_id),
            "digest": version.digest,
        }
        atomic_write_text(
            self.root / "active.yaml", yaml.safe_dump(manifest, sort_keys=True)
        )
        _sync_directory(self.root)


def apply_version(
    store: AuthorityStore, materializer: Materializer, version: ConfigurationVersion
) -> None:
    """Coordinate the prepare → YAML select → database activate contract.

    Failures before manifest selection preserve prior truth. Failures afterward
    intentionally leave a mismatch; recovery may activate only the exact already
    prepared manifest version. No side is silently chosen as newer truth.
    """
    with materializer.lock():
        active = store.active()
        current_digest = None if active is None else active.digest
        if current_digest != materializer.active_digest():
            raise ConfigError(
                "configuration recovery is required before applying a change"
            )
        if current_digest == version.digest:
            if not materializer.is_prepared(current_digest):
                raise ConfigError("active configuration materialization is incomplete")
            return
        if current_digest != version.predecessor_digest:
            raise ConfigError("configuration change is based on a stale version")
        store.write_version(version)
        materializer.prepare(version)
        if not materializer.is_prepared(version.digest):
            raise ConfigError("configuration materialization is incomplete")
        store.select(version)
        materializer.activate(version.digest)
        if materializer.active_digest() != version.digest:
            raise ConfigError("configuration activation did not converge")


def recover_active(store: AuthorityStore, materializer: Materializer) -> None:
    """Resume only an exact prepared snapshot; never rebuild or guess on mismatch."""
    with materializer.lock():
        active = store.active()
        if active is None:
            if materializer.active_digest() is not None:
                raise ConfigError("configuration manifest is missing")
            return
        if not materializer.is_prepared(active.digest):
            raise ConfigError("active configuration has no matching prepared snapshot")
        if materializer.active_digest() == active.digest:
            return
        materializer.activate(active.digest)
        if materializer.active_digest() != active.digest:
            raise ConfigError("configuration recovery did not converge")
