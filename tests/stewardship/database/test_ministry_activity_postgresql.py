"""Real configuration requests, retained overrides and PostgreSQL forgery checks."""

from datetime import timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
    MinistryActivity,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)
from parishkit.stewardship.accounts.ministry_activity import (
    MinistryReconciliationUnavailable,
)
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action

from ..configuration_factory import configuration_version, successor_document
from ..policy_factory import address, assignment
from ..test_ministry_activity import activity, policy_document
from .campaign_builders import (
    add_draft,
    campaign_clock,
    change,
    command,
    draft_campaign,
    initialized,
)
from .test_recovery_postgresql import arguments

pytestmark = pytest.mark.django_db(transaction=True)


def prepare(version):
    """Exercise complete durable preparation, not test-transaction savepoints."""
    return prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())


def test_ordinary_request_applies_activity_and_preserves_campaign(tmp_path):
    """New schema retains policy, campaign and schedule projections at activation."""
    store, root, actor = initialized(tmp_path)
    result, campaign, schedule = add_draft(store, root, actor)
    assert result.state == "applied"
    row = activity()
    result = change(
        store,
        store.active(),
        actor,
        [{"operation": "add", "section": "ministries", **row}],
    )
    assert result.state == "applied"
    runtime = SystemConfiguration.objects.get()
    snapshot = runtime.active_configuration
    assert snapshot.validation_schema == "ministry-activity-v4"
    assert is_prepared(snapshot.digest)
    assert snapshot.ministry_activity.get().active is False
    assert str(snapshot.campaign_configurations.get().record_id) == campaign["id"]
    assert str(snapshot.schedule_revisions.get().record_id) == schedule["id"]
    result = change(
        store,
        store.active(),
        actor,
        [
            {
                "operation": "update",
                "section": "ministries",
                "id": row["id"],
                "values": {"active": True},
            }
        ],
    )
    assert result.state == "applied"
    assert snapshot.ministry_activity.get().active is False
    assert (
        SystemConfiguration.objects.get()
        .active_configuration.ministry_activity.get()
        .active
        is True
    )


def test_removal_retains_historical_binding_and_exact_readdition(tmp_path):
    """Removing an override restores the default, not permission to reuse its ID."""
    store, root, actor = initialized(tmp_path)
    row = activity()
    assert (
        change(
            store, root, actor, [{"operation": "add", "section": "ministries", **row}]
        ).state
        == "applied"
    )
    historical = store.active()
    assert (
        change(
            store,
            historical,
            actor,
            [{"operation": "remove", "section": "ministries", "id": row["id"]}],
        ).state
        == "applied"
    )
    assert is_prepared(store.active().digest)
    assert MinistryActivity.objects.count() == 1
    for replacement in (
        activity(),
        {"id": row["id"], "values": activity(ministry_duid=2)["values"]},
    ):
        with pytest.raises(ConfigError, match="stable"):
            record_request(
                base_digest=store.active().digest,
                patch=[{"operation": "add", "section": "ministries", **replacement}],
                actor_id=actor,
                request_key=uuid4(),
                correlation_id=uuid4(),
            )
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "ministries", **row}],
        ).state
        == "applied"
    )
    assert is_prepared(store.active().digest)


def test_same_duid_in_another_tenant_is_a_separate_override():
    """Tenant switches and retained old mappings cannot collide by Ministry number."""
    version = configuration_version(
        policy_document(activity(), activity(organization_id=2))
    )
    snapshot = prepare(version)
    assert snapshot.ministry_activity.count() == 2
    assert is_prepared(version.digest)


@pytest.mark.parametrize("action", ["update", "delete"])
def test_sql_cannot_mutate_history(action):
    """Bypassing model save still cannot alter or delete retained local decisions."""
    snapshot = prepare(configuration_version(policy_document(activity())))
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_ministry_activity SET active=true "
            "WHERE configuration_id=%s"
            if action == "update"
            else "DELETE FROM stewardship_ministry_activity WHERE configuration_id=%s",
            [snapshot.pk],
        )


def test_missing_projection_rolls_back_entire_preparation(monkeypatch):
    """Deferred completeness refuses a candidate even if Python omitted its rows."""
    version = configuration_version(policy_document(activity()))
    monkeypatch.setattr(MinistryActivity.objects, "create", lambda **kwargs: None)
    with pytest.raises(IntegrityError, match="incomplete"):
        prepare(version)
    assert not AppliedConfigurationVersion.objects.exists()


def test_projection_must_exactly_match_yaml(monkeypatch):
    """Direct insertion cannot invert an Admin's activity choice."""
    create = MinistryActivity.objects.create

    def forged(**kwargs):
        """Corrupt only the database projection, leaving valid canonical input."""
        return create(**(kwargs | {"active": not kwargs["active"]}))

    monkeypatch.setattr(MinistryActivity.objects, "create", forged)
    with pytest.raises(IntegrityError, match="differs"):
        prepare(configuration_version(policy_document(activity())))
    assert not AppliedConfigurationVersion.objects.exists()


def test_preparation_rejects_retired_identity_without_intake():
    """Offline candidates also verify identity across a removed-record ancestor."""
    row = activity()
    root = configuration_version(policy_document(row))
    prepare(root)
    document = successor_document(root)
    document["sections"]["ministries"] = []
    removed = configuration_version(document)
    prepare(removed)
    document = successor_document(removed)
    document["sections"]["ministries"] = [activity()]
    with pytest.raises(ConfigError, match="predecessor"):
        prepare(configuration_version(document))


@pytest.mark.parametrize("reused_binding", ["record", "pair"])
def test_sql_preserves_binding_even_if_ancestry_preflight_is_bypassed(
    monkeypatch, reused_binding
):
    """The SQL trigger rejects rebinding even without Python ancestry validation."""
    row = activity()
    root = configuration_version(policy_document(row))
    prepare(root)
    document = successor_document(root)
    document["sections"]["ministries"] = [
        {"id": row["id"], "values": activity(ministry_duid=2)["values"]}
        if reused_binding == "record"
        else activity()
    ]
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.configuration_snapshots._verify_history",
        lambda *args, **kwargs: True,
    )
    with pytest.raises(IntegrityError, match="stable"):
        prepare(configuration_version(document))
    assert AppliedConfigurationVersion.objects.count() == 1


def test_populated_downgrade_preserves_guards():
    """A downgrade fails atomically before weakening retained configuration history."""
    prepare(configuration_version(policy_document(activity())))
    migration = import_module(
        "parishkit.stewardship.accounts.migrations.0039_ministry_activity_guards"
    )
    with (
        pytest.raises(IntegrityError, match="downgrade"),
        connection.schema_editor() as editor,
    ):
        migration.backward(None, editor)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE stewardship_ministry_activity SET active=true")


def test_operator_recovery_preserves_applied_activity_and_replays(tmp_path):
    """Actual offline recovery uses the new discriminator and unchanged authority."""
    store, root, actor = initialized(tmp_path)
    row = activity()
    assert (
        change(
            store, root, actor, [{"operation": "add", "section": "ministries", **row}]
        ).state
        == "applied"
    )
    options = arguments()
    result = recover_admin(store, **options)
    assert result.state == "applied"
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert request.request_schema == "operator-recovery-ministry-v4"
    assert store.active().document()["sections"]["ministries"] == [row]
    assert is_prepared(store.active().digest)
    assert recover_admin(store, **options) == result


@pytest.mark.parametrize("bypass_preflight", [False, True])
def test_activity_cannot_apply_without_seeded_assignment_effects(
    tmp_path, monkeypatch, bypass_preflight
):
    """Storage alone never activates an activity change with stale seeded scope."""
    store, root, actor = initialized(tmp_path, [address(), assignment(seeded=True)])
    if bypass_preflight:
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.ministry_activity.validate_installation",
            lambda document: None,
        )
    error = IntegrityError if bypass_preflight else MinistryReconciliationUnavailable
    with pytest.raises(error, match="reconciliation"):
        change(
            store,
            root,
            actor,
            [
                {
                    "operation": "add",
                    "section": "ministries",
                    **activity(ministry_duid=123),
                }
            ],
        )
    assert SystemConfiguration.objects.get().active_configuration_id == root.version_id
    if not bypass_preflight:
        assert store.active() == root
        assert (
            ConfigurationChangeRequest.objects.get()
            .checkpoints.latest("sequence")
            .state
            == "staged"
        )


def test_manual_assignments_do_not_block_local_activity(tmp_path):
    """Local hiding does not remove manual Ministry-leader administrative scope."""
    manual = assignment(ministry=1)
    store, root, actor = initialized(tmp_path, [address(), manual])
    assert (
        change(
            store,
            root,
            actor,
            [{"operation": "add", "section": "ministries", **activity()}],
        ).state
        == "applied"
    )
    snapshot = SystemConfiguration.objects.get().active_configuration
    assert str(snapshot.ministryassignment_set.get().record_id) == manual["id"]


def test_activity_change_preserves_locked_campaign_selection(tmp_path):
    """A local visibility edit is not an edit to the campaign's structural selection."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
        campaign.refresh_from_db()
        assert campaign.structural_locked and campaign.state == "scheduled"
        before = campaign.active_configuration.values
        assert (
            change(
                store,
                store.active(),
                actor,
                [{"operation": "add", "section": "ministries", **activity()}],
            ).state
            == "applied"
        )
    campaign.refresh_from_db()
    assert campaign.active_configuration.values == before
    assert campaign.structural_locked and campaign.state == "scheduled"
