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


def prepare(version):
    """Supply fresh synthetic caller attribution without enabling authentication."""
    return prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())


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
        "validation_schema",
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
    elif defect == "validation_schema":
        values["validation_schema"] = "unsupported"
    elif defect == "version_id":
        values["id"] = uuid4()
    snapshot = AppliedConfigurationVersion.objects.create(**values)
    if defect == "projection_values":
        parish = document["sections"]["parish"][0]
        branding = parish["values"]["branding"]
        Parish.objects.create(
            configuration=snapshot,
            record_id=parish["id"],
            name="Wrong projection",
            website=parish["values"]["website"],
            timezone=parish["values"]["timezone"],
            phone=parish["values"]["phone"],
            large_logo_id=branding["large"],
            menu_logo_id=branding["menu"],
            icon_logo_id=branding["icon"],
            favicon_id=branding["favicon"],
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

    def write(version):
        """Acquire separate connections and release them even on stale-root denial."""
        try:
            barrier.wait()
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
