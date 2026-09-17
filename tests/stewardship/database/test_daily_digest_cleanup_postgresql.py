"""Testing reports leave no private retained inputs after journaled cleanup."""

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.cleanup_catalog import iter_inventory
from parishkit.stewardship.campaigns.production_models import ProductionCleanupTarget
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.digest_building import retain_daily_content
from parishkit.stewardship.reports.digest_capture import capture_daily_snapshot
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestReady,
    DailyDigestSnapshot,
)
from parishkit.stewardship.reports.digest_ownership import checkpoint_preparation
from parishkit.stewardship.reports.models import CampaignDailyFactSet, CampaignFactPin
from parishkit.stewardship.source.models import SourceSnapshotPin

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import delete_batch, running_request
from .test_daily_digest_building_postgresql import build
from .test_daily_digest_capture_postgresql import prepare
from .test_daily_digest_planning_postgresql import INSTANT

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("rendered", [False, True])
def test_cleanup_removes_digest_inputs_in_two_row_units(response_service, rendered):
    """Independent inventories agree; deletion preserves facts and stale metadata."""
    with campaign_clock(INSTANT):
        if rendered:
            claim, document, content = build(response_service)
            with task_login(ServiceRole.WORKER, exact=True), work_transaction():
                ready = retain_daily_content(claim, document, content)
            snapshot_id = ready.snapshot_id
        else:
            claim = prepare(response_service)
            with task_login(ServiceRole.WORKER, exact=True), work_transaction():
                snapshot_id = capture_daily_snapshot(claim).pk
        facts = set(CampaignDailyFactSet.objects.values_list("pk", flat=True))
        with work_transaction(), connection.cursor() as cursor:
            targets = {
                (item.category.value, item.identifier)
                for item in iter_inventory(response_service.campaign.pk)
            }
            cursor.execute(
                "SELECT category,target_id FROM stewardship_cleanup_inventory_v1(%s)",
                [response_service.campaign.pk],
            )
            assert targets == set(cursor.fetchall())
        assert ("daily_digest_snapshots", snapshot_id) in targets
        assert (
            any(category == "daily_digest_ready" for category, _ in targets) == rendered
        )
        status = running_request(response_service)
        # Several dependency sweeps are allowed, but a broken selector must fail
        # quickly rather than turn into an indefinitely hanging CI test.
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
        assert not DailyDigestSnapshot.objects.exists()
        assert not DailyDigestReady.objects.exists()
        assert not SourceSnapshotPin.objects.filter(
            parent_kind="digest", parent_id=snapshot_id
        ).exists()
        assert not CampaignFactPin.objects.filter(
            parent_kind="digest", parent_id=snapshot_id
        ).exists()
        assert set(CampaignDailyFactSet.objects.values_list("pk", flat=True)) == facts
        assert DailyDigestPreparation.objects.filter(task_id=claim.run_id).exists()
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert checkpoint_preparation(claim, phase="cancelled").phase == "cancelled"


@pytest.mark.parametrize("target", ["snapshot", "ready", "source_pin", "fact_pin"])
def test_retained_digest_cannot_be_removed_outside_cleanup(response_service, target):
    """Even schema-owner SQL cannot bypass private checkpoint deletion evidence."""
    with campaign_clock(INSTANT):
        claim, document, content = build(response_service)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            ready = retain_daily_content(claim, document, content)
        table, identifier = {
            "snapshot": ("stewardship_daily_digest_snapshot", ready.snapshot_id),
            "ready": ("stewardship_daily_digest_ready", ready.pk),
            "source_pin": (
                "stewardship_source_pin",
                SourceSnapshotPin.objects.get(
                    parent_kind="digest", parent_id=ready.snapshot_id
                ).pk,
            ),
            "fact_pin": (
                "stewardship_fact_pin",
                CampaignFactPin.objects.get(
                    parent_kind="digest", parent_id=ready.snapshot_id
                ).pk,
            ),
        }[target]
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(f'DELETE FROM "{table}" WHERE id=%s', [identifier])
        assert DailyDigestReady.objects.filter(pk=ready.pk).exists()
