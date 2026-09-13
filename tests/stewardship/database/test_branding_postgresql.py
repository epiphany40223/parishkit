"""Branding receipt state and configuration pinning under real PostgreSQL guards."""

from datetime import timedelta
from uuid import uuid4, uuid5

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.accounts.branding_models import BrandingAsset, BrandingBundle
from parishkit.stewardship.accounts.configuration_errors import (
    ConfigurationReadinessUnavailable,
)
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.request_models import ConfigurationRequestCheckpoint

from .campaign_builders import change, initialized

pytestmark = pytest.mark.django_db(transaction=True)


def bundle(version, actor, **changes):
    """Synthetic metadata; filesystem durability has separate real-file tests."""
    return BrandingBundle.objects.create(
        **dict(
            owner_id=actor,
            actor_id=actor,
            session_id=uuid4(),
            base_id=version.version_id,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        | changes
    )


def asset(row, label, **changes):
    """Stable reference matches the file layer's deterministic per-bundle UUID."""
    return BrandingAsset.objects.create(
        **dict(
            id=uuid5(row.pk, label),
            bundle=row,
            label=label,
            width=20,
            height=20,
            size=100,
            sha256="a" * 64,
            actor_id=row.owner_id,
        )
        | changes
    )


def advance(row, state):
    """Let SQL enforce legal transitions, complete inventories and retained history."""
    BrandingBundle.objects.filter(pk=row.pk).update(
        state=state, version=F("version") + 1
    )
    row.refresh_from_db()


def ready(version, actor):
    """A complete normalized receipt set is required before preview/application."""
    row = bundle(version, actor)
    values = {
        label: str(asset(row, label).pk)
        for label in ("large", "menu", "icon", "favicon")
    }
    advance(row, "ready")
    return row, values


def branding_patch(version, values):
    """Only UUID references enter YAML; never file paths, digests or image bytes."""
    parish = version.document()["sections"]["parish"][0]
    return [
        {
            "operation": "update",
            "section": "parish",
            "id": parish["id"],
            "values": {"branding": values},
        }
    ]


def test_normalizing_branding_holds_the_same_configuration_intent_for_retry(tmp_path):
    """An unfinished valid bundle is not a permanently invalid configuration."""
    store, version, actor = initialized(tmp_path)
    row = bundle(version, actor)
    values = {
        label: str(asset(row, label).pk)
        for label in ("large", "menu", "icon", "favicon")
    }
    request = record_request(
        base_digest=version.digest,
        patch=branding_patch(version, values),
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    with pytest.raises(ConfigurationReadinessUnavailable):
        install_request(store, request_id=request.request_id, correlation_id=uuid4())
    assert store.active() == version
    assert not ConfigurationRequestCheckpoint.objects.filter(
        request_id=request.request_id, state="failed"
    ).exists()
    advance(row, "ready")
    assert (
        install_request(
            store, request_id=request.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )


def test_complete_bundle_can_apply_and_historical_configuration_pins_it(tmp_path):
    """Applying a later profile cannot make the original logo eligible for cleanup."""
    store, version, actor = initialized(tmp_path)
    row, values = ready(version, actor)
    receipt = change(store, version, actor, branding_patch(version, values))
    assert receipt.state == "applied"
    current = store.active()
    parish = current.document()["sections"]["parish"][0]
    assert parish["values"]["branding"] == values
    change(
        store,
        current,
        actor,
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Updated Parish"},
            }
        ],
    )
    with pytest.raises(DatabaseError, match="pins branding"), transaction.atomic():
        advance(row, "cleanup_pending")
    row.refresh_from_db()
    assert row.state == "ready"


def test_unreferenced_bundle_cleanup_preserves_opaque_receipts(tmp_path):
    """Filesystem cleanup owns removal; SQL retains only safe historical metadata."""
    _, version, actor = initialized(tmp_path)
    row, _ = ready(version, actor)
    advance(row, "cleanup_pending")
    advance(row, "scrubbed")
    assert row.state == "scrubbed" and row.assets.count() == 4
    with pytest.raises(DatabaseError), transaction.atomic():
        advance(row, "ready")


def test_partial_bundle_cannot_become_ready_or_gain_assets_after_cleanup(tmp_path):
    """A crash partway through metadata insertion never publishes a partial bundle."""
    _, version, actor = initialized(tmp_path)
    row = bundle(version, actor)
    asset(row, "large")
    with pytest.raises(DatabaseError), transaction.atomic():
        advance(row, "ready")
    advance(row, "cleanup_pending")
    with pytest.raises(DatabaseError), transaction.atomic():
        asset(row, "menu")


@pytest.mark.parametrize(
    "values",
    [
        {"label": "unknown"},
        {"width": 129},
        {"height": 129},
        {"size": 0},
        {"sha256": "not-a-digest"},
        {"actor_id": uuid4()},
    ],
)
def test_asset_raw_constraints_reject_invalid_receipts(tmp_path, values):
    """SQL repeats variant bounds and ownership without trusting the model caller."""
    _, version, actor = initialized(tmp_path)
    row = bundle(version, actor)
    arguments = {"label": "menu"} | values
    with pytest.raises(DatabaseError), transaction.atomic():
        asset(row, **arguments)


@pytest.mark.parametrize(
    "values",
    [
        {"state": "ready"},
        {"actor_id": uuid4()},
        {"expires_at": timezone.now() + timedelta(days=2)},
    ],
)
def test_bundle_intake_is_bounded_and_attributed(tmp_path, values):
    """Create the receipt before file work, with at most a day of staging."""
    _, version, actor = initialized(tmp_path)
    with pytest.raises(DatabaseError), transaction.atomic():
        bundle(version, actor, **values)


def test_unknown_or_wrongly_owned_branding_never_applies(tmp_path):
    """A valid UUID string is not proof of a normalized ready logo bundle."""
    store, version, actor = initialized(tmp_path)
    values = {label: str(uuid4()) for label in ("large", "menu", "icon", "favicon")}
    receipt = change(store, version, actor, branding_patch(version, values))
    assert receipt.state == "failed" and store.active() == version
    _, values = ready(version, uuid4())
    receipt = change(store, version, actor, branding_patch(version, values))
    assert receipt.state == "failed" and store.active() == version


def test_asset_history_and_bundle_bindings_are_immutable(tmp_path):
    """Raw SQL cannot rewrite a digest or reassign an upload to another session."""
    _, version, actor = initialized(tmp_path)
    row = bundle(version, actor)
    asset(row, "large")
    for statement in (
        "UPDATE stewardship_branding_asset SET sha256=repeat('b',64)",
        "DELETE FROM stewardship_branding_asset",
        "UPDATE stewardship_branding_bundle "
        "SET session_id=gen_random_uuid(),version=version+1",
    ):
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)


def test_branding_migration_empty_roundtrip_and_populated_refusal(tmp_path):
    """Retained asset ownership cannot silently lose its database cleanup fence."""
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    previous = [("stewardship_accounts", "0054_brandingbundle_brandingasset_and_more")]
    try:
        MigrationExecutor(connection).migrate(previous)
        MigrationExecutor(connection).migrate(leaves)
        _, version, actor = initialized(tmp_path)
        ready(version, actor)
        with pytest.raises(DatabaseError):
            MigrationExecutor(connection).migrate(previous)
    finally:
        MigrationExecutor(connection).migrate(leaves)
