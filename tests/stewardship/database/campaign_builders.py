"""Shared Phase 1A database-backed campaign scenarios, never operational permits."""

from contextlib import contextmanager
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime import transition_campaign

from .test_campaign_postgresql import add_draft
from .test_policy_postgresql import initialized


def admit_test_work(*args):
    """Isolated storage fixture; deliberately cannot establish external readiness."""


def draft_campaign(tmp_path, row=None):
    """Create through the real installer and return the persisted current draft."""
    store, root, actor = initialized(tmp_path)
    result, row, _ = add_draft(store, root, actor, row)
    assert result.state == "applied"
    return store, Campaign.objects.get(pk=UUID(row["id"])), actor


@contextmanager
def campaign_clock(instant):
    """Replace only the domain test clock, never TaskRun's genuine lease clock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_campaign_now_v1()'::regprocedure)"
        )
        original = cursor.fetchone()[0]
        cursor.execute(
            "CREATE OR REPLACE FUNCTION stewardship_campaign_now_v1() "
            "RETURNS timestamptz "
            "LANGUAGE sql STABLE AS %s",
            ["SELECT '" + instant.isoformat() + "'::timestamptz"],
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(original)


def command(campaign, actor, action, **kwargs):
    """Fresh expected versions plus synthetic owning admission for storage tests."""
    campaign.refresh_from_db()
    runtime = SystemConfiguration.objects.get()
    return transition_campaign(
        campaign_id=campaign.pk,
        action=action,
        request_id=uuid4(),
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        **({"token_generation_id": uuid4()} if action is Action.ACTIVATE else {}),
        **kwargs,
    )


@contextmanager
def restored_runtime(backup_at):
    """Emulate offline restore input, not an application writer or release API.

    OPS-06 owns the future journalled restore operation. These fixtures require
    the disposable database's schema-owner privileges to load its otherwise
    frozen gate fields, with user triggers disabled only inside each fixture
    transaction. All application operations execute with every guard enabled.
    """
    restore_id = uuid4()
    columns = (
        "restore_review_required",
        "restore_id",
        "restore_backup_at",
        "restore_activated_at",
        "restore_released_at",
    )
    saved = SystemConfiguration.objects.values_list(*columns).get()

    def load(values):
        """Install synthetic backup state atomically and immediately restore guards."""
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_system_configuration DISABLE TRIGGER USER"
            )
            cursor.execute(
                "UPDATE stewardship_system_configuration "
                "SET restore_review_required=%s, restore_id=%s, restore_backup_at=%s, "
                "restore_activated_at=%s, restore_released_at=%s",
                values,
            )
            cursor.execute(
                "ALTER TABLE stewardship_system_configuration ENABLE TRIGGER USER"
            )

    load((True, restore_id, backup_at, backup_at, None))
    try:
        yield restore_id
    finally:
        load(saved)
