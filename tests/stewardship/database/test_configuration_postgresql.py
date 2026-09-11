"""Real database snapshot atomicity, append-only constraints and preparation races."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)

from ..configuration_factory import configuration_version, successor_document

# Preparation owns and commits its transaction; wrapping tests in savepoints
# would hide its durability/lock-lifetime contract.
pytestmark = pytest.mark.django_db(transaction=True)


def prepare(version):
    """Supply fresh synthetic caller attribution without enabling authentication."""
    return prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())


def insert_unchecked(version, *, predecessor=None, include_parish=True):
    """Deliberately bypass the service to test forged-but-self-consistent history."""
    from parishkit.stewardship.accounts.configuration_snapshots import (
        _digest,
        _normalized,
    )

    document = version.document()
    snapshot = AppliedConfigurationVersion.objects.create(
        id=version.version_id,
        digest=version.digest,
        schema_version=1,
        predecessor=predecessor,
        canonical_document=document,
        normalized_digest=_digest(_normalized(document)),
        validation_schema="parish-integrations-v1",
    )
    parish = document["sections"]["parish"][0]
    values = parish["values"]
    if include_parish:
        Parish.objects.create(
            configuration=snapshot,
            record_id=parish["id"],
            name=values["name"],
            website=values["website"],
            timezone=values["timezone"],
            phone=values["phone"],
            large_logo_id=values["branding"]["large"],
            menu_logo_id=values["branding"]["menu"],
            icon_logo_id=values["branding"]["icon"],
            favicon_id=values["branding"]["favicon"],
        )
    for row in document["sections"].get("integrations", []):
        AppliedIntegration.objects.create(
            configuration=snapshot,
            record_id=row["id"],
            **row["values"],
        )
    return snapshot


@pytest.mark.parametrize("defect", ["incomplete_ancestor", "different_parish"])
def test_forged_successor_cannot_extend_invalid_history(db, defect):
    """Matching local hashes cannot hide an invalid parent or replaced owner."""
    root = configuration_version()
    parent = insert_unchecked(root, include_parish=defect != "incomplete_ancestor")
    document = successor_document(root)
    if defect == "different_parish":
        document["sections"]["parish"][0]["id"] = str(uuid4())
    child = configuration_version(document)
    child_row = insert_unchecked(child, predecessor=parent)
    grandchild = configuration_version(successor_document(child))
    insert_unchecked(grandchild, predecessor=child_row)
    assert not is_prepared(child.digest)
    assert not is_prepared(grandchild.digest)
    with pytest.raises(ConfigError, match="immutable"):
        prepare(child)
    with pytest.raises(ConfigError, match="predecessor"):
        prepare(configuration_version(successor_document(grandchild)))


@pytest.mark.parametrize("integrations", ["absent", "empty", "multiple"])
def test_normalization_matches_zero_and_multiple_integration_rows(db, integrations):
    """Canonical UUID text order must match PostgreSQL ordering for every list."""
    document = configuration_version().document()
    if integrations == "absent":
        del document["sections"]["integrations"]
    elif integrations == "empty":
        document["sections"]["integrations"] = []
    else:
        document["sections"]["integrations"] = [
            {
                "id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "values": {
                    "kind": "slack",
                    "settings": {"channel_id": "C123"},
                    "credential_fingerprint": None,
                },
            },
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "values": {
                    "kind": "google_workspace",
                    "settings": {"delegated_email": "a@example.org"},
                    "credential_fingerprint": "b" * 64,
                },
            },
            *document["sections"]["integrations"],
        ]
    version = configuration_version(document)
    snapshot = prepare(version)
    assert is_prepared(version.digest)
    assert snapshot.integrations.count() == (3 if integrations == "multiple" else 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("timezone", ""),
        ("phone", "2025550123"),
        ("website", "file:///tmp/a"),
    ],
)
def test_parish_shape_constraints_apply_to_raw_inserts(db, field, value):
    """Basic database shape checks remain active even without the strict parser."""
    version = configuration_version()
    snapshot = prepare(version)
    row = snapshot.parish
    values = {
        item.attname: getattr(row, item.attname)
        for item in Parish._meta.fields
        if item.name != "id"
    }
    values[field] = value
    candidate = insert_unchecked(
        configuration_version(successor_document(version)),
        predecessor=snapshot,
        include_parish=False,
    )
    values["configuration_id"] = candidate.pk
    constraint = {
        "name": "parish_nonempty_identity",
        "timezone": "parish_nonempty_identity",
        "phone": "parish_us_phone",
        "website": "parish_website_scheme",
    }[field]
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        Parish.objects.create(**values)


def test_database_rejects_unknown_validation_evidence(db):
    """The evidence discriminator cannot falsely claim an unsupported validator."""
    with (
        pytest.raises(IntegrityError, match="configuration_validation_schema"),
        transaction.atomic(),
    ):
        AppliedConfigurationVersion.objects.create(
            digest="a" * 64,
            normalized_digest="b" * 64,
            schema_version=1,
            canonical_document={},
            validation_schema="unknown",
        )


def test_prepare_exact_projection_and_idempotent_retry(db):
    """A retry preserves all row identities, timestamps and original attribution."""
    version = configuration_version()
    first = prepare(version)
    assert is_prepared(version.digest)
    second = prepare(version)
    assert first.pk == second.pk
    assert first.created_at == second.created_at
    assert first.actor_id == second.actor_id
    assert AppliedConfigurationVersion.objects.count() == 1
    assert Parish.objects.count() == 1
    assert AppliedIntegration.objects.count() == 1
    assert first.parish.actor_id == first.actor_id
    assert first.parish.correlation_id == first.correlation_id
    assert first.integrations.get().correlation_id == first.correlation_id
    assert not is_prepared("a" * 64)


def test_successor_preserves_immutable_history(db):
    """Preparing a new parish display does not rewrite the previous profile."""
    version = configuration_version()
    first = prepare(version)
    candidate = configuration_version(successor_document(version))
    second = prepare(candidate)
    assert second.predecessor_id == first.pk
    assert second.parish.record_id == first.parish.record_id
    assert second.parish.name != first.parish.name
    assert is_prepared(version.digest) and is_prepared(candidate.digest)


def test_incomplete_predecessor_and_changed_parish_are_rejected(db):
    """Historical linkage requires a prepared parent and unchanged stable owner."""
    version = configuration_version()
    candidate = successor_document(version)
    with pytest.raises(ConfigError, match="predecessor"):
        prepare(configuration_version(candidate))
    prepare(version)
    candidate["sections"]["parish"][0]["id"] = str(uuid4())
    with pytest.raises(ConfigError, match="parish identity"):
        prepare(configuration_version(candidate))
    assert AppliedConfigurationVersion.objects.count() == 1


def test_existing_uuid_and_second_root_are_rejected(db):
    """Neither ID reuse nor a second independent parish can replace authority."""
    version = configuration_version()
    prepare(version)
    changed = version.document()
    changed["sections"]["parish"][0]["values"]["name"] = "Different"
    with pytest.raises(ConfigError, match="immutable"):
        prepare(configuration_version(changed))
    with pytest.raises(ConfigError, match="root"):
        prepare(configuration_version())


def test_partial_insert_rolls_back_every_record(db, monkeypatch):
    """A failure during projections cannot leave a seemingly prepared snapshot."""
    version = configuration_version()

    def fail(*args, **kwargs):
        """Fail the last insert after the canonical and parish records exist."""
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(AppliedIntegration.objects, "create", fail)
    with pytest.raises(RuntimeError):
        prepare(version)
    assert not AppliedConfigurationVersion.objects.exists()
    assert not Parish.objects.exists()
    assert not is_prepared(version.digest)


@pytest.mark.parametrize(
    "table",
    [
        "stewardship_configuration_version",
        "stewardship_parish",
        "stewardship_applied_integration",
    ],
)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_projection_guards_reject_raw_sql(db, table, operation):
    """Every canonical and normalized table rejects direct historical changes."""
    prepare(configuration_version())
    statement = (
        f"UPDATE {table} SET actor_id = NULL"
        if operation == "UPDATE"
        else f"DELETE FROM {table}"
    )
    with (
        pytest.raises(IntegrityError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)


def test_database_enforces_single_root(db):
    """Direct ORM insertion cannot create another root even without the service."""
    first = prepare(configuration_version())
    with pytest.raises(IntegrityError), transaction.atomic():
        AppliedConfigurationVersion.objects.create(
            id=uuid4(),
            digest="b" * 64,
            schema_version=1,
            predecessor=None,
            canonical_document=first.canonical_document,
            normalized_digest=first.normalized_digest,
            validation_schema=first.validation_schema,
        )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_parish",
        "invalid_document",
        "normalized_digest",
        "version_id",
        "projection_values",
    ],
)
def test_incomplete_or_mismatched_rows_are_never_prepared(db, defect):
    """Raw incomplete inserts cannot fool the installer readiness predicate."""
    version = configuration_version()
    document = version.document()
    from parishkit.stewardship.accounts.configuration_snapshots import (
        _digest,
        _normalized,
    )

    values = {
        "id": version.version_id,
        "digest": version.digest,
        "schema_version": 1,
        "canonical_document": document,
        "normalized_digest": _digest(_normalized(document)),
        "validation_schema": "parish-integrations-v1",
    }
    if defect == "invalid_document":
        values["canonical_document"] = {"private_key": "synthetic-private"}
    elif defect == "normalized_digest":
        values["normalized_digest"] = "a" * 64
    elif defect == "version_id":
        values["id"] = uuid4()
    snapshot = AppliedConfigurationVersion.objects.create(**values)
    if defect != "missing_parish":
        parish = document["sections"]["parish"][0]
        branding = parish["values"]["branding"]
        Parish.objects.create(
            configuration=snapshot,
            record_id=parish["id"],
            name="Wrong projection"
            if defect == "projection_values"
            else parish["values"]["name"],
            website=parish["values"]["website"],
            timezone=parish["values"]["timezone"],
            phone=parish["values"]["phone"],
            large_logo_id=branding["large"],
            menu_logo_id=branding["menu"],
            icon_logo_id=branding["icon"],
            favicon_id=branding["favicon"],
        )
        for row in document["sections"]["integrations"]:
            AppliedIntegration.objects.create(
                configuration=snapshot,
                record_id=row["id"],
                **row["values"],
            )
    assert not is_prepared(version.digest)
    with pytest.raises(
        ConfigError, match="root" if defect == "version_id" else "immutable"
    ):
        prepare(version)


@pytest.mark.parametrize(
    "column,value",
    [
        ("kind", "unknown"),
        ("credential_fingerprint", "private-secret"),
    ],
)
def test_integration_database_shape_constraints(db, column, value):
    """Known target names and fingerprints also have direct INSERT protection."""
    snapshot = prepare(configuration_version())
    values = {
        "kind": "slack",
        "settings": {"channel_id": "C123"},
        "credential_fingerprint": None,
        column: value,
    }
    with pytest.raises(IntegrityError), transaction.atomic():
        AppliedIntegration.objects.create(
            configuration=snapshot,
            record_id=uuid4(),
            **values,
        )


def test_duplicate_projection_inserts_fail(db):
    """One-to-one parish and per-version integration keys survive ORM bypass."""
    snapshot = prepare(configuration_version())
    for model, row in (
        (Parish, snapshot.parish),
        (AppliedIntegration, snapshot.integrations.get()),
    ):
        values = {
            field.attname: getattr(row, field.attname)
            for field in model._meta.fields
            if field.name != "id"
        }
        with pytest.raises(IntegrityError), transaction.atomic():
            model.objects.create(**values)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_candidate", [True, False])
def test_concurrent_first_preparation(same_candidate):
    """Independent connections safely retry one candidate or reject a second root."""
    first = configuration_version()
    candidates = [first, first if same_candidate else configuration_version()]
    barrier = Barrier(2, timeout=10)

    def synchronize_claim(execute, sql, params, many, context):
        """Both connections finish preflight before either can claim the lock."""
        if "pg_advisory_xact_lock" in sql:
            barrier.wait()
        return execute(sql, params, many, context)

    def write(version):
        """Acquire separate connections and release them even on stale-root denial."""
        try:
            with connection.execute_wrapper(synchronize_claim):
                return str(prepare(version).pk)
        except ConfigError:
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, candidates))
    assert results.count("rejected") == int(not same_candidate)
    assert AppliedConfigurationVersion.objects.count() == 1
    assert Parish.objects.count() == 1
    assert is_prepared(AppliedConfigurationVersion.objects.get().digest)


@pytest.mark.django_db(transaction=True)
def test_prepared_snapshot_survives_connection_restart():
    """Prepared state can be recovered without process memory or an active flag."""
    version = configuration_version()
    prepare(version)
    connection.close()
    assert is_prepared(version.digest)
    assert prepare(version).pk == version.version_id


@pytest.mark.parametrize("defect", ["metadata", "missing_parish", "extra_projection"])
def test_raced_candidate_is_rechecked_inside_lock(db, defect):
    """A row appearing after preflight still needs exact canonical/projection data."""
    version = configuration_version()

    def inject_before_lock(execute, sql, params, many, context):
        """Model an uncooperative direct writer at the precise claim boundary."""
        if "pg_advisory_xact_lock" in sql:
            document = version.document()
            if defect == "metadata":
                document["sections"]["parish"][0]["values"]["name"] = "Changed"
            row = insert_unchecked(
                configuration_version(document),
                include_parish=defect != "missing_parish",
            )
            if defect == "extra_projection":
                AppliedIntegration.objects.create(
                    configuration=row,
                    record_id=uuid4(),
                    kind="slack",
                    settings={"channel_id": "C123"},
                    credential_fingerprint=None,
                )
        return execute(sql, params, many, context)

    with connection.execute_wrapper(inject_before_lock), pytest.raises(ConfigError):
        prepare(version)
    assert not AppliedConfigurationVersion.objects.exists()


@pytest.mark.parametrize(
    "change", ["replace_id", "reuse_for_other_kind", "remove_then_replace"]
)
def test_integration_identity_survives_all_versions(db, change):
    """A logical integration retains its ID; a retired ID cannot name another kind."""
    root = configuration_version()
    parent = prepare(root)
    base = root
    if change != "replace_id":
        removed = successor_document(root)
        removed["sections"]["integrations"] = []
        base = configuration_version(removed)
        parent = prepare(base)
    document = successor_document(base)
    row = root.document()["sections"]["integrations"][0]
    if change == "reuse_for_other_kind":
        row["values"] = {
            "kind": "slack",
            "settings": {"channel_id": "C123"},
            "credential_fingerprint": None,
        }
    else:
        row["id"] = str(uuid4())
    document["sections"]["integrations"] = [row]
    candidate = configuration_version(document)
    with pytest.raises(ConfigError, match="predecessor"):
        prepare(candidate)
    insert_unchecked(candidate, predecessor=parent)
    assert not is_prepared(candidate.digest)
    with pytest.raises(ConfigError, match="immutable"):
        prepare(candidate)


def test_integration_readdition_with_same_identity_is_allowed(db):
    """Removing optional configuration does not retire the logical integration."""
    root = configuration_version()
    prepare(root)
    document = successor_document(root)
    document["sections"]["integrations"] = []
    removed = configuration_version(document)
    prepare(removed)
    restored = successor_document(removed)
    restored["sections"]["integrations"] = root.document()["sections"]["integrations"]
    version = configuration_version(restored)
    prepare(version)
    assert is_prepared(version.digest)


@pytest.mark.parametrize("depth", [1, 40, 130])
def test_history_reads_are_batched_before_global_lock(db, monkeypatch, depth):
    """Round trips grow per bounded batch, never per version or inside write locks."""
    from django.test.utils import CaptureQueriesContext

    from parishkit.stewardship.accounts import configuration_snapshots as snapshots

    version = configuration_version()
    parent = insert_unchecked(version)
    for _ in range(depth - 1):
        version = configuration_version(successor_document(version))
        parent = insert_unchecked(version, predecessor=parent)
    with CaptureQueriesContext(connection) as captured:
        assert is_prepared(version.digest)
    batches = (depth + snapshots.HISTORY_BATCH_SIZE - 1) // snapshots.HISTORY_BATCH_SIZE
    assert len(captured) == 1 + 3 * batches
    assert "canonical_document" not in captured[0]["sql"]

    lock_seen = False
    original_parse = snapshots.parse_version

    def parse_before_lock(*args, **kwargs):
        """Full canonical/schema revalidation must not extend the critical section."""
        assert not lock_seen
        return original_parse(*args, **kwargs)

    def track_lock(execute, sql, params, many, context):
        """Observe the real PostgreSQL advisory lock, not a mocked lock helper."""
        nonlocal lock_seen
        if "pg_advisory_xact_lock" in sql:
            lock_seen = True
        return execute(sql, params, many, context)

    candidate = configuration_version(successor_document(version))
    monkeypatch.setattr(snapshots, "parse_version", parse_before_lock)
    with (
        connection.execute_wrapper(track_lock),
        CaptureQueriesContext(connection) as captured,
    ):
        prepare(candidate)
    assert lock_seen
    lock_index = next(
        index
        for index, query in enumerate(captured)
        if "pg_advisory_xact_lock" in query["sql"]
    )
    assert not any("RECURSIVE" in query["sql"] for query in list(captured)[lock_index:])


def test_preparation_requires_an_owned_transaction(db):
    """No caller transaction may extend the lock or roll back a returned result."""
    from parishkit.stewardship.storage import StorageInvariantError

    version = configuration_version()
    with (
        transaction.atomic(),
        pytest.raises(StorageInvariantError, match="own its transaction"),
    ):
        prepare(version)
    connection.set_autocommit(False)
    try:
        with pytest.raises(StorageInvariantError, match="own its transaction"):
            prepare(version)
    finally:
        connection.rollback()
        connection.set_autocommit(True)
    assert not AppliedConfigurationVersion.objects.exists()
    prepare(version)

    def independent_reader():
        """A new connection sees committed data and can immediately claim the lock."""
        try:
            assert AppliedConfigurationVersion.objects.filter(
                pk=version.version_id
            ).exists()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", [736210, 1])
                return cursor.fetchone()[0]
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(independent_reader).result(timeout=10)


def test_history_dispatches_its_stored_validator(db, monkeypatch):
    """A stricter current validator cannot retroactively reinterpret v1 history."""
    from parishkit.stewardship.accounts import configuration_schema as schema

    version = configuration_version()
    prepare(version)

    def reject_new_input(document):
        """Represent a future schema with different input requirements."""
        raise ConfigError("Synthetic newer-schema rejection")

    monkeypatch.setattr(
        schema, "VALIDATORS", {**schema.VALIDATORS, "future-v2": reject_new_input}
    )
    monkeypatch.setattr(schema, "VALIDATION_SCHEMA", "future-v2")
    with pytest.raises(ConfigError, match="newer-schema"):
        schema.validate_sections(version.document())
    assert is_prepared(version.digest)


def test_catalog_outage_is_not_mislabeled_as_corrupt_history(db, monkeypatch):
    """Installation failures propagate distinctly from a content mismatch."""
    from parishkit.stewardship import schema_primitives as schema

    version = configuration_version()
    prepare(version)

    def unavailable(package):
        """Simulate an unreadable installed schema asset."""
        raise OSError("private-installation-path")

    schema.timezone_names.cache_clear()
    monkeypatch.setattr(schema, "files", unavailable)
    try:
        with pytest.raises(schema.SchemaEnvironmentError, match="catalog"):
            is_prepared(version.digest)
        with pytest.raises(schema.SchemaEnvironmentError, match="catalog"):
            prepare(version)
    finally:
        schema.timezone_names.cache_clear()
