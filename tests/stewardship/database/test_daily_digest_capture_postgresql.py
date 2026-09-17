"""Exact retained digest observations and source pins commit as one guarded effect."""

from unittest.mock import patch

import pytest
from django.db import DatabaseError

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.digest_capture import (
    capture_daily_snapshot,
    retained_dates,
    retained_statistics,
)
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestSnapshot,
)
from parishkit.stewardship.reports.digest_planning import cover_dates, discover_dates
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

from .campaign_builders import campaign_clock
from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_daily_digest_planning_postgresql import INSTANT, allocate
from .test_digest_schedule_planning_postgresql import add_digest
from .test_recipient_suppressions_postgresql import refresh

pytestmark = pytest.mark.django_db(transaction=True)


def prepare(harness, *, configure=None):
    """Finish every discovery/coverage page through the real metadata services."""
    add_digest(harness.service.store, harness.campaign)
    if configure is not None:
        configure()
    claim = allocate()
    with task_login(ServiceRole.WORKER, exact=True):
        row = DailyDigestPreparation.objects.get(task_id=claim.run_id)
        for _ in range(10):
            if row.phase not in {"dates", "cover"}:
                break
            with work_transaction():
                row = (discover_dates if row.phase == "dates" else cover_dates)(claim)
    assert row.phase == "facts"
    return claim


def test_snapshot_and_pin_are_retained_across_source_promotion(response_service):
    harness = response_service
    with campaign_clock(INSTANT):
        claim = prepare(harness)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            snapshot = capture_daily_snapshot(claim)
            statistics = retained_statistics(snapshot)
            dates = retained_dates(snapshot)
        assert statistics.active.families == 1
        assert statistics.submission_watermark == 0
        assert snapshot.source_id == harness.snapshot.pk
        assert SourceSnapshotPin.objects.filter(
            snapshot_id=snapshot.source_id,
            parent_kind="digest",
            parent_id=snapshot.pk,
            expires_at__isnull=True,
        ).exists()
        newer = response_source()
        newer.members[3]["emailAddress"] = "changed@example.org"
        refresh(harness, newer)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            replay = capture_daily_snapshot(claim)
            assert replay.pk == snapshot.pk
            assert replay.statistics_inputs == snapshot.statistics_inputs
            assert retained_statistics(replay) == statistics
            assert retained_dates(replay) == dates
        assert DailyDigestSnapshot.objects.count() == 1


def test_missing_source_pin_rolls_back_the_entire_capture(response_service):
    with campaign_clock(INSTANT):
        claim = prepare(response_service)
        with (
            patch("parishkit.stewardship.reports.digest_capture.pin_snapshot"),
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError, match="retained source protection"),
            work_transaction(),
        ):
            capture_daily_snapshot(claim)
        assert not DailyDigestSnapshot.objects.exists()
        assert not SourceSnapshotPin.objects.filter(parent_kind="digest").exists()
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert capture_daily_snapshot(claim).pk is not None
