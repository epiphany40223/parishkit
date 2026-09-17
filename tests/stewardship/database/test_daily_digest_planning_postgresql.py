"""Real bounded daily ownership and exact-role denial, without provider calls."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import TaskClaim, TaskOwnershipLost
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import change_run
from parishkit.stewardship.reports import digest_planning
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestReady,
    DailyDigestSnapshot,
)
from parishkit.stewardship.reports.digest_ownership import (
    DailyDigestProducer,
    checkpoint_preparation,
)
from parishkit.stewardship.reports.digest_planning import cover_dates, discover_dates

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_digest_schedule_planning_postgresql import add_digest
from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
INSTANT = datetime(2026, 10, 10, tzinfo=UTC)


def allocate(*, claim_task=True):
    """Use actual scheduler authority, with only the first original slot present."""
    producer = DailyDigestProducer(uuid4())
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        DigestScheduleProducer(uuid4(), limit=1)(guard)
        (status,) = producer(guard)
        assert producer(guard) == ()
    if not claim_task:
        return status
    with work_transaction():
        status = change_run(
            run_id=status.run_id,
            action="claim",
            expected_version=status.version,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=lambda *args: True,
            lease_seconds=300,
        )
    return TaskClaim(status.run_id, status.fence, status.worker_id)


def test_complete_bounded_coverage_precedes_snapshot_or_mail(
    family_service,  # noqa: F811
    auth_service,
    monkeypatch,
):
    """No two-date page may accidentally become a complete recovery report."""
    identifier = add_digest(auth_service.store, family_service.campaign)
    monkeypatch.setattr(digest_planning, "LIMIT", 2)
    with campaign_clock(INSTANT):
        claim = allocate()
        with task_login(ServiceRole.WORKER, exact=True):
            row = DailyDigestPreparation.objects.get(task_id=claim.run_id)
            date_pages, cover_pages = 0, 0
            while row.phase == "dates":
                before = ScheduleOccurrence.objects.filter(
                    definition_id=identifier
                ).count()
                with work_transaction():
                    row = discover_dates(claim)
                after = ScheduleOccurrence.objects.filter(
                    definition_id=identifier
                ).count()
                assert 0 <= after - before <= 2
                date_pages += 1
                assert date_pages <= 10, (
                    "Discovery failed to advance its finite cursor."
                )
                assert row.occurrence_id is None
                assert not DailyDigestSnapshot.objects.exists()
                assert not DailyDigestReady.objects.exists()
                assert not OutboxMessage.objects.exists()
            while row.phase == "cover":
                before = ScheduleOccurrence.objects.filter(state="coalesced").count()
                with work_transaction():
                    row = cover_dates(claim)
                after = ScheduleOccurrence.objects.filter(state="coalesced").count()
                assert 0 <= after - before <= 2
                cover_pages += 1
                assert cover_pages <= 10, "Coverage failed to consume pending rows."
        assert row.phase == "facts" and date_pages > 1 and cover_pages > 1
        original = list(
            ScheduleOccurrence.objects.filter(definition_id=identifier).order_by("slot")
        )
        assert len(original) == 8
        assert all(item.state == "coalesced" for item in original[:-1])
        assert original[-1].pk == row.occurrence_id
        assert [
            day.isoformat() for day in covered_dates(row.occurrence_id, mode=row.mode)
        ] == [item.slot for item in original]
        # This fixture is Testing: the default reader cannot mix its dates into
        # production obligations even though both namespaces use the same dates.
        assert row.mode == "testing" and covered_dates(row.occurrence_id) == ()
        assert not OutboxMessage.objects.exists()


def test_daily_page_rejects_stale_claim_and_raw_phase_jump(
    family_service,  # noqa: F811
    auth_service,
):
    add_digest(auth_service.store, family_service.campaign)
    with campaign_clock(INSTANT):
        claim = allocate()
        with work_transaction(), pytest.raises(TaskOwnershipLost):
            discover_dates(replace(claim, fence=claim.fence + 1))
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            row = DailyDigestPreparation.objects.get(task_id=claim.run_id)
            with pytest.raises(DatabaseError), transaction.atomic():
                DailyDigestPreparation.objects.filter(pk=row.pk).update(
                    phase="fanout",
                    version=row.version + 1,
                    run_id=claim.run_id,
                    task_fence=claim.fence,
                    worker_id=claim.worker_id,
                    actor_id=claim.worker_id,
                )
            cancelled = checkpoint_preparation(claim, phase="cancelled")
            assert cancelled.phase == "cancelled"
            with pytest.raises(PermissionError):
                discover_dates(claim)


@pytest.mark.parametrize(
    "table,column",
    [
        ("stewardship_daily_digest_snapshot", "statistics_inputs"),
        ("stewardship_daily_digest_snapshot", "covered_dates"),
        ("stewardship_daily_digest_ready", "html"),
        ("stewardship_daily_digest_ready", "chart"),
        ("stewardship_daily_digest_recipient", "address"),
    ],
)
def test_scheduler_cannot_read_private_digest_values(table, column):
    """An empty table is still private: grants, not row presence, enforce this."""
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f'SELECT "{column}" FROM "{table}"')


def test_daily_producer_rejects_unowned_calls():
    with pytest.raises(ValueError):
        DailyDigestProducer("not-a-worker")
    with pytest.raises(TypeError):
        DailyDigestProducer(uuid4())(object())
