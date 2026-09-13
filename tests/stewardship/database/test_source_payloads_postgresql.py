"""Canonical payload reuse, database digest enforcement and coherent membership."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.source.canonical import canonical_payload
from parishkit.stewardship.source.leases import acquire_source
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.version_models import ENTITY_MODELS, SourceFamily

from .source_builders import running_source_task as running_task

pytestmark = pytest.mark.django_db(transaction=True)


def version(kind="family", key="1", organization=100, **values):
    """Use normalized synthetic relationships rather than production source data."""
    model, _ = ENTITY_MODELS[kind]
    relations = {
        field.name: values.get(
            field.name, "family" if field.name == "owner_kind" else "1"
        )
        for field in model._meta.fields
        if field.name.endswith("_key")
        and field.name != "source_key"
        or field.name == "owner_kind"
    }
    canonical, digest = canonical_payload({"name": "Synthetic", **relations, **values})
    return model.objects.create(
        organization_id=organization,
        source_key=key,
        digest=digest,
        canonical=canonical,
        **relations,
    )


def staging():
    """Stage under a real task/source fence without any provider calls."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    owner = running_task()
    claim = acquire_source(**owner, phase="full")
    snapshot = SourceSnapshot.objects.create(
        organization_id=100,
        kind="full",
        task_id=claim.task_id,
        source_fence=claim.fence,
        started_at=datetime.now(UTC),
    )
    return snapshot, claim


@pytest.mark.parametrize("kind", ENTITY_MODELS)
def test_each_entity_has_a_typed_immutable_version_and_membership(kind):
    """Every entity kind maps through a real version FK, not a soft generic UUID."""
    snapshot, _ = staging()
    payload = version(kind)
    _, membership = ENTITY_MODELS[kind]
    member = membership.objects.create(
        snapshot=snapshot, source_key="1", payload=payload
    )
    assert (
        membership.objects.select_related("payload")
        .get(pk=member.pk)
        .payload.payload["name"]
        == "Synthetic"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        membership.objects.create(snapshot=snapshot, source_key="1", payload=payload)


def test_unchanged_identity_and_digest_cannot_duplicate_payload_rows():
    """Mandatory content addressing is enforced below the refresh service."""
    first = version()
    with pytest.raises(IntegrityError), transaction.atomic():
        version()
    changed = version(name="Changed")
    assert first.pk != changed.pk
    assert version(key="2").digest == first.digest
    assert version(organization=101).digest == first.digest


@pytest.mark.parametrize(
    "canonical,digest",
    [("{}", "0" * 64), ('{ "b":1,"a":2}', None), ('{"a":1.2}', None)],
)
def test_sql_refuses_forged_digest_noncanonical_text_and_floating_point(
    canonical, digest
):
    """Raw insertion cannot evade canonicalization or change immutable content."""
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_source_family "
            "(id,correlation_id,organization_id,source_key,canonical,digest) "
            "VALUES (%s,%s,100,'1',%s,"
            "COALESCE(%s,encode(sha256(convert_to(%s,'UTF8')),'hex')))",
            (uuid4(), uuid4(), canonical, digest, canonical),
        )


def test_database_and_python_canonicalizers_agree_on_nested_unicode_scalars():
    """Digest equality must hold independently of locale or provider key ordering."""
    text, _ = canonical_payload(
        {"é": [None, True, "a\nb", {"z": -1}], "a": {"bb": 2, "a": 1}}
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_source_canonical(%s::jsonb)", (text,))
        assert cursor.fetchone()[0] == text


@pytest.mark.parametrize("change", [{"source_key": "2"}, {"organization": 101}])
def test_membership_refuses_cross_identity_or_cross_organization_payloads(change):
    """Snapshot maps cannot silently replace one parish/entity with another."""
    snapshot, _ = staging()
    payload = version(organization=change.get("organization", 100))
    _, membership = ENTITY_MODELS["family"]
    with pytest.raises(IntegrityError), transaction.atomic():
        membership.objects.create(
            snapshot=snapshot, source_key=change.get("source_key", "1"), payload=payload
        )


def test_payload_and_membership_rewrites_are_rejected_by_sql():
    """SQL invariants remain effective without ImmutableRecord's ORM helpers."""
    snapshot, _ = staging()
    payload = version()
    ENTITY_MODELS["family"][1].objects.create(
        snapshot=snapshot, source_key="1", payload=payload
    )
    for statement in (
        "UPDATE stewardship_source_family SET source_key='2' WHERE id=%s",
        "DELETE FROM stewardship_source_family WHERE id=%s",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, (payload.pk,))
    for statement in (
        "UPDATE stewardship_snapshot_family SET source_key='2' WHERE snapshot_id=%s",
        "DELETE FROM stewardship_snapshot_family WHERE snapshot_id=%s",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, (snapshot.pk,))
    assert SourceFamily.objects.get().payload["name"] == "Synthetic"
