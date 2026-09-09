"""Pure non-secret schema validation, including hostile types and private errors."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import ConfigurationVersion, parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    INTEGRATION_FIELDS,
)
from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot

from .configuration_factory import configuration_document, configuration_version


@pytest.mark.parametrize("kind,fields", INTEGRATION_FIELDS.items())
def test_supported_integration_shapes(kind, fields):
    """Every supported integration accepts explicit metadata, never credentials."""
    document = configuration_document()
    document["sections"]["integrations"][0]["values"] = {
        "kind": kind,
        "settings": {
            name: {
                "email": "staff@example.org",
                "url": "https://backup.example.org/bucket",
                "text": "example-id",
            }[value_type]
            for name, value_type in fields.items()
        },
        "credential_fingerprint": None,
    }
    assert configuration_version(document).document() == document


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("name", " x "),
        ("name", "a" * 255),
        ("name", "bad\x7f"),
        ("website", "https://user:private-secret@example.org"),
        ("website", "https://example.org/?token=private-secret"),
        ("website", "https://example.org/#private-secret"),
        ("website", "file:///tmp/a"),
        ("website", 1),
        ("timezone", "No/Such_Zone"),
        ("timezone", "/etc/passwd"),
        ("phone", "+11025550123"),
        ("phone", 2025550123),
        ("branding", []),
        ("branding", {}),
        ("unknown_private_key", "private-secret"),
    ],
)
def test_parish_rejects_invalid_or_unknown_fields(field, value):
    """Invalid private values never appear in user-visible validation errors."""
    document = configuration_document()
    document["sections"]["parish"][0]["values"][field] = value
    with pytest.raises(ConfigError) as error:
        configuration_version(document)
    assert "private-secret" not in str(error.value)
    assert "unknown_private_key" not in str(error.value)


@pytest.mark.parametrize(
    "value", ["not-uuid", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", 12]
)
def test_branding_requires_canonical_opaque_references(value):
    """Unvalidated paths or implicit identifier encodings cannot reach projections."""
    document = configuration_document()
    document["sections"]["parish"][0]["values"]["branding"]["large"] = value
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", []),
        ("kind", "unknown"),
        ("settings", []),
        ("settings", {"api_key": "private-secret"}),
        ("settings", {"organization_id": "x", "api_key": "private-secret"}),
        ("credential_fingerprint", "private-secret"),
        ("credential_fingerprint", 1),
        ("extra", "private-secret"),
    ],
)
def test_integration_rejects_arbitrary_metadata(field, value):
    """There is no untyped metadata escape hatch for plaintext credentials."""
    document = configuration_document()
    document["sections"]["integrations"][0]["values"][field] = value
    with pytest.raises(ConfigError) as error:
        configuration_version(document)
    assert "private-secret" not in str(error.value)


def test_future_sections_and_duplicate_singletons_fail_closed():
    """Only empty future sections are tolerated; implemented records are unique."""
    for section in ("campaigns", "parish", "integrations"):
        document = configuration_document()
        record = {"id": str(uuid4()), "values": {}}
        if section == "integrations":
            record["values"] = document["sections"][section][0]["values"]
        document["sections"].setdefault(section, []).append(record)
        with pytest.raises(ConfigError):
            configuration_version(document)
    document = configuration_document()
    document["sections"]["parish"] = []
    with pytest.raises(ConfigError):
        configuration_version(document)
    document = configuration_document()
    document["sections"]["campaigns"] = []
    configuration_version(document)


def test_preparation_revalidates_before_opening_database():
    """A permissive envelope parser cannot inject secrets into persisted JSON."""
    document = configuration_document()
    document["sections"]["parish"][0]["values"]["api_key"] = "private-secret"
    forged = parse_version(document, validate_sections=lambda unused: None)
    with pytest.raises(ConfigError):
        prepare_snapshot(forged, actor_id=None, correlation_id=uuid4())
    valid = configuration_version()
    with pytest.raises(ConfigError, match="metadata"):
        prepare_snapshot(
            ConfigurationVersion(uuid4(), None, valid.canonical),
            actor_id=None,
            correlation_id=uuid4(),
        )


@pytest.mark.parametrize(
    "version,actor,correlation",
    [
        ({}, None, uuid4()),
        (configuration_version(), "private", uuid4()),
        (configuration_version(), None, "private"),
    ],
)
def test_storage_attribution_rejects_invalid_types_before_io(
    version, actor, correlation
):
    """A caller cannot reach a transaction using malformed authority metadata."""
    with pytest.raises(TypeError):
        prepare_snapshot(version, actor_id=actor, correlation_id=correlation)


def test_history_walk_rejects_cycles_without_a_recursion_limit():
    """Even malformed imported graphs terminate; long legitimate chains work."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.configuration_snapshots import _history

    head = SimpleNamespace(pk=uuid4(), predecessor=None)
    head.predecessor = head
    with pytest.raises(ConfigError, match="cycle"):
        list(_history(head))
    head = None
    for _ in range(1100):
        head = SimpleNamespace(pk=uuid4(), predecessor=head)
    assert len(list(_history(head))) == 1100


@pytest.mark.parametrize(
    "zone", ["America", "Europe", "Etc", "US", "posix", "private-zone"]
)
def test_nonleaf_timezone_rejected_without_path_disclosure(zone):
    """User input is only looked up in a leaf-name set, never opened as a path."""
    document = configuration_document()
    document["sections"]["parish"][0]["values"]["timezone"] = zone
    with pytest.raises(ConfigError) as error:
        configuration_version(document)
    assert zone not in str(error.value)


def test_timezone_validation_ignores_host_only_catalog(tmp_path):
    """An OS-installed zone outside the pinned application catalog stays invalid."""
    import zoneinfo
    from importlib.resources import files

    (tmp_path / "HostOnly").write_bytes(
        files("tzdata.zoneinfo").joinpath("UTC").read_bytes()
    )
    old_path = zoneinfo.TZPATH
    try:
        zoneinfo.reset_tzpath([str(tmp_path)])
        assert zoneinfo.ZoneInfo.no_cache("HostOnly").key == "HostOnly"
        document = configuration_document()
        document["sections"]["parish"][0]["values"]["timezone"] = "HostOnly"
        with pytest.raises(ConfigError):
            configuration_version(document)
        # The normal bundled leaf remains valid even with the OS path replaced.
        configuration_version()
    finally:
        zoneinfo.reset_tzpath(old_path)


@pytest.mark.parametrize("failure", [OSError, UnicodeError, ModuleNotFoundError])
def test_timezone_catalog_failure_is_safe(monkeypatch, failure):
    """Broken installations fail closed without exposing resource paths."""
    from parishkit.stewardship.accounts import configuration_schema as schema

    def fail(package):
        """Simulate unreadable/corrupt/missing installed resources, not input files."""
        raise failure("private-resource-location")

    schema._timezone_names.cache_clear()
    monkeypatch.setattr(schema, "files", fail)
    try:
        with pytest.raises(
            schema.SchemaEnvironmentError, match="catalog is unavailable"
        ) as error:
            configuration_version()
        assert "private-resource-location" not in str(error.value)
    finally:
        schema._timezone_names.cache_clear()


def test_frozen_catalog_ignores_changed_dependency(monkeypatch):
    """Changing the installed tzdata catalog cannot change the v1 accepted set."""
    from parishkit.stewardship.accounts import configuration_schema as schema

    original = schema.files
    requests = []

    def only_application_data(package):
        """A missing or changed dependency must never be consulted for membership."""
        requests.append(package)
        if package == "tzdata":
            raise ModuleNotFoundError("Synthetic upgraded dependency")
        return original(package)

    schema._timezone_names.cache_clear()
    monkeypatch.setattr(schema, "files", only_application_data)
    try:
        configuration_version()
        assert requests == [schema.__package__]
    finally:
        schema._timezone_names.cache_clear()


def test_frozen_catalog_digest_rejects_asset_drift(tmp_path, monkeypatch):
    """A damaged or silently regenerated schema asset is an installation error."""
    from parishkit.stewardship.accounts import configuration_schema as schema

    (tmp_path / "timezone_names_v1.txt").write_text("Synthetic/NewZone\n")
    monkeypatch.setattr(schema, "files", lambda package: tmp_path)
    schema._timezone_names.cache_clear()
    try:
        with pytest.raises(schema.SchemaEnvironmentError):
            configuration_version()
    finally:
        schema._timezone_names.cache_clear()


def test_unknown_schema_never_selects_current_validator():
    """Unknown stored discriminators are rejected, not silently upgraded."""
    from parishkit.stewardship.accounts.configuration_schema import validator_for

    with pytest.raises(ConfigError, match="Unsupported"):
        validator_for("private-unknown-schema")


def test_wheel_includes_frozen_schema_catalog(tmp_path):
    """Normal credential-free package builds retain the exact application asset."""
    import hashlib
    import subprocess
    import sys
    from pathlib import Path
    from zipfile import ZipFile

    from parishkit.stewardship.accounts.configuration_schema import (
        _TIMEZONE_NAMES_SHA256,
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    with ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        payload = wheel.read("parishkit/stewardship/accounts/timezone_names_v1.txt")
    assert hashlib.sha256(payload).hexdigest() == _TIMEZONE_NAMES_SHA256
