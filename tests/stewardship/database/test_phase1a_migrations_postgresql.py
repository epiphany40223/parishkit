"""New history and active readers refuse lossy Phase 1A downgrades."""

from importlib import import_module

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor

from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.campaigns.read_guards import DOWNLOAD_NAMESPACE

from .campaign_builders import campaign_clock, command, draft_campaign, restored_runtime
from .test_campaign_review_guards_postgresql import inventory_values
from .test_exceptional_end_postgresql import complete_empty_catchup, end_request

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "history,target,message",
    [
        ("intent", "0009_boundary_catchup_guards", "Exceptional configuration history"),
        ("hold", "0013_control_guards", "Mail resolution history"),
        ("checkpoint", "0007_schedule_guards", "Catch-up completion history"),
    ],
)
def test_populated_phase1a_reverse_preserves_history(
    tmp_path, history, target, message
):
    """Actual downgrade refuses new ledgers; cleanup reapplies any preceding steps."""
    store, campaign, actor = draft_campaign(tmp_path)
    if history == "intent":
        with campaign_clock(campaign.active_configuration.starts_at):
            command(campaign, actor, Action.ACTIVATE)
            end_request(store, campaign, actor, "edit_end")
    elif history == "checkpoint":
        with campaign_clock(campaign.active_configuration.starts_at):
            command(campaign, actor, Action.ACTIVATE)
            complete_empty_catchup(campaign, actor)
    else:
        with restored_runtime(campaign.active_configuration.starts_at) as restore_id:
            RestoreDeliveryHold.objects.create(
                **inventory_values(campaign, actor, restore_id)
            )
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError, match=message):
            MigrationExecutor(connection).migrate([("stewardship_campaigns", target)])
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_capacity_delete_has_constraint_sqlstate():
    """Deletion is deliberately rejected, not an unassigned-record SQL error."""
    with (
        pytest.raises(IntegrityError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_download_policy")
    assert error.value.__cause__.sqlstate == "23514"


def test_capacity_reverse_refuses_an_independent_active_slot():
    """Run the exact reverse script atomically while another session owns capacity."""
    migration = import_module(
        "parishkit.stewardship.campaigns.migrations.0004_read_guard_capacity"
    ).Migration
    other = connections["default"].copy(alias="capacity-reverse-probe")
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s,0)", [DOWNLOAD_NAMESPACE])
        from django.db import ProgrammingError

        with (
            pytest.raises(
                ProgrammingError, match="Active downloads prevent schema downgrade"
            ),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(migration.operations[0].reverse_sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT capacity FROM stewardship_download_policy")
            assert cursor.fetchone()[0] == 4
    finally:
        other.close()


def test_capacity_resize_rejects_the_same_backend_owning_a_slot():
    """Advisory locks are reentrant, so explicitly reject self-owned downloads."""
    other = connections["default"].copy(alias="capacity-owner-probe")
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s,0)", [DOWNLOAD_NAMESPACE])
            with pytest.raises(IntegrityError, match="download-owning session"):
                cursor.execute(
                    "UPDATE stewardship_download_policy "
                    "SET capacity=3, version=version+1"
                )
        with connection.cursor() as cursor:
            cursor.execute("SELECT capacity FROM stewardship_download_policy")
            assert cursor.fetchone()[0] == 4
    finally:
        other.close()
