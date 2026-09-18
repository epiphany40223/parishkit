"""Journaled Testing cleanup owns weekly private retention, not runtime DELETE."""

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.cleanup_catalog import (
    CleanupCategory,
    inventory_queries,
    iter_inventory,
)
from parishkit.stewardship.campaigns.production_models import ProductionCleanupTarget
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.weekly_capture import capture_weekly_snapshot
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
    WeeklyDigestSnapshot,
)
from parishkit.stewardship.reports.weekly_ownership import checkpoint_preparation

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import delete_batch, running_request
from .test_weekly_capture_postgresql import INSTANT, prepare
from .test_weekly_coverage_postgresql import publish
from .test_weekly_dispatch_postgresql import allocated

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("completed", [False, True])
def test_testing_snapshot_cleanup_is_bounded_and_preserves_opaque_owner(
    response_service, completed
):
    """Empty Testing captures still contain private cohort/source observations."""
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            snapshot = capture_weekly_snapshot(claim)
        assert snapshot.information == [] and snapshot.corrections == []
        if completed:
            publish(claim)
        with task_login(ServiceRole.WEB, exact=True), work_transaction():
            queries = inventory_queries(response_service.campaign.pk)
            assert list(
                queries[CleanupCategory.WEEKLY_SNAPSHOT].values_list("pk", flat=True)
            ) == [snapshot.pk]
            assert not queries[CleanupCategory.WEEKLY_RECIPIENT].exists()
        with work_transaction():
            targets = {
                (item.category.value, item.identifier)
                for item in iter_inventory(response_service.campaign.pk)
            }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT category,target_id FROM stewardship_cleanup_inventory_v1(%s)",
                [response_service.campaign.pk],
            )
            assert targets == set(cursor.fetchall())
        assert ("weekly_digest_snapshots", snapshot.pk) in targets
        status = running_request(response_service)
        for _ in range(status.inventory_total * 4):
            if not ProductionCleanupTarget.objects.filter(
                request_id=status.request_id
            ).exists():
                break
            checkpoint = delete_batch(status, maximum=2)
            assert checkpoint.deleted_count <= 2
        assert not ProductionCleanupTarget.objects.filter(
            request_id=status.request_id
        ).exists()
        assert not WeeklyDigestSnapshot.objects.exists()
        assert not WeeklyDigestRecipient.objects.exists()
        owner = WeeklyDigestPreparation.objects.get(task_id=claim.run_id)
        if completed:
            assert owner.phase == "complete"
        else:
            with task_login(ServiceRole.WORKER, exact=True), work_transaction():
                assert (
                    checkpoint_preparation(claim, phase="cancelled").phase
                    == "cancelled"
                )


@pytest.mark.parametrize("target", ["snapshot", "recipient"])
def test_production_weekly_details_are_not_testing_cleanup_targets(
    live_response_service, target
):
    """Neither inventory selection nor schema-owner DELETE bypasses retention."""
    with campaign_clock(INSTANT):
        snapshot = allocated(live_response_service)
        recipient = WeeklyDigestRecipient.objects.get(snapshot=snapshot)
        with work_transaction():
            targets = {
                (item.category.value, item.identifier)
                for item in iter_inventory(live_response_service.campaign.pk)
            }
        assert ("weekly_digest_snapshots", snapshot.pk) not in targets
        assert ("weekly_digest_recipients", recipient.pk) not in targets
        table, identifier = {
            "snapshot": ("stewardship_weekly_digest_snapshot", snapshot.pk),
            "recipient": ("stewardship_weekly_digest_recipient", recipient.pk),
        }[target]
        with (
            pytest.raises(DatabaseError, match="immutable outside owned cleanup"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(f'DELETE FROM "{table}" WHERE id=%s', [identifier])
        assert WeeklyDigestSnapshot.objects.filter(pk=snapshot.pk).exists()
        assert WeeklyDigestRecipient.objects.filter(pk=recipient.pk).exists()
