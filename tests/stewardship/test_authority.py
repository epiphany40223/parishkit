"""ARC-02 authority contract tests with a fake durable-materializer boundary."""

import json
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from parishkit import config as shared_config
from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import (
    AuthorityStore,
    ConfigurationVersion,
    apply_version,
    parse_version,
    recover_active,
)


def validate_synthetic_schema(document):
    """A test-only complete schema; no production validator is implied here."""
    for records in document["sections"].values():
        for record in records:
            if set(record["values"]) != {"label"} or not isinstance(
                record["values"]["label"], str
            ):
                raise ConfigError("invalid synthetic schema")


def document(number=1, predecessor=None):
    """Build deterministic version/record identifiers with synthetic values."""
    return {
        "schema_version": 1,
        "version_id": str(UUID(int=number)),
        "predecessor_digest": predecessor,
        "sections": {
            "parish": [
                {"id": str(UUID(int=10)), "values": {"label": "Synthetic parish"}}
            ]
        },
    }


def candidate(number=1, predecessor=None):
    """Construct only schema-validated candidate objects in normal tests."""
    return parse_version(
        document(number, predecessor), validate_sections=validate_synthetic_schema
    )


class MemoryMaterializer:
    """Fake the DB protocol, including durable prepared state across failures."""

    def __init__(self):
        """Start with no active/prepared snapshot and no injected failure."""
        self.current = None
        self.prepared = set()
        self.locked = False
        self.failure = None

    @contextmanager
    def lock(self):
        """Prove every coordinator call holds its required serialization lock."""
        assert not self.locked
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def active_digest(self):
        """Read authoritative state only inside the coordinator's lock."""
        assert self.locked
        return self.current

    def prepare(self, version):
        """Model a durable prepare or an injected pre-commit database failure."""
        assert self.locked
        if self.failure == "prepare":
            raise RuntimeError("synthetic prepare failure")
        if self.failure != "incomplete":
            self.prepared.add(version.digest)

    def is_prepared(self, digest):
        """Report whether the canonical and normalized snapshots match."""
        assert self.locked
        return digest in self.prepared

    def activate(self, digest):
        """Model a durable activation, exception, or incomplete adapter commit."""
        assert self.locked and digest in self.prepared
        if self.failure == "activate":
            raise RuntimeError("synthetic activation failure")
        if self.failure != "nonconverging":
            self.current = digest


@pytest.fixture
def authority(tmp_path):
    """Use an isolated, already provisioned directory, never runtime defaults."""
    if os.name != "posix":
        pytest.skip("POSIX authority durability tests require Linux Compose or WSL")
    return AuthorityStore(tmp_path, validate_synthetic_schema)


def test_inaccessible_manifest_is_not_absent(authority, monkeypatch):
    """Even when exists() suppresses access errors, recovery must fail closed."""
    original = Path.lstat

    def inaccessible(path):
        """Model unreadable directory entries independently of the host Python."""
        if path.name == "active.yaml":
            raise PermissionError("synthetic-private-authority")
        return original(path)

    monkeypatch.setattr(Path, "lstat", inaccessible)
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "exists", lambda path: False)
    with pytest.raises(ConfigError, match="manifest is unreadable or invalid") as exc:
        recover_active(authority, MemoryMaterializer())
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("kind", ["missing", "file", "inaccessible"])
def test_invalid_authority_root_is_not_unconfigured(tmp_path, monkeypatch, kind):
    """Missing mounts and inaccessible roots fail before materializer writes."""
    root = tmp_path / "authority"
    if kind == "file":
        root.write_text("synthetic-original")
    elif kind == "inaccessible":
        root.mkdir()
        original_stat = Path.stat

        def inaccessible(path, **kwargs):
            """Inject a root access error without depending on the test UID."""
            if path == root:
                raise PermissionError("synthetic-private-path")
            return original_stat(path, **kwargs)

        monkeypatch.setattr(Path, "stat", inaccessible)
    store = AuthorityStore(root, validate_synthetic_schema)
    materializer = MemoryMaterializer()
    with pytest.raises(ConfigError, match="manifest is unreadable or invalid"):
        apply_version(store, materializer, candidate())
    assert materializer.current is None
    assert not materializer.prepared
    if kind == "missing":
        assert not root.exists()
    elif kind == "file":
        assert root.read_text() == "synthetic-original"


def test_select_cannot_create_missing_authority_root(tmp_path):
    """Selection requires the persisted version before the manifest writer runs."""
    root = tmp_path / "absent" / "authority"
    store = AuthorityStore(root, validate_synthetic_schema)
    with pytest.raises(ConfigError, match="version is unreadable or invalid"):
        store.select(candidate())
    assert not root.parent.exists()


@pytest.mark.parametrize(
    "value", ["a\u0085b", "a\u2028b", "a\u2029b", "a\nb", "María ☺"]
)
def test_unicode_authority_round_trip_preserves_digest(authority, value):
    """YAML emission must preserve text even for normalized line separators."""
    data = document()
    data["sections"]["parish"][0]["values"]["label"] = value
    version = parse_version(data, validate_sections=validate_synthetic_schema)
    authority.write_version(version)
    assert authority.read_version(version.version_id) == version
    authority.select(version)
    assert authority.active() == version


@pytest.mark.parametrize("target", ["manifest", "version"])
@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_authority_is_rejected_before_read(
    authority, monkeypatch, target, kind
):
    """Neither authority boundary may block reading non-regular filesystem entries."""
    version = candidate()
    name = "active.yaml" if target == "manifest" else f"{version.version_id}.yaml"
    path = authority.root / name
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)

    def unexpected_read(*args, **kwargs):
        """Assert that input type checking happens before any YAML read."""
        pytest.fail("non-regular manifest reached the YAML reader")

    monkeypatch.setattr(
        "parishkit.stewardship.accounts.authority.load_yaml_config", unexpected_read
    )
    with pytest.raises(ConfigError, match="is unreadable or invalid"):
        if target == "manifest":
            authority.active()
        else:
            authority.read_version(version.version_id)


@pytest.mark.parametrize("target", ["version", "manifest"])
@pytest.mark.parametrize("fault", ["depth", "nodes", "bytes", "recursion"])
def test_strict_resource_failures_are_sanitized(authority, monkeypatch, target, fault):
    """Both authority read boundaries sanitize parser limits and recursion errors."""
    version = candidate()
    authority.write_version(version)
    authority.select(version)
    name = f"{version.version_id}.yaml" if target == "version" else "active.yaml"
    if fault == "depth":
        # Flow notation fits even the manifest's pre-existing 4,096-byte limit,
        # so it exercises the strict parser instead of the earlier stat check.
        (authority.root / name).write_text("key: " + "[" * 550 + "0" + "]" * 550)
    elif fault == "nodes":
        monkeypatch.setattr(shared_config, "STRICT_YAML_MAX_NODES", 4)
    elif fault == "bytes":
        monkeypatch.setattr(shared_config, "STRICT_YAML_MAX_BYTES", 16)
    else:

        def fail(*args, **kwargs):
            """Simulate recursion below the consumer's configuration boundary."""
            raise RecursionError("synthetic-secret")

        monkeypatch.setattr(shared_config.yaml, "load", fail)
    error = (
        "configuration version is unreadable or invalid"
        if target == "version"
        else "active configuration manifest is unreadable or invalid"
    )
    with pytest.raises(ConfigError, match=f"^{error}$"):
        if target == "version":
            authority.read_version(version.version_id)
        else:
            authority.active()


def test_canonical_round_trip_preserves_ids_and_ignores_order():
    """Equivalent key and record order produces identical canonical bytes/hash."""
    data = document()
    data["sections"]["parish"].append(
        {"id": str(UUID(int=11)), "values": {"label": "Second"}}
    )
    version = parse_version(data, validate_sections=validate_synthetic_schema)
    data["sections"]["parish"].reverse()
    assert parse_version(data, validate_sections=validate_synthetic_schema) == version
    assert (
        parse_version(
            yaml.safe_load(version.yaml_text()),
            validate_sections=validate_synthetic_schema,
        )
        == version
    )
    data["sections"]["parish"][0]["values"]["label"] = "Changed after parsing"
    assert "Changed after parsing" not in version.canonical.decode()
    copy = version.document()
    copy["sections"].clear()
    assert version.document()["sections"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", 0),
        ("schema_version", 2),
        ("schema_version", True),
        ("version_id", "unknown"),
        ("version_id", 1),
        ("predecessor_digest", "unknown"),
        ("sections", {"unknown": []}),
        ("sections", []),
        ("sections", {"parish": "wrong"}),
    ],
)
def test_bad_envelopes_and_unsupported_schema_versions(key, value):
    """Unknown versions fail closed until an explicit importer migration exists."""
    data = document()
    data[key] = value
    with pytest.raises(ConfigError):
        parse_version(data, validate_sections=validate_synthetic_schema)


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"id": str(UUID(int=10)), "values": []},
        {"id": str(UUID(int=10)), "values": {"secret": "synthetic-secret"}},
    ],
)
def test_bad_record_schema(record):
    """Envelope validation cannot stand in for the mandatory owning schema."""
    data = document()
    data["sections"]["parish"] = [record]
    with pytest.raises(ConfigError):
        parse_version(data, validate_sections=validate_synthetic_schema)


def test_duplicate_ids_are_rejected_across_sections():
    """Stable-ID references must not become ambiguous across schema collections."""
    data = document()
    data["sections"]["campaigns"] = data["sections"]["parish"]
    with pytest.raises(ConfigError):
        parse_version(data, validate_sections=validate_synthetic_schema)


@pytest.mark.parametrize("value", [1.5, date(2026, 9, 7), {1: "not a string key"}])
def test_implicit_or_noncanonical_types_are_rejected(value):
    """Dates/money require explicit string encoding; floats never enter YAML truth."""
    data = document()
    data["sections"]["parish"][0]["values"]["label"] = value
    with pytest.raises(ConfigError):
        parse_version(data, validate_sections=validate_synthetic_schema)


def test_structural_cycles_and_oversized_values_fail():
    """Malformed YAML graphs cannot recurse forever or evade the byte bound."""
    data = document()
    data["sections"]["parish"][0]["values"]["label"] = data
    with pytest.raises(ConfigError):
        parse_version(data, validate_sections=validate_synthetic_schema)
    data = document()
    data["sections"]["parish"][0]["values"]["label"] = "x" * 2_000_001
    with pytest.raises(ConfigError):
        parse_version(data, validate_sections=validate_synthetic_schema)


def test_immutable_file_and_manifest_round_trip(authority):
    """Writing history is not activation; retries cannot replace an existing ID."""
    first = candidate()
    assert authority.active() is None
    authority.write_version(first)
    authority.write_version(first)
    assert authority.active() is None
    assert authority.read_version(first.version_id) == first
    authority.select(first)
    assert authority.active() == first
    assert (authority.root / "active.yaml").stat().st_mode & 0o777 == 0o600
    assert (authority.root / f"{first.version_id}.yaml").stat().st_mode & 0o777 == 0o600
    altered = document()
    altered["sections"]["parish"][0]["values"]["label"] = "changed"
    with pytest.raises(ConfigError):
        authority.write_version(
            parse_version(altered, validate_sections=validate_synthetic_schema)
        )
    assert authority.active() == first


def test_apply_is_idempotent_and_rejects_stale_change(authority):
    """Replayed exact applies succeed; obsolete base versions cannot overwrite."""
    materializer = MemoryMaterializer()
    first = candidate()
    apply_version(authority, materializer, first)
    apply_version(authority, materializer, first)
    assert materializer.current == authority.active().digest == first.digest
    with pytest.raises(ConfigError):
        apply_version(authority, materializer, candidate(2))
    second = candidate(2, first.digest)
    apply_version(authority, materializer, second)
    assert materializer.current == second.digest


@pytest.mark.parametrize(
    "failure", ["prepare", "incomplete", "activate", "nonconverging"]
)
def test_activation_failure_and_recovery(authority, failure):
    """Pre-selection failures preserve truth; post-selection failures need recovery."""
    materializer = MemoryMaterializer()
    first = candidate()
    apply_version(authority, materializer, first)
    second = candidate(2, first.digest)
    materializer.failure = failure
    with pytest.raises((RuntimeError, ConfigError)):
        apply_version(authority, materializer, second)
    assert materializer.current == first.digest
    if failure in {"prepare", "incomplete"}:
        assert authority.active().digest == first.digest
    else:
        assert authority.active().digest == second.digest
        with pytest.raises(ConfigError):
            apply_version(authority, materializer, candidate(3, first.digest))
        materializer.failure = None
        recover_active(authority, materializer)
        recover_active(authority, materializer)
        assert materializer.current == second.digest


def test_manifest_replace_failure_preserves_prior_selection(authority, monkeypatch):
    """An interrupted atomic manifest replacement cannot publish partial YAML."""
    materializer = MemoryMaterializer()
    first = candidate()
    apply_version(authority, materializer, first)

    def fail_replace(*args):
        """Inject a rename failure after the manifest temporary file is fsynced."""
        raise OSError("synthetic rename failure")

    monkeypatch.setattr("parishkit.files.os.replace", fail_replace)
    with pytest.raises(OSError):
        apply_version(authority, materializer, candidate(2, first.digest))
    assert authority.active() == first
    assert materializer.current == first.digest


def test_recovery_never_guesses_an_unprepared_or_missing_version(authority):
    """Neither a missing manifest nor a mismatched DB snapshot wins implicitly."""
    materializer = MemoryMaterializer()
    recover_active(authority, materializer)
    materializer.current = "0" * 64
    with pytest.raises(ConfigError):
        recover_active(authority, materializer)
    first = candidate()
    authority.write_version(first)
    authority.select(first)
    with pytest.raises(ConfigError):
        recover_active(authority, materializer)
    materializer.prepared.add(first.digest)
    materializer.failure = "nonconverging"
    with pytest.raises(ConfigError):
        recover_active(authority, materializer)


def test_corrupt_manifest_and_file_are_rejected(authority):
    """Tampering or interrupted input cannot silently select a different truth."""
    first = candidate()
    authority.write_version(first)
    authority.select(first)
    manifest_path = authority.root / "active.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["digest"] = "0" * 64
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ConfigError):
        authority.active()
    manifest_path.write_text("secret: [synthetic-secret", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        authority.active()
    assert "synthetic-secret" not in str(exc.value)
    (authority.root / f"{first.version_id}.yaml").write_text(
        json.dumps(document(2)), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        authority.read_version(first.version_id)


def test_forged_candidate_metadata_cannot_be_written(authority):
    """Even internal callers cannot persist inconsistent envelope metadata."""
    first = candidate()
    with pytest.raises(ConfigError):
        authority.write_version(
            ConfigurationVersion(UUID(int=2), None, first.canonical)
        )


def test_missing_invalid_and_symlink_references_fail_closed(authority):
    """No malformed or symlink reference can select arbitrary configuration files."""
    with pytest.raises(ConfigError):
        parse_version({}, validate_sections=validate_synthetic_schema)
    with pytest.raises(ConfigError):
        authority.read_version("not-a-uuid")
    with pytest.raises(ConfigError):
        authority.read_version(UUID(int=999))
    first = candidate()
    authority.write_version(first)
    link = authority.root / "active.yaml"
    link.symlink_to(authority.root / f"{first.version_id}.yaml")
    with pytest.raises(ConfigError):
        authority.active()


def test_invalid_manifest_schema_and_input_limits(authority):
    """Reject unsupported or oversized manifest/version input before using it."""
    manifest = authority.root / "active.yaml"
    for text in ("schema_version: 2\n", "x" * 4097):
        manifest.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError):
            authority.active()
    version_path = authority.root / f"{UUID(int=1)}.yaml"
    version_path.write_text("x" * 8_000_001, encoding="utf-8")
    with pytest.raises(ConfigError):
        authority.read_version(UUID(int=1))


def test_selection_rejects_mismatched_candidate(authority):
    """A same-ID candidate with changed contents cannot become the manifest target."""
    first = candidate()
    authority.write_version(first)
    altered = document()
    altered["sections"]["parish"][0]["values"]["label"] = "Changed"
    version = parse_version(altered, validate_sections=validate_synthetic_schema)
    with pytest.raises(ConfigError):
        authority.select(version)
    assert authority.active() is None


def test_applied_retry_requires_complete_materialization(authority):
    """Matching active pointers alone do not establish normalized-snapshot validity."""
    materializer = MemoryMaterializer()
    version = candidate()
    apply_version(authority, materializer, version)
    materializer.prepared.clear()
    with pytest.raises(ConfigError):
        apply_version(authority, materializer, version)
    with pytest.raises(ConfigError):
        recover_active(authority, materializer)
